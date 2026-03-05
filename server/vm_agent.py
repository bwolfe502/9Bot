"""VM Agent — per-VM management service for cloud-hosted 9Bot instances.

Runs as a lightweight HTTP service on each Windows VM. Manages local
BlueStacks instances and 9Bot processes.

Usage:
    python -m server.vm_agent                    # default config
    python -m server.vm_agent --config path.json # custom config
    python -m server.vm_agent --port 9090        # custom port

Environment variables:
    NINEBOT_AGENT_SECRET  -- Bearer token for authentication (required)
    NINEBOT_LICENSE_KEY   -- License key passed to 9Bot instances

Endpoints:
    GET  /health                      VM health (CPU, RAM, disk, uptime)
    GET  /instances                   List all instances + status
    POST /instances/{name}/start      Start BlueStacks + game + 9Bot
    POST /instances/{name}/stop       Stop 9Bot + BlueStacks
    GET  /instances/{name}/status     Instance status
    POST /instances/{name}/screenshot Take ADB screenshot
    POST /update                      Pull latest 9Bot code + restart instances
"""

import json
import os
import signal
import subprocess
import sys
import time
import threading
from functools import wraps

from flask import Flask, jsonify, request, abort


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_config = {}
_config_path = None

# Runtime state: {instance_name: {"bluestacks_pid": int|None,
#                                   "ninebot_proc": Popen|None,
#                                   "status": str,
#                                   "started_at": float|None,
#                                   "error": str|None}}
_instances = {}
_lock = threading.Lock()


def _load_config(path):
    """Load and validate agent configuration."""
    global _config, _config_path
    _config_path = path
    with open(path, "r", encoding="utf-8") as f:
        _config = json.load(f)

    # Initialize runtime state for each configured instance
    for inst in _config.get("instances", []):
        name = inst["name"]
        if name not in _instances:
            _instances[name] = {
                "bluestacks_pid": None,
                "ninebot_proc": None,
                "status": "stopped",
                "started_at": None,
                "error": None,
            }
    return _config


def _get_instance_config(name):
    """Return config dict for a named instance, or None."""
    for inst in _config.get("instances", []):
        if inst["name"] == name:
            return inst
    return None


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _get_agent_secret():
    return os.environ.get("NINEBOT_AGENT_SECRET", "")


def require_auth(f):
    """Decorator: reject requests without valid Bearer token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        secret = _get_agent_secret()
        if not secret:
            # No secret configured — reject all requests
            abort(403, description="Agent secret not configured")
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {secret}":
            abort(401, description="Invalid or missing authorization")
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Instance management helpers
# ---------------------------------------------------------------------------

def _bluestacks_exe():
    """Path to BlueStacks player executable."""
    bs_path = _config.get("bluestacks_path", r"C:\Program Files\BlueStacks_nxt")
    return os.path.join(bs_path, "HD-Player.exe")


def _adb_path():
    """Path to ADB executable (BlueStacks bundles its own)."""
    bs_path = _config.get("bluestacks_path", r"C:\Program Files\BlueStacks_nxt")
    return os.path.join(bs_path, "HD-Adb.exe")


def _ninebot_dir():
    return _config.get("ninebot_path", r"C:\9Bot")


def _start_bluestacks(inst_config):
    """Start a BlueStacks instance and launch the game. Returns PID."""
    exe = _bluestacks_exe()
    name = inst_config["name"]
    game_pkg = _config.get("game_package", "com.tap4fun.odin.kingdomguard")

    proc = subprocess.Popen([
        exe,
        "--instance", name,
        "--cmd", "launchApp",
        "--package", game_pkg,
    ])
    return proc.pid


def _wait_for_adb_device(adb_device, timeout=60):
    """Poll until ADB device is online."""
    adb = _adb_path()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = subprocess.run(
                [adb, "-s", adb_device, "shell", "echo", "ok"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and "ok" in result.stdout:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _start_ninebot(inst_config):
    """Start a 9Bot process for an instance. Returns subprocess.Popen."""
    bot_dir = _ninebot_dir()
    port = inst_config["port"]
    name = inst_config["name"]
    license_key = os.environ.get("NINEBOT_LICENSE_KEY", "")

    env = os.environ.copy()
    env["CLOUD_MODE"] = "1"
    env["NINEBOT_LICENSE_KEY"] = license_key
    env["NINEBOT_INSTANCE_ID"] = name
    env["NINEBOT_PORT"] = str(port)

    # Run 9Bot in headless mode
    proc = subprocess.Popen(
        [sys.executable, "run_web.py", "--headless"],
        cwd=bot_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    return proc


def _kill_process(pid):
    """Force-kill a process by PID."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _is_process_alive(pid):
    """Check if a process is still running."""
    if pid is None:
        return False
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app():
    app = Flask(__name__)

    @app.route("/health")
    @require_auth
    def health():
        """VM health: CPU, RAM, disk, uptime."""
        import platform as _platform
        info = {
            "status": "ok",
            "hostname": _platform.node(),
            "platform": _platform.platform(),
            "python": _platform.python_version(),
            "uptime_s": _get_uptime(),
        }
        # CPU/RAM via psutil if available
        try:
            import psutil
            info["cpu_percent"] = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            info["ram_total_gb"] = round(mem.total / (1024**3), 1)
            info["ram_used_gb"] = round(mem.used / (1024**3), 1)
            info["ram_percent"] = mem.percent
            disk = psutil.disk_usage("C:\\")
            info["disk_total_gb"] = round(disk.total / (1024**3), 1)
            info["disk_used_gb"] = round(disk.used / (1024**3), 1)
            info["disk_percent"] = disk.percent
        except ImportError:
            info["note"] = "Install psutil for detailed metrics"
        return jsonify(info)

    @app.route("/instances")
    @require_auth
    def list_instances():
        """List all configured instances with their current status."""
        result = []
        with _lock:
            for inst_cfg in _config.get("instances", []):
                name = inst_cfg["name"]
                state = _instances.get(name, {})
                result.append({
                    "name": name,
                    "port": inst_cfg["port"],
                    "adb_device": inst_cfg["adb_device"],
                    "status": state.get("status", "unknown"),
                    "started_at": state.get("started_at"),
                    "error": state.get("error"),
                    "bluestacks_alive": _is_process_alive(state.get("bluestacks_pid")),
                    "ninebot_alive": (state.get("ninebot_proc") is not None
                                      and state["ninebot_proc"].poll() is None),
                })
        return jsonify(result)

    @app.route("/instances/<name>/status")
    @require_auth
    def instance_status(name):
        """Detailed status for a single instance."""
        inst_cfg = _get_instance_config(name)
        if not inst_cfg:
            abort(404, description=f"Instance '{name}' not found")
        with _lock:
            state = _instances.get(name, {})
        return jsonify({
            "name": name,
            "port": inst_cfg["port"],
            "adb_device": inst_cfg["adb_device"],
            "status": state.get("status", "unknown"),
            "started_at": state.get("started_at"),
            "error": state.get("error"),
            "bluestacks_alive": _is_process_alive(state.get("bluestacks_pid")),
            "ninebot_alive": (state.get("ninebot_proc") is not None
                              and state["ninebot_proc"].poll() is None),
        })

    @app.route("/instances/<name>/start", methods=["POST"])
    @require_auth
    def start_instance(name):
        """Start BlueStacks instance + game + 9Bot."""
        inst_cfg = _get_instance_config(name)
        if not inst_cfg:
            abort(404, description=f"Instance '{name}' not found")

        with _lock:
            state = _instances.get(name, {})
            if state.get("status") in ("starting", "running"):
                return jsonify({"status": state["status"],
                                "message": "Already running"}), 409

            _instances[name] = {
                "bluestacks_pid": None,
                "ninebot_proc": None,
                "status": "starting",
                "started_at": time.time(),
                "error": None,
            }

        def _do_start():
            try:
                # 1. Start BlueStacks
                pid = _start_bluestacks(inst_cfg)
                with _lock:
                    _instances[name]["bluestacks_pid"] = pid

                # 2. Wait for ADB device to come online
                if not _wait_for_adb_device(inst_cfg["adb_device"], timeout=90):
                    with _lock:
                        _instances[name]["status"] = "error"
                        _instances[name]["error"] = "ADB device not found after 90s"
                    return

                # 3. Start 9Bot
                proc = _start_ninebot(inst_cfg)
                with _lock:
                    _instances[name]["ninebot_proc"] = proc
                    _instances[name]["status"] = "running"

            except Exception as e:
                with _lock:
                    _instances[name]["status"] = "error"
                    _instances[name]["error"] = str(e)

        threading.Thread(target=_do_start, daemon=True,
                         name=f"start-{name}").start()
        return jsonify({"status": "starting", "message": f"Starting {name}..."})

    @app.route("/instances/<name>/stop", methods=["POST"])
    @require_auth
    def stop_instance(name):
        """Stop 9Bot + BlueStacks for an instance."""
        inst_cfg = _get_instance_config(name)
        if not inst_cfg:
            abort(404, description=f"Instance '{name}' not found")

        with _lock:
            state = _instances.get(name, {})
            if state.get("status") == "stopped":
                return jsonify({"status": "stopped",
                                "message": "Already stopped"}), 200

        def _do_stop():
            with _lock:
                state = _instances.get(name, {})

            # Stop 9Bot process
            ninebot_proc = state.get("ninebot_proc")
            if ninebot_proc is not None:
                try:
                    ninebot_proc.terminate()
                    ninebot_proc.wait(timeout=10)
                except Exception:
                    if ninebot_proc.poll() is None:
                        _kill_process(ninebot_proc.pid)

            # Stop BlueStacks
            bs_pid = state.get("bluestacks_pid")
            if bs_pid and _is_process_alive(bs_pid):
                _kill_process(bs_pid)

            with _lock:
                _instances[name] = {
                    "bluestacks_pid": None,
                    "ninebot_proc": None,
                    "status": "stopped",
                    "started_at": None,
                    "error": None,
                }

        threading.Thread(target=_do_stop, daemon=True,
                         name=f"stop-{name}").start()
        return jsonify({"status": "stopping", "message": f"Stopping {name}..."})

    @app.route("/instances/<name>/screenshot", methods=["POST"])
    @require_auth
    def take_screenshot(name):
        """Take a screenshot via ADB and return as PNG."""
        inst_cfg = _get_instance_config(name)
        if not inst_cfg:
            abort(404, description=f"Instance '{name}' not found")

        adb = _adb_path()
        device = inst_cfg["adb_device"]
        try:
            result = subprocess.run(
                [adb, "-s", device, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                abort(500, description="Screenshot failed")
            from flask import Response
            return Response(result.stdout, mimetype="image/png")
        except subprocess.TimeoutExpired:
            abort(504, description="Screenshot timed out")

    @app.route("/update", methods=["POST"])
    @require_auth
    def update_bot():
        """Pull latest 9Bot code and restart all running instances."""
        bot_dir = _ninebot_dir()
        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=bot_dir, capture_output=True, text=True, timeout=30,
            )
            git_output = result.stdout.strip()
        except Exception as e:
            return jsonify({"status": "error",
                            "message": f"Git pull failed: {e}"}), 500

        # Restart running instances
        restarted = []
        with _lock:
            running = [name for name, state in _instances.items()
                       if state.get("status") == "running"]
        for name in running:
            # Stop and restart each
            inst_cfg = _get_instance_config(name)
            if inst_cfg:
                state = _instances.get(name, {})
                ninebot_proc = state.get("ninebot_proc")
                if ninebot_proc:
                    ninebot_proc.terminate()
                    try:
                        ninebot_proc.wait(timeout=10)
                    except Exception:
                        _kill_process(ninebot_proc.pid)
                proc = _start_ninebot(inst_cfg)
                with _lock:
                    _instances[name]["ninebot_proc"] = proc
                restarted.append(name)

        return jsonify({
            "status": "ok",
            "git": git_output,
            "restarted": restarted,
        })

    return app


# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------

_start_time = time.time()


def _get_uptime():
    return int(time.time() - _start_time)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="9Bot VM Agent")
    parser.add_argument("--config", default=None,
                        help="Path to config JSON (default: auto-detect)")
    parser.add_argument("--port", type=int, default=9090,
                        help="Agent listen port (default: 9090)")
    args = parser.parse_args()

    # Find config file
    config_path = args.config
    if config_path is None:
        # Look next to this script, then in ninebot dir
        for candidate in [
            os.path.join(os.path.dirname(__file__), "vm_agent_config.json"),
            r"C:\9Bot\server\vm_agent_config.json",
        ]:
            if os.path.isfile(candidate):
                config_path = candidate
                break
    if config_path is None or not os.path.isfile(config_path):
        print("ERROR: No config file found. Use --config to specify one.")
        sys.exit(1)

    _load_config(config_path)
    print(f"VM Agent loaded config: {config_path}")
    print(f"  Instances: {[i['name'] for i in _config.get('instances', [])]}")
    print(f"  BlueStacks: {_config.get('bluestacks_path')}")
    print(f"  9Bot: {_config.get('ninebot_path')}")

    if not _get_agent_secret():
        print("WARNING: NINEBOT_AGENT_SECRET not set — all requests will be rejected!")

    app = create_app()
    print(f"\nVM Agent listening on port {args.port}")
    app.run(host="0.0.0.0", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
