"""
Portal Database Layer

SQLite-backed storage for portal users, bots, devices, grants, sessions,
and invite codes.  All public functions are synchronous (blocking) —
call from ``asyncio.to_thread()`` in async handlers.

Usage:
    from portal_db import init_db, create_user, ...
    init_db()  # call once at startup
"""

import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("PORTAL_DB", "/opt/9bot-relay/portal.db")

_local = threading.local()


def _conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (created on first access)."""
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    email         TEXT,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login    TEXT
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT    NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bots (
    bot_name   TEXT PRIMARY KEY,
    owner_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    label      TEXT,
    last_seen  TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_name    TEXT    NOT NULL REFERENCES bots(bot_name) ON DELETE CASCADE,
    device_hash TEXT    NOT NULL,
    device_name TEXT,
    UNIQUE(bot_name, device_hash)
);

CREATE TABLE IF NOT EXISTS grants (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bot_name     TEXT    NOT NULL,
    device_hash  TEXT,
    access_level TEXT    NOT NULL DEFAULT 'readonly',
    granted_by   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, bot_name, device_hash)
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invite_codes (
    code       TEXT PRIMARY KEY,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    used_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    stripe_customer_id      TEXT NOT NULL,
    stripe_subscription_id  TEXT,
    plan                    TEXT NOT NULL DEFAULT 'none',
    status                  TEXT NOT NULL DEFAULT 'inactive',
    device_limit            INTEGER NOT NULL DEFAULT 0,
    current_period_end      TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    """Create tables if they don't exist.  Safe to call multiple times."""
    c = _conn()
    c.executescript(_SCHEMA)
    # Migration: add stripe_customer_id to users if missing
    cols = {row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()}
    if "stripe_customer_id" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT")
    if "email" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN email TEXT")
    # Migration: create password_reset_tokens if missing (schema above handles new DBs)
    c.execute(
        "CREATE TABLE IF NOT EXISTS password_reset_tokens ("
        "token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
        "expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    # Migration: add label + is_shared to devices if missing
    dev_cols = {row[1] for row in c.execute("PRAGMA table_info(devices)").fetchall()}
    if "label" not in dev_cols:
        c.execute("ALTER TABLE devices ADD COLUMN label TEXT")
    if "is_shared" not in dev_cols:
        c.execute("ALTER TABLE devices ADD COLUMN is_shared INTEGER NOT NULL DEFAULT 0")
    if "is_public" not in dev_cols:
        c.execute("ALTER TABLE devices ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0")
    c.commit()


# ------------------------------------------------------------------
# Users
# ------------------------------------------------------------------

def create_user(
    username: str, password_hash: str, is_admin: bool = False,
    email: str | None = None,
) -> int:
    """Insert a new user.  Returns the user ID."""
    c = _conn()
    cur = c.execute(
        "INSERT INTO users (username, password_hash, is_admin, email) VALUES (?, ?, ?, ?)",
        (username, password_hash, int(is_admin), email or None),
    )
    c.commit()
    return cur.lastrowid


def get_user_by_username(username: str) -> dict | None:
    """Look up user by username (case-insensitive).  Returns dict or None."""
    row = _conn().execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    row = _conn().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def update_user_login(user_id: int) -> None:
    c = _conn()
    c.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (user_id,))
    c.commit()


def update_user_password(user_id: int, password_hash: str) -> None:
    c = _conn()
    c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    c.commit()


def list_users() -> list[dict]:
    rows = _conn().execute(
        "SELECT id, username, email, is_admin, created_at, last_login FROM users ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_user(user_id: int) -> bool:
    c = _conn()
    cur = c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    c.commit()
    return cur.rowcount > 0


def update_user_email(user_id: int, email: str | None) -> None:
    c = _conn()
    c.execute("UPDATE users SET email = ? WHERE id = ?", (email or None, user_id))
    c.commit()


def get_user_by_email(email: str) -> dict | None:
    """Look up user by email (case-insensitive).  Returns dict or None."""
    row = _conn().execute(
        "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------
# Password Reset Tokens
# ------------------------------------------------------------------

RESET_TOKEN_LIFETIME_HOURS = 24


def create_password_reset_token(user_id: int) -> str:
    """Create a password reset token valid for RESET_TOKEN_LIFETIME_HOURS.  Returns token."""
    token = secrets.token_urlsafe(32)
    expires = (
        datetime.now(timezone.utc) + timedelta(hours=RESET_TOKEN_LIFETIME_HOURS)
    ).isoformat()
    c = _conn()
    c.execute(
        "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires),
    )
    c.commit()
    return token


def validate_password_reset_token(token: str) -> dict | None:
    """Return user dict if token is valid and unused, else None."""
    c = _conn()
    row = c.execute(
        "SELECT t.user_id, t.expires_at, t.used FROM password_reset_tokens t "
        "WHERE t.token = ?",
        (token,),
    ).fetchone()
    if not row or row["used"]:
        return None
    expires = datetime.fromisoformat(row["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return None
    user = get_user_by_id(row["user_id"])
    return user


def use_password_reset_token(token: str) -> bool:
    """Mark a reset token as used.  Returns True if updated."""
    c = _conn()
    cur = c.execute(
        "UPDATE password_reset_tokens SET used = 1 WHERE token = ? AND used = 0",
        (token,),
    )
    c.commit()
    return cur.rowcount > 0


# ------------------------------------------------------------------
# Sessions
# ------------------------------------------------------------------

SESSION_LIFETIME_DAYS = 30


def create_session(user_id: int) -> str:
    """Create a session token valid for SESSION_LIFETIME_DAYS.  Returns token."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)).isoformat()
    c = _conn()
    c.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires),
    )
    c.commit()
    return token


def validate_session(token: str) -> dict | None:
    """Return user dict if session is valid, else None.  Deletes expired sessions."""
    c = _conn()
    row = c.execute(
        "SELECT s.user_id, s.expires_at, u.username, u.is_admin "
        "FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.token = ?",
        (token,),
    ).fetchone()
    if not row:
        return None
    expires = datetime.fromisoformat(row["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        c.execute("DELETE FROM sessions WHERE token = ?", (token,))
        c.commit()
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
    }


def delete_session(token: str) -> None:
    c = _conn()
    c.execute("DELETE FROM sessions WHERE token = ?", (token,))
    c.commit()


def delete_user_sessions(user_id: int) -> None:
    c = _conn()
    c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    c.commit()


def cleanup_expired_sessions() -> int:
    """Delete expired sessions.  Returns count deleted."""
    c = _conn()
    cur = c.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")
    c.commit()
    return cur.rowcount


# ------------------------------------------------------------------
# Bots
# ------------------------------------------------------------------

def upsert_bot(bot_name: str, label: str | None = None) -> None:
    """Insert or update bot, refreshing last_seen."""
    c = _conn()
    c.execute(
        "INSERT INTO bots (bot_name, last_seen) VALUES (?, datetime('now')) "
        "ON CONFLICT(bot_name) DO UPDATE SET last_seen = datetime('now')",
        (bot_name,),
    )
    if label:
        c.execute("UPDATE bots SET label = ? WHERE bot_name = ? AND label IS NULL",
                   (label, bot_name))
    c.commit()


def get_bot(bot_name: str) -> dict | None:
    row = _conn().execute("SELECT * FROM bots WHERE bot_name = ?", (bot_name,)).fetchone()
    return dict(row) if row else None


def set_bot_owner(bot_name: str, owner_id: int | None) -> bool:
    c = _conn()
    cur = c.execute("UPDATE bots SET owner_id = ? WHERE bot_name = ?", (owner_id, bot_name))
    c.commit()
    return cur.rowcount > 0


def set_bot_label(bot_name: str, label: str) -> bool:
    c = _conn()
    cur = c.execute("UPDATE bots SET label = ? WHERE bot_name = ?", (label, bot_name))
    c.commit()
    return cur.rowcount > 0


def list_bots() -> list[dict]:
    rows = _conn().execute(
        "SELECT b.*, u.username AS owner_name "
        "FROM bots b LEFT JOIN users u ON b.owner_id = u.id "
        "ORDER BY b.bot_name"
    ).fetchall()
    return [dict(r) for r in rows]


def touch_bot(bot_name: str) -> None:
    c = _conn()
    c.execute("UPDATE bots SET last_seen = datetime('now') WHERE bot_name = ?", (bot_name,))
    c.commit()


# ------------------------------------------------------------------
# Devices
# ------------------------------------------------------------------

def upsert_device(bot_name: str, device_hash: str, device_name: str | None = None) -> None:
    c = _conn()
    c.execute(
        "INSERT INTO devices (bot_name, device_hash, device_name) VALUES (?, ?, ?) "
        "ON CONFLICT(bot_name, device_hash) DO UPDATE SET device_name = "
        "COALESCE(excluded.device_name, devices.device_name)",
        (bot_name, device_hash, device_name),
    )
    c.commit()


def list_devices(bot_name: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM devices WHERE bot_name = ? ORDER BY device_name, device_hash",
        (bot_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_stale_devices(bot_name: str, keep_hashes: set[str]) -> int:
    """Remove devices no longer reported by the bot.  Returns count deleted."""
    if not keep_hashes:
        return 0
    c = _conn()
    placeholders = ",".join("?" for _ in keep_hashes)
    cur = c.execute(
        f"DELETE FROM devices WHERE bot_name = ? AND device_hash NOT IN ({placeholders})",
        [bot_name, *keep_hashes],
    )
    c.commit()
    return cur.rowcount


def set_device_label(bot_name: str, device_hash: str, label: str) -> bool:
    """Set the admin display name for a device.  Returns True if updated."""
    c = _conn()
    cur = c.execute(
        "UPDATE devices SET label = ? WHERE bot_name = ? AND device_hash = ?",
        (label or None, bot_name, device_hash),
    )
    c.commit()
    return cur.rowcount > 0


def set_device_shared(bot_name: str, device_hash: str, is_shared: bool) -> bool:
    """Toggle the community/shared flag on a device.  Returns True if updated."""
    c = _conn()
    cur = c.execute(
        "UPDATE devices SET is_shared = ? WHERE bot_name = ? AND device_hash = ?",
        (int(is_shared), bot_name, device_hash),
    )
    c.commit()
    return cur.rowcount > 0


def list_shared_devices() -> list[dict]:
    """Return all devices marked as community/shared, with bot online info."""
    rows = _conn().execute(
        "SELECT d.*, b.last_seen FROM devices d "
        "JOIN bots b ON d.bot_name = b.bot_name "
        "WHERE d.is_shared = 1 "
        "ORDER BY d.label, d.device_name, d.device_hash"
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_devices(user_id: int) -> list[dict]:
    """Get all devices a user has grants for (via specific or wildcard grants).

    Returns devices with labels and bot info.  Does NOT include shared devices.
    """
    c = _conn()
    # Devices from bots the user owns
    owned = c.execute(
        "SELECT d.*, b.last_seen FROM devices d "
        "JOIN bots b ON d.bot_name = b.bot_name "
        "WHERE b.owner_id = ? "
        "ORDER BY d.label, d.device_name",
        (user_id,),
    ).fetchall()

    # Devices via specific grants
    granted_specific = c.execute(
        "SELECT d.*, b.last_seen, g.access_level FROM devices d "
        "JOIN bots b ON d.bot_name = b.bot_name "
        "JOIN grants g ON g.bot_name = d.bot_name AND g.device_hash = d.device_hash "
        "WHERE g.user_id = ? AND (b.owner_id IS NULL OR b.owner_id != ?) "
        "ORDER BY d.label, d.device_name",
        (user_id, user_id),
    ).fetchall()

    # Devices via wildcard grants (device_hash IS NULL = all devices on that bot)
    granted_wildcard = c.execute(
        "SELECT d.*, b.last_seen, g.access_level FROM devices d "
        "JOIN bots b ON d.bot_name = b.bot_name "
        "JOIN grants g ON g.bot_name = d.bot_name AND g.device_hash IS NULL "
        "WHERE g.user_id = ? AND (b.owner_id IS NULL OR b.owner_id != ?) "
        "ORDER BY d.label, d.device_name",
        (user_id, user_id),
    ).fetchall()

    # Merge, dedup by (bot_name, device_hash)
    seen = set()
    result = []
    for row in list(owned) + list(granted_specific) + list(granted_wildcard):
        d = dict(row)
        key = (d["bot_name"], d["device_hash"])
        if key not in seen:
            seen.add(key)
            if "access_level" not in d:
                d["access_level"] = "full"  # owner gets full
            result.append(d)
    return result


def set_device_public(bot_name: str, device_hash: str, is_public: bool) -> bool:
    """Toggle the public (no login required) flag on a device."""
    c = _conn()
    cur = c.execute(
        "UPDATE devices SET is_public = ? WHERE bot_name = ? AND device_hash = ?",
        (int(is_public), bot_name, device_hash),
    )
    c.commit()
    return cur.rowcount > 0


def is_device_shared(bot_name: str, device_hash: str) -> bool:
    """Check if a specific device is marked as community/shared."""
    row = _conn().execute(
        "SELECT is_shared FROM devices WHERE bot_name = ? AND device_hash = ?",
        (bot_name, device_hash),
    ).fetchone()
    return bool(row and row["is_shared"])


def is_device_public(bot_name: str, device_hash: str) -> bool:
    """Check if a specific device is marked as public (no login required)."""
    row = _conn().execute(
        "SELECT is_public FROM devices WHERE bot_name = ? AND device_hash = ?",
        (bot_name, device_hash),
    ).fetchone()
    return bool(row and row["is_public"])


# ------------------------------------------------------------------
# Grants
# ------------------------------------------------------------------

def create_grant(
    user_id: int, bot_name: str, device_hash: str | None,
    access_level: str, granted_by: int,
) -> int:
    """Create or replace a grant.  Returns grant ID."""
    c = _conn()
    cur = c.execute(
        "INSERT INTO grants (user_id, bot_name, device_hash, access_level, granted_by) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, bot_name, device_hash) DO UPDATE SET "
        "access_level = excluded.access_level, granted_by = excluded.granted_by",
        (user_id, bot_name, device_hash, access_level, granted_by),
    )
    c.commit()
    return cur.lastrowid


def delete_grant(grant_id: int) -> bool:
    c = _conn()
    cur = c.execute("DELETE FROM grants WHERE id = ?", (grant_id,))
    c.commit()
    return cur.rowcount > 0


def list_grants_for_bot(bot_name: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT g.*, u.username FROM grants g JOIN users u ON g.user_id = u.id "
        "WHERE g.bot_name = ? ORDER BY u.username",
        (bot_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def check_access(user_id: int, bot_name: str, device_hash: str | None = None) -> str | None:
    """Check if user has access to a bot (or specific device).

    Returns 'full' or 'readonly' if granted, None if no access.
    Bot owner always gets 'full' access.
    """
    c = _conn()

    # Bot owner check
    row = c.execute(
        "SELECT owner_id FROM bots WHERE bot_name = ?", (bot_name,)
    ).fetchone()
    if row and row["owner_id"] == user_id:
        return "full"

    # Admin gets full access to everything
    user = get_user_by_id(user_id)
    if user and user["is_admin"]:
        return "full"

    # Check specific device grant first, then wildcard (device_hash IS NULL)
    if device_hash:
        row = c.execute(
            "SELECT access_level FROM grants "
            "WHERE user_id = ? AND bot_name = ? AND device_hash = ?",
            (user_id, bot_name, device_hash),
        ).fetchone()
        if row:
            return row["access_level"]

    # Wildcard grant (all devices)
    row = c.execute(
        "SELECT access_level FROM grants "
        "WHERE user_id = ? AND bot_name = ? AND device_hash IS NULL",
        (user_id, bot_name),
    ).fetchone()
    return row["access_level"] if row else None


def get_user_bots(user_id: int) -> dict:
    """Get all bots a user can access.

    Returns {"owned": [...], "shared": [...]}.
    """
    c = _conn()
    user = get_user_by_id(user_id)

    # Owned bots
    owned = c.execute(
        "SELECT b.*, "
        "(SELECT COUNT(*) FROM devices d WHERE d.bot_name = b.bot_name) AS device_count "
        "FROM bots b WHERE b.owner_id = ? ORDER BY b.bot_name",
        (user_id,),
    ).fetchall()

    # Shared bots (via grants, excluding owned)
    shared = c.execute(
        "SELECT DISTINCT b.*, g.access_level, "
        "(SELECT COUNT(*) FROM devices d WHERE d.bot_name = b.bot_name) AS device_count "
        "FROM grants g JOIN bots b ON g.bot_name = b.bot_name "
        "WHERE g.user_id = ? AND (b.owner_id IS NULL OR b.owner_id != ?) "
        "ORDER BY b.bot_name",
        (user_id, user_id),
    ).fetchall()

    # Admin sees all bots
    if user and user["is_admin"]:
        all_bots = c.execute(
            "SELECT b.*, "
            "(SELECT COUNT(*) FROM devices d WHERE d.bot_name = b.bot_name) AS device_count "
            "FROM bots b ORDER BY b.bot_name"
        ).fetchall()
        owned_names = {r["bot_name"] for r in owned}
        shared_names = {r["bot_name"] for r in shared}
        for b in all_bots:
            if b["bot_name"] not in owned_names and b["bot_name"] not in shared_names:
                shared = list(shared) + [b]

    return {"owned": [dict(r) for r in owned], "shared": [dict(r) for r in shared]}


# ------------------------------------------------------------------
# Invite Codes
# ------------------------------------------------------------------

def create_invite_code(created_by: int) -> str:
    """Generate and store a one-time invite code.  Returns the code."""
    code = secrets.token_urlsafe(8)
    c = _conn()
    c.execute(
        "INSERT INTO invite_codes (code, created_by) VALUES (?, ?)",
        (code, created_by),
    )
    c.commit()
    return code


def use_invite_code(code: str, user_id: int) -> bool:
    """Attempt to consume an invite code.  Returns True if valid and unused."""
    c = _conn()
    row = c.execute(
        "SELECT code FROM invite_codes WHERE code = ? AND used_by IS NULL",
        (code,),
    ).fetchone()
    if not row:
        return False
    c.execute("UPDATE invite_codes SET used_by = ? WHERE code = ?", (user_id, code))
    c.commit()
    return True


def list_invite_codes(created_by: int | None = None) -> list[dict]:
    """List invite codes, optionally filtered by creator."""
    c = _conn()
    if created_by is not None:
        rows = c.execute(
            "SELECT ic.*, u.username AS used_by_name "
            "FROM invite_codes ic LEFT JOIN users u ON ic.used_by = u.id "
            "WHERE ic.created_by = ? ORDER BY ic.created_at DESC",
            (created_by,),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT ic.*, u.username AS used_by_name, c.username AS created_by_name "
            "FROM invite_codes ic "
            "LEFT JOIN users u ON ic.used_by = u.id "
            "LEFT JOIN users c ON ic.created_by = c.id "
            "ORDER BY ic.created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Subscriptions
# ------------------------------------------------------------------

def upsert_subscription(
    user_id: int,
    stripe_customer_id: str,
    stripe_subscription_id: str | None = None,
    plan: str = "none",
    status: str = "inactive",
    device_limit: int = 0,
    current_period_end: str | None = None,
) -> int:
    """Insert or update a subscription for a user.  Returns row ID."""
    c = _conn()
    cur = c.execute(
        "INSERT INTO subscriptions "
        "(user_id, stripe_customer_id, stripe_subscription_id, plan, status, "
        "device_limit, current_period_end) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "stripe_customer_id = excluded.stripe_customer_id, "
        "stripe_subscription_id = excluded.stripe_subscription_id, "
        "plan = excluded.plan, status = excluded.status, "
        "device_limit = excluded.device_limit, "
        "current_period_end = excluded.current_period_end, "
        "updated_at = datetime('now')",
        (user_id, stripe_customer_id, stripe_subscription_id, plan, status,
         device_limit, current_period_end),
    )
    # Also store stripe_customer_id on users table for reverse lookup
    c.execute(
        "UPDATE users SET stripe_customer_id = ? WHERE id = ?",
        (stripe_customer_id, user_id),
    )
    c.commit()
    return cur.lastrowid


def get_subscription(user_id: int) -> dict | None:
    """Get subscription for a user.  Returns dict or None."""
    row = _conn().execute(
        "SELECT * FROM subscriptions WHERE user_id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    """Look up user by Stripe customer ID."""
    row = _conn().execute(
        "SELECT * FROM users WHERE stripe_customer_id = ?", (stripe_customer_id,)
    ).fetchone()
    return dict(row) if row else None


def count_user_device_grants(user_id: int) -> int:
    """Count distinct devices a user has been granted access to (as owner or grantee)."""
    c = _conn()
    # Count devices in bots the user owns
    owned = c.execute(
        "SELECT COUNT(DISTINCT d.device_hash) FROM devices d "
        "JOIN bots b ON d.bot_name = b.bot_name WHERE b.owner_id = ?",
        (user_id,),
    ).fetchone()[0]
    # Count distinct device-specific grants (exclude wildcard grants)
    granted = c.execute(
        "SELECT COUNT(*) FROM grants WHERE user_id = ? AND device_hash IS NOT NULL",
        (user_id,),
    ).fetchone()[0]
    return owned + granted


def grant_admin_subscription(
    user_id: int, plan: str, device_limit: int, duration_days: int | None = None,
) -> int:
    """Grant a free admin subscription.  duration_days=None means permanent (2099)."""
    if duration_days:
        period_end = (
            datetime.now(timezone.utc) + timedelta(days=duration_days)
        ).isoformat()
    else:
        period_end = "2099-12-31T23:59:59+00:00"
    return upsert_subscription(
        user_id=user_id,
        stripe_customer_id="admin_grant",
        stripe_subscription_id=None,
        plan=plan,
        status="active",
        device_limit=device_limit,
        current_period_end=period_end,
    )


def revoke_admin_subscription(user_id: int) -> bool:
    """Cancel an admin-granted subscription.  Returns True if updated."""
    c = _conn()
    cur = c.execute(
        "UPDATE subscriptions SET status = 'canceled', updated_at = datetime('now') "
        "WHERE user_id = ? AND stripe_customer_id = 'admin_grant'",
        (user_id,),
    )
    c.commit()
    return cur.rowcount > 0


def expire_admin_subscriptions() -> int:
    """Mark expired admin-granted subs as canceled.  Returns count expired."""
    c = _conn()
    cur = c.execute(
        "UPDATE subscriptions SET status = 'canceled', updated_at = datetime('now') "
        "WHERE stripe_customer_id = 'admin_grant' AND status = 'active' "
        "AND current_period_end < datetime('now')"
    )
    c.commit()
    return cur.rowcount


def list_subscriptions() -> list[dict]:
    """List all subscriptions with username.  For admin panel."""
    rows = _conn().execute(
        "SELECT s.*, u.username FROM subscriptions s "
        "JOIN users u ON s.user_id = u.id ORDER BY s.user_id"
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# CLI helpers (for initial admin creation)
# ------------------------------------------------------------------

def create_admin_cli(username: str, password: str) -> None:
    """Create an admin user from the command line.  Used during initial setup."""
    import bcrypt
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    try:
        uid = create_user(username, pw_hash, is_admin=True)
        print(f"Admin user '{username}' created (id={uid})")
    except sqlite3.IntegrityError:
        print(f"User '{username}' already exists")
