"""
Portal Authentication Helpers

- bcrypt password hashing (cost 12)
- Session cookie management
- Rate limiting (5 failed logins per 5 min per IP)
- CSRF token generation/validation
"""

import hashlib
import hmac
import secrets
import time
import threading
from collections import defaultdict

import bcrypt

# ------------------------------------------------------------------
# Password hashing
# ------------------------------------------------------------------

BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


# ------------------------------------------------------------------
# Rate limiting (in-memory, per-IP)
# ------------------------------------------------------------------

_rate_lock = threading.Lock()
_failed_attempts: dict[str, list[float]] = defaultdict(list)

RATE_LIMIT_WINDOW = 300   # 5 minutes
RATE_LIMIT_MAX = 5         # max failures per window


def check_rate_limit(ip: str) -> bool:
    """Return True if the IP is allowed to attempt login, False if rate-limited."""
    now = time.time()
    with _rate_lock:
        attempts = _failed_attempts[ip]
        # Prune old entries
        attempts[:] = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]
        return len(attempts) < RATE_LIMIT_MAX


def record_failed_login(ip: str) -> None:
    with _rate_lock:
        _failed_attempts[ip].append(time.time())


def clear_rate_limit(ip: str) -> None:
    with _rate_lock:
        _failed_attempts.pop(ip, None)


# ------------------------------------------------------------------
# CSRF tokens
# ------------------------------------------------------------------

# Server-wide secret, regenerated on restart.  Fine for CSRF tokens
# (they're tied to sessions, not persistent across restarts).
_csrf_secret = secrets.token_bytes(32)


def generate_csrf_token(session_token: str) -> str:
    """Generate a CSRF token bound to a session."""
    return hmac.new(
        _csrf_secret,
        session_token.encode(),
        hashlib.sha256,
    ).hexdigest()


def validate_csrf_token(session_token: str, csrf_token: str) -> bool:
    expected = generate_csrf_token(session_token)
    return hmac.compare_digest(expected, csrf_token)


# ------------------------------------------------------------------
# Cookie helpers
# ------------------------------------------------------------------

SESSION_COOKIE = "portal_session"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days


def set_session_cookie(response, token: str) -> None:
    """Set the session cookie on an aiohttp response."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.del_cookie(SESSION_COOKIE, path="/")
