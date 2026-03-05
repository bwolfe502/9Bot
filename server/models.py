"""SQLite persistence layer for the 9Bot cloud orchestrator.

Tables: vms, instances, users.  All timestamps are ISO-8601 strings.
"""

import os
import sqlite3
import threading
from datetime import datetime


_db_path = None
_local = threading.local()


def init_db(db_path=None):
    """Initialize the database, creating tables if needed.

    Args:
        db_path: Path to SQLite file. Defaults to ``server/orchestrator.db``
                 next to this module.
    """
    global _db_path
    _db_path = db_path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "orchestrator.db"
    )
    conn = _get_conn()
    conn.executescript(_SCHEMA)
    conn.commit()


def _get_conn():
    """Return a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(_db_path)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS vms (
    id          TEXT PRIMARY KEY,   -- Hetzner server ID
    ip          TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'provisioning',
    capacity    INTEGER NOT NULL DEFAULT 4,
    agent_port  INTEGER NOT NULL DEFAULT 9090,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instances (
    id          TEXT PRIMARY KEY,   -- unique instance ID (vm_id + name)
    vm_id       TEXT NOT NULL REFERENCES vms(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,      -- BlueStacks instance name
    port        INTEGER NOT NULL,   -- 9Bot dashboard port
    adb_device  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'stopped',
    assigned_user TEXT,             -- Discord username or NULL
    relay_url   TEXT,               -- User's relay access URL
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,   -- Discord username
    instance_id TEXT REFERENCES instances(id) ON DELETE SET NULL,
    relay_url   TEXT,
    notes       TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# VM CRUD
# ---------------------------------------------------------------------------

def create_vm(vm_id, ip, name="", capacity=4, agent_port=9090):
    """Register a new VM."""
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO vms (id, ip, name, status, capacity, agent_port, created_at) "
        "VALUES (?, ?, ?, 'provisioning', ?, ?, ?)",
        (vm_id, ip, name, capacity, agent_port, now),
    )
    conn.commit()
    return get_vm(vm_id)


def get_vm(vm_id):
    """Return a VM dict, or None."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM vms WHERE id = ?", (vm_id,)).fetchone()
    return dict(row) if row else None


def list_vms():
    """Return all VMs as list of dicts."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM vms ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def update_vm(vm_id, **kwargs):
    """Update VM fields. Accepted: ip, name, status, capacity, agent_port."""
    allowed = {"ip", "name", "status", "capacity", "agent_port"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    conn = _get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [vm_id]
    conn.execute(f"UPDATE vms SET {set_clause} WHERE id = ?", values)
    conn.commit()


def delete_vm(vm_id):
    """Delete a VM and cascade-delete its instances."""
    conn = _get_conn()
    conn.execute("DELETE FROM vms WHERE id = ?", (vm_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Instance CRUD
# ---------------------------------------------------------------------------

def create_instance(instance_id, vm_id, name, port, adb_device):
    """Register a new instance slot on a VM."""
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO instances (id, vm_id, name, port, adb_device, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'stopped', ?)",
        (instance_id, vm_id, name, port, adb_device, now),
    )
    conn.commit()
    return get_instance(instance_id)


def get_instance(instance_id):
    """Return an instance dict, or None."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM instances WHERE id = ?",
                       (instance_id,)).fetchone()
    return dict(row) if row else None


def list_instances(vm_id=None):
    """Return instances, optionally filtered by VM."""
    conn = _get_conn()
    if vm_id:
        rows = conn.execute(
            "SELECT * FROM instances WHERE vm_id = ? ORDER BY name",
            (vm_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM instances ORDER BY vm_id, name"
        ).fetchall()
    return [dict(r) for r in rows]


def update_instance(instance_id, **kwargs):
    """Update instance fields."""
    allowed = {"status", "assigned_user", "relay_url"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    conn = _get_conn()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [instance_id]
    conn.execute(f"UPDATE instances SET {set_clause} WHERE id = ?", values)
    conn.commit()


def find_available_instance():
    """Return the first unassigned, stopped instance, or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM instances WHERE assigned_user IS NULL "
        "ORDER BY vm_id, name LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def create_user(user_id, notes=""):
    """Register a new user (Discord username)."""
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO users (id, notes, created_at) VALUES (?, ?, ?)",
        (user_id, notes, now),
    )
    conn.commit()
    return get_user(user_id)


def get_user(user_id):
    """Return a user dict, or None."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?",
                       (user_id,)).fetchone()
    return dict(row) if row else None


def list_users():
    """Return all users as list of dicts."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def assign_user(user_id, instance_id):
    """Assign a user to an instance. Updates both user and instance records."""
    conn = _get_conn()
    conn.execute("UPDATE users SET instance_id = ? WHERE id = ?",
                 (instance_id, user_id))
    conn.execute("UPDATE instances SET assigned_user = ? WHERE id = ?",
                 (user_id, instance_id))
    conn.commit()


def unassign_user(user_id):
    """Remove a user's instance assignment."""
    conn = _get_conn()
    # Clear instance assignment
    user = get_user(user_id)
    if user and user["instance_id"]:
        conn.execute("UPDATE instances SET assigned_user = NULL WHERE id = ?",
                     (user["instance_id"],))
    conn.execute("UPDATE users SET instance_id = NULL, relay_url = NULL WHERE id = ?",
                 (user_id,))
    conn.commit()


def delete_user(user_id):
    """Unassign and delete a user."""
    unassign_user(user_id)
    conn = _get_conn()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Stats / summaries
# ---------------------------------------------------------------------------

def get_stats():
    """Return aggregate stats for the admin dashboard."""
    conn = _get_conn()
    vm_count = conn.execute("SELECT COUNT(*) FROM vms").fetchone()[0]
    inst_total = conn.execute("SELECT COUNT(*) FROM instances").fetchone()[0]
    inst_running = conn.execute(
        "SELECT COUNT(*) FROM instances WHERE status = 'running'"
    ).fetchone()[0]
    inst_assigned = conn.execute(
        "SELECT COUNT(*) FROM instances WHERE assigned_user IS NOT NULL"
    ).fetchone()[0]
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {
        "vms": vm_count,
        "instances_total": inst_total,
        "instances_running": inst_running,
        "instances_assigned": inst_assigned,
        "users": user_count,
    }
