"""Orchestrator API — central management service for 9Bot cloud hosting.

Runs on the relay server (1453.life). Manages VMs via Hetzner Cloud API,
proxies commands to VM agents, and handles user assignment.

Usage:
    python -m server.orchestrator
    python -m server.orchestrator --port 9091 --db /path/to/orchestrator.db

Environment variables:
    ORCHESTRATOR_SECRET   -- Admin API key (required)
    HETZNER_API_TOKEN     -- Hetzner Cloud API token (for VM provisioning)

Endpoints (all require admin API key):
    GET    /vms                     List VMs
    POST   /vms                     Provision new VM
    DELETE /vms/{id}                Destroy VM
    GET    /instances               List all instances
    POST   /instances/{id}/start    Start instance (proxies to VM agent)
    POST   /instances/{id}/stop     Stop instance
    GET    /users                   List users
    POST   /users                   Create + assign user
    DELETE /users/{id}              Unassign + delete user
    GET    /dashboard               Admin web UI
    GET    /stats                   Aggregate stats
"""

import os
import sys
import json
import time
import threading
from functools import wraps

import requests as http_client
from flask import Flask, jsonify, request, abort, render_template_string

from server.models import (
    init_db, create_vm, get_vm, list_vms, update_vm, delete_vm,
    create_instance, get_instance, list_instances, update_instance,
    find_available_instance, create_user, get_user, list_users,
    assign_user, unassign_user, delete_user, get_stats,
)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _get_admin_secret():
    return os.environ.get("ORCHESTRATOR_SECRET", "")


def require_admin(f):
    """Reject requests without valid admin API key."""
    @wraps(f)
    def decorated(*args, **kwargs):
        secret = _get_admin_secret()
        if not secret:
            abort(403, description="ORCHESTRATOR_SECRET not configured")
        # Accept via header or query param
        token = (request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                 or request.args.get("key", ""))
        if token != secret:
            abort(401, description="Invalid admin key")
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# VM Agent proxy
# ---------------------------------------------------------------------------

def _agent_request(vm, method, path, timeout=30):
    """Send an authenticated request to a VM's agent.

    Returns (status_code, json_body) or (None, error_string).
    """
    agent_secret = os.environ.get("NINEBOT_AGENT_SECRET", "")
    url = f"http://{vm['ip']}:{vm['agent_port']}{path}"
    try:
        resp = http_client.request(
            method, url,
            headers={"Authorization": f"Bearer {agent_secret}"},
            timeout=timeout,
        )
        return resp.status_code, resp.json()
    except http_client.Timeout:
        return None, "Agent request timed out"
    except http_client.ConnectionError:
        return None, "Agent unreachable"
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Hetzner Cloud API
# ---------------------------------------------------------------------------

_HETZNER_API = "https://api.hetzner.cloud/v1"


def _hetzner_headers():
    token = os.environ.get("HETZNER_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _hetzner_create_server(name, server_type="cpx41", image="windows-2022",
                           location="fsn1"):
    """Create a Hetzner Cloud server. Returns (server_id, ip) or raises."""
    resp = http_client.post(
        f"{_HETZNER_API}/servers",
        headers=_hetzner_headers(),
        json={
            "name": name,
            "server_type": server_type,
            "image": image,
            "location": location,
            "automount": False,
            "start_after_create": True,
        },
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Hetzner API error: {resp.status_code} {resp.text}")
    data = resp.json()
    server = data["server"]
    ip = server["public_net"]["ipv4"]["ip"]
    return str(server["id"]), ip


def _hetzner_delete_server(server_id):
    """Delete a Hetzner Cloud server."""
    resp = http_client.delete(
        f"{_HETZNER_API}/servers/{server_id}",
        headers=_hetzner_headers(),
        timeout=30,
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"Hetzner API error: {resp.status_code} {resp.text}")


# ---------------------------------------------------------------------------
# Health monitoring
# ---------------------------------------------------------------------------

_health_thread = None
_health_stop = threading.Event()


def _health_monitor_loop():
    """Poll all VMs every 60s, update status on failure."""
    consecutive_fails = {}  # vm_id -> int

    while not _health_stop.is_set():
        _health_stop.wait(60)
        if _health_stop.is_set():
            break
        for vm in list_vms():
            if vm["status"] == "offline":
                continue
            status_code, body = _agent_request(vm, "GET", "/health", timeout=10)
            if status_code == 200:
                consecutive_fails[vm["id"]] = 0
                if vm["status"] != "ready" and vm["status"] != "full":
                    update_vm(vm["id"], status="ready")
            else:
                fails = consecutive_fails.get(vm["id"], 0) + 1
                consecutive_fails[vm["id"]] = fails
                if fails >= 3:
                    update_vm(vm["id"], status="offline")
                    # TODO: webhook alert to Discord


def _start_health_monitor():
    global _health_thread
    if _health_thread is not None and _health_thread.is_alive():
        return
    _health_stop.clear()
    _health_thread = threading.Thread(target=_health_monitor_loop,
                                      daemon=True, name="health-monitor")
    _health_thread.start()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app(db_path=None):
    init_db(db_path)
    app = Flask(__name__)

    # --- VMs ---

    @app.route("/vms")
    @require_admin
    def api_list_vms():
        vms = list_vms()
        # Enrich with instance counts
        for vm in vms:
            instances = list_instances(vm["id"])
            vm["instances"] = len(instances)
            vm["instances_running"] = sum(
                1 for i in instances if i["status"] == "running")
            vm["instances_assigned"] = sum(
                1 for i in instances if i["assigned_user"])
        return jsonify(vms)

    @app.route("/vms", methods=["POST"])
    @require_admin
    def api_create_vm():
        """Provision a new Hetzner VM and register it."""
        data = request.get_json(force=True) if request.is_json else {}
        name = data.get("name", f"9bot-vm-{int(time.time())}")
        server_type = data.get("server_type", "cpx41")
        location = data.get("location", "fsn1")

        if not os.environ.get("HETZNER_API_TOKEN"):
            abort(400, description="HETZNER_API_TOKEN not set")

        try:
            server_id, ip = _hetzner_create_server(
                name, server_type=server_type, location=location)
        except Exception as e:
            abort(502, description=f"Hetzner API failed: {e}")

        vm = create_vm(server_id, ip, name=name)

        # Pre-create instance slots (4 per VM by default)
        capacity = data.get("capacity", 4)
        base_port = 8081
        adb_base = 5555
        for i in range(capacity):
            inst_id = f"{server_id}-inst{i+1}"
            create_instance(
                inst_id, server_id,
                name=f"Instance{i+1}",
                port=base_port + i,
                adb_device=f"127.0.0.1:{adb_base + i * 2}",
            )

        return jsonify(vm), 201

    @app.route("/vms/<vm_id>", methods=["DELETE"])
    @require_admin
    def api_delete_vm(vm_id):
        """Destroy a VM (must have no assigned users)."""
        vm = get_vm(vm_id)
        if not vm:
            abort(404, description="VM not found")

        # Check for assigned users
        instances = list_instances(vm_id)
        assigned = [i for i in instances if i.get("assigned_user")]
        if assigned:
            abort(409, description=f"VM has {len(assigned)} assigned users — "
                                   "unassign them first")

        # Destroy on Hetzner
        if os.environ.get("HETZNER_API_TOKEN"):
            try:
                _hetzner_delete_server(vm_id)
            except Exception as e:
                # Log but continue with local deletion
                pass

        delete_vm(vm_id)
        return jsonify({"status": "deleted", "vm_id": vm_id})

    # --- Instances ---

    @app.route("/instances")
    @require_admin
    def api_list_instances():
        vm_id = request.args.get("vm_id")
        return jsonify(list_instances(vm_id))

    @app.route("/instances/<instance_id>/start", methods=["POST"])
    @require_admin
    def api_start_instance(instance_id):
        inst = get_instance(instance_id)
        if not inst:
            abort(404, description="Instance not found")
        vm = get_vm(inst["vm_id"])
        if not vm:
            abort(404, description="VM not found")

        status_code, body = _agent_request(
            vm, "POST", f"/instances/{inst['name']}/start")
        if status_code is None:
            abort(502, description=body)

        update_instance(instance_id, status="starting")
        return jsonify(body), status_code

    @app.route("/instances/<instance_id>/stop", methods=["POST"])
    @require_admin
    def api_stop_instance(instance_id):
        inst = get_instance(instance_id)
        if not inst:
            abort(404, description="Instance not found")
        vm = get_vm(inst["vm_id"])
        if not vm:
            abort(404, description="VM not found")

        status_code, body = _agent_request(
            vm, "POST", f"/instances/{inst['name']}/stop")
        if status_code is None:
            abort(502, description=body)

        update_instance(instance_id, status="stopped")
        return jsonify(body), status_code

    # --- Users ---

    @app.route("/users")
    @require_admin
    def api_list_users():
        return jsonify(list_users())

    @app.route("/users", methods=["POST"])
    @require_admin
    def api_create_user():
        """Create a user and optionally assign to an available instance."""
        data = request.get_json(force=True) if request.is_json else {}
        user_id = data.get("id") or data.get("username")
        if not user_id:
            abort(400, description="'id' or 'username' required")

        if get_user(user_id):
            abort(409, description=f"User '{user_id}' already exists")

        user = create_user(user_id, notes=data.get("notes", ""))

        # Auto-assign to available instance if requested
        if data.get("auto_assign", True):
            inst = find_available_instance()
            if inst:
                assign_user(user_id, inst["id"])
                user = get_user(user_id)
                user["assigned_instance"] = inst["id"]

        return jsonify(user), 201

    @app.route("/users/<user_id>", methods=["DELETE"])
    @require_admin
    def api_delete_user(user_id):
        user = get_user(user_id)
        if not user:
            abort(404, description="User not found")
        delete_user(user_id)
        return jsonify({"status": "deleted", "user_id": user_id})

    @app.route("/users/<user_id>/assign", methods=["POST"])
    @require_admin
    def api_assign_user(user_id):
        """Manually assign a user to a specific instance."""
        user = get_user(user_id)
        if not user:
            abort(404, description="User not found")
        data = request.get_json(force=True) if request.is_json else {}
        instance_id = data.get("instance_id")
        if not instance_id:
            abort(400, description="'instance_id' required")
        inst = get_instance(instance_id)
        if not inst:
            abort(404, description="Instance not found")
        if inst["assigned_user"] and inst["assigned_user"] != user_id:
            abort(409, description=f"Instance already assigned to {inst['assigned_user']}")

        assign_user(user_id, instance_id)
        return jsonify(get_user(user_id))

    @app.route("/users/<user_id>/unassign", methods=["POST"])
    @require_admin
    def api_unassign_user(user_id):
        user = get_user(user_id)
        if not user:
            abort(404, description="User not found")
        unassign_user(user_id)
        return jsonify(get_user(user_id))

    # --- Stats / Dashboard ---

    @app.route("/stats")
    @require_admin
    def api_stats():
        return jsonify(get_stats())

    @app.route("/dashboard")
    @require_admin
    def admin_dashboard():
        stats = get_stats()
        vms = list_vms()
        instances = list_instances()
        users = list_users()
        return render_template_string(
            _DASHBOARD_HTML,
            stats=stats, vms=vms, instances=instances, users=users,
        )

    return app


# ---------------------------------------------------------------------------
# Admin dashboard HTML
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>9Bot Cloud Admin</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
         background: #0a0a0f; color: #e0e0e0; padding: 20px; }
  h1 { color: #00d4ff; margin-bottom: 20px; }
  h2 { color: #9090a0; margin: 20px 0 10px; font-size: 14px;
       text-transform: uppercase; letter-spacing: 1px; }
  .stats { display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px; }
  .stat { background: #16161e; border: 1px solid #2a2a3a; border-radius: 8px;
          padding: 15px 20px; min-width: 120px; }
  .stat .value { font-size: 28px; font-weight: bold; color: #00d4ff; }
  .stat .label { font-size: 12px; color: #8888a0; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #1a1a2e; }
  th { color: #6060a0; font-size: 12px; text-transform: uppercase; }
  .status-running { color: #00ff88; }
  .status-stopped { color: #666; }
  .status-error { color: #ff4444; }
  .status-offline { color: #ff8800; }
</style>
</head>
<body>
<h1>9Bot Cloud Admin</h1>

<div class="stats">
  <div class="stat"><div class="value">{{ stats.vms }}</div><div class="label">VMs</div></div>
  <div class="stat"><div class="value">{{ stats.instances_running }}/{{ stats.instances_total }}</div><div class="label">Instances Running</div></div>
  <div class="stat"><div class="value">{{ stats.instances_assigned }}</div><div class="label">Assigned</div></div>
  <div class="stat"><div class="value">{{ stats.users }}</div><div class="label">Users</div></div>
</div>

<h2>VMs</h2>
<table>
<tr><th>ID</th><th>IP</th><th>Name</th><th>Status</th><th>Capacity</th><th>Created</th></tr>
{% for vm in vms %}
<tr>
  <td>{{ vm.id }}</td>
  <td>{{ vm.ip }}</td>
  <td>{{ vm.name }}</td>
  <td class="status-{{ vm.status }}">{{ vm.status }}</td>
  <td>{{ vm.capacity }}</td>
  <td>{{ vm.created_at[:10] }}</td>
</tr>
{% endfor %}
</table>

<h2>Instances</h2>
<table>
<tr><th>ID</th><th>VM</th><th>Name</th><th>Port</th><th>Status</th><th>User</th></tr>
{% for inst in instances %}
<tr>
  <td>{{ inst.id }}</td>
  <td>{{ inst.vm_id }}</td>
  <td>{{ inst.name }}</td>
  <td>{{ inst.port }}</td>
  <td class="status-{{ inst.status }}">{{ inst.status }}</td>
  <td>{{ inst.assigned_user or '-' }}</td>
</tr>
{% endfor %}
</table>

<h2>Users</h2>
<table>
<tr><th>Username</th><th>Instance</th><th>Relay URL</th><th>Created</th></tr>
{% for user in users %}
<tr>
  <td>{{ user.id }}</td>
  <td>{{ user.instance_id or '-' }}</td>
  <td>{{ user.relay_url or '-' }}</td>
  <td>{{ user.created_at[:10] }}</td>
</tr>
{% endfor %}
</table>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="9Bot Cloud Orchestrator")
    parser.add_argument("--port", type=int, default=9091,
                        help="Listen port (default: 9091)")
    parser.add_argument("--db", default=None,
                        help="SQLite database path")
    args = parser.parse_args()

    if not _get_admin_secret():
        print("WARNING: ORCHESTRATOR_SECRET not set — all requests will be rejected!")

    app = create_app(db_path=args.db)
    _start_health_monitor()
    print(f"9Bot Cloud Orchestrator listening on port {args.port}")
    app.run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
