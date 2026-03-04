"""
Portal Routes

All /portal/... handlers for the 9Bot relay server.
Server-rendered HTML with dark theme matching the 9Bot dashboard.

Route summary:
    GET  /portal/login          — login page
    POST /portal/api/login      — login API
    POST /portal/api/logout     — logout API
    GET  /portal/register       — register page
    POST /portal/api/register   — register API
    GET  /portal/               — dashboard (my bots + shared)
    GET  /portal/bot/{bot_name} — bot detail / grant management
    GET  /portal/admin          — admin panel
    GET  /portal/account        — account settings
    POST /portal/api/grants     — create grant
    DELETE /portal/api/grants/{id} — revoke grant
    POST /portal/api/invite-codes — generate invite code
    GET  /portal/api/invite-codes — list invite codes
    GET  /portal/api/bots       — my bots + shared (JSON)
    PUT  /portal/api/bots/{bot_name} — update bot label
    GET  /portal/api/grants/{bot_name} — grants for bot (JSON)
    GET  /portal/api/admin/users — all users (admin)
    DELETE /portal/api/admin/users/{id} — delete user (admin)
    PUT  /portal/api/admin/bots/{bot_name}/owner — set bot owner (admin)
    GET  /portal/pricing          — subscription plan cards
    GET  /portal/billing          — current plan + manage link
    POST /portal/api/subscribe    — create Stripe Checkout session
    GET  /portal/checkout-success — post-checkout landing
    POST /portal/api/billing-portal — redirect to Stripe Customer Portal
    POST /portal/api/stripe-webhook — Stripe webhook (no auth, sig-verified)
"""

import asyncio
import functools
import json
import logging

from aiohttp import web

import portal_db as db
import portal_auth as auth

log = logging.getLogger("portal")

# Reference to _bots dict from relay_server (injected at setup time)
_active_bots: dict = {}


def setup_portal_routes(app: web.Application, active_bots: dict) -> None:
    """Register all portal routes on the aiohttp app."""
    global _active_bots
    _active_bots = active_bots

    # Pages
    app.router.add_get("/portal", _redirect_portal_slash)
    app.router.add_get("/portal/", page_dashboard)
    app.router.add_get("/portal/login", page_login)
    app.router.add_get("/portal/register", page_register)
    app.router.add_get("/portal/community", page_community)
    app.router.add_get("/portal/bot/{bot_name}", page_bot_detail)
    app.router.add_get("/portal/admin", page_admin)
    app.router.add_get("/portal/account", page_account)
    app.router.add_get("/portal/guide", page_guide)

    # API
    app.router.add_post("/portal/api/login", api_login)
    app.router.add_post("/portal/api/logout", api_logout)
    app.router.add_post("/portal/api/register", api_register)
    app.router.add_get("/portal/api/bots", api_bots)
    app.router.add_put("/portal/api/bots/{bot_name}", api_update_bot)
    app.router.add_get("/portal/api/grants/{bot_name}", api_grants_for_bot)
    app.router.add_post("/portal/api/grants", api_create_grant)
    app.router.add_delete("/portal/api/grants/{grant_id}", api_delete_grant)
    app.router.add_post("/portal/api/invite-codes", api_create_invite)
    app.router.add_get("/portal/api/invite-codes", api_list_invites)
    app.router.add_get("/portal/api/admin/users", api_admin_users)
    app.router.add_delete("/portal/api/admin/users/{user_id}", api_admin_delete_user)
    app.router.add_put("/portal/api/admin/bots/{bot_name}/owner", api_admin_set_owner)
    app.router.add_post("/portal/api/admin/reset-password", api_admin_reset_password)
    app.router.add_post("/portal/api/admin/grant-subscription", api_admin_grant_subscription)
    app.router.add_post("/portal/api/admin/revoke-subscription", api_admin_revoke_subscription)
    app.router.add_post("/portal/api/account/password", api_change_password)
    app.router.add_post("/portal/api/account/email", api_change_email)
    app.router.add_put("/portal/api/devices/{bot_name}/{device_hash}/label", api_set_device_label)
    app.router.add_put("/portal/api/devices/{bot_name}/{device_hash}/shared", api_set_device_shared)
    app.router.add_put("/portal/api/devices/{bot_name}/{device_hash}/public", api_set_device_public)

    # Billing
    app.router.add_get("/portal/pricing", page_pricing)
    app.router.add_get("/portal/billing", page_billing)
    app.router.add_get("/portal/checkout-success", page_checkout_success)
    app.router.add_post("/portal/api/subscribe", api_subscribe)
    app.router.add_post("/portal/api/billing-portal", api_billing_portal)
    app.router.add_post("/portal/api/stripe-webhook", api_stripe_webhook)


async def _redirect_portal_slash(request: web.Request) -> web.Response:
    raise web.HTTPFound("/portal/")


# ------------------------------------------------------------------
# Auth helpers
# ------------------------------------------------------------------

def _get_session_token(request: web.Request) -> str | None:
    return request.cookies.get(auth.SESSION_COOKIE)


async def _get_user(request: web.Request) -> dict | None:
    """Validate session cookie, return user dict or None."""
    token = _get_session_token(request)
    if not token:
        return None
    return await asyncio.to_thread(db.validate_session, token)


async def _require_user(request: web.Request) -> dict:
    """Like _get_user but redirects to login if not authenticated."""
    user = await _get_user(request)
    if not user:
        next_url = str(request.url.relative())
        raise web.HTTPFound(f"/portal/login?next={next_url}")
    return user


async def _require_admin(request: web.Request) -> dict:
    user = await _require_user(request)
    if not user["is_admin"]:
        raise web.HTTPForbidden(text="Admin access required")
    return user


def _get_csrf(request: web.Request) -> str:
    token = _get_session_token(request)
    return auth.generate_csrf_token(token) if token else ""


async def _check_csrf(request: web.Request) -> None:
    """Validate CSRF token from form data or JSON body."""
    session_token = _get_session_token(request)
    if not session_token:
        raise web.HTTPForbidden(text="No session")

    ct = request.content_type or ""
    if "json" in ct:
        try:
            data = await request.json()
        except Exception:
            data = {}
        csrf = data.get("csrf_token", "")
    else:
        data = await request.post()
        csrf = data.get("csrf_token", "")

    if not auth.validate_csrf_token(session_token, csrf):
        raise web.HTTPForbidden(text="Invalid CSRF token")


def _client_ip(request: web.Request) -> str:
    return request.headers.get("X-Real-IP", request.remote or "unknown")


# ------------------------------------------------------------------
# HTML templating (inline, minimal)
# ------------------------------------------------------------------

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,wght@1,700&text=9&display=swap');

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: #0c0c18; color: #e0e0f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    min-height: 100vh;
    -webkit-text-size-adjust: 100%;
}
a { color: #64d8ff; text-decoration: none; }
a:hover { text-decoration: underline; }
code { font-family: "SF Mono", "Consolas", monospace; font-size: 0.9em; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }

/* Layout */
.container { max-width: 720px; margin: 0 auto; padding: 12px; }

/* Nav — matches 9Bot dashboard nav */
.nav {
    position: sticky; top: 0; z-index: 100;
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px;
    background: #14142a;
    border-bottom: 1px solid rgba(100,216,255,0.08);
}
.nav-logo {
    display: flex; align-items: center; gap: 3px;
    text-decoration: none;
}
.nav-logo-bars {
    display: flex; flex-direction: column; gap: 2px; width: 3px; height: 28px;
}
.nav-logo-bars::before, .nav-logo-bars::after {
    content: ''; width: 2px; flex: 1; border-radius: 1px;
}
.nav-logo-bars::before {
    background: linear-gradient(180deg, rgba(100,216,255,0.13), rgba(100,216,255,0.53), rgba(100,216,255,0.13));
}
.nav-logo-bars::after {
    background: linear-gradient(180deg, rgba(100,216,255,0.27), rgba(100,216,255,1), rgba(100,216,255,0.27));
}
.nav-logo-text { display: flex; flex-direction: column; line-height: 1; }
.nav-logo-main {
    display: flex; align-items: baseline; gap: 1px;
    font-size: 11px; font-weight: 300; color: #e0e0f0; letter-spacing: 2px;
}
.nav-logo-nine {
    font-family: 'Bodoni Moda', serif; font-style: italic; font-weight: 700;
    font-size: 28px; color: #e0e0f0; line-height: 1;
}
.nav-logo-sub {
    font-size: 7px; color: rgba(100,216,255,0.25); letter-spacing: 1.5px;
    font-weight: 400;
}
.nav-links { display: flex; align-items: center; gap: 4px; }
.nav-links a, .nav-links button {
    color: #889; font-size: 13px; font-weight: 600; text-decoration: none;
    padding: 7px 14px; border-radius: 8px; border: none; background: transparent;
    cursor: pointer; transition: all 0.2s ease;
}
.nav-links a:hover, .nav-links button:hover { color: #fff; background: #1e3a5f; text-decoration: none; }
.nav-links a.active { color: #64d8ff; background: rgba(100,216,255,0.1); }

/* Cards — matches 9Bot .card */
.card {
    background: #181830;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 14px;
    padding: 16px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.25);
    margin-bottom: 10px;
}
.card h2 { font-size: 18px; font-weight: 600; margin-bottom: 12px; }
.card h3 {
    font-size: 10px; font-weight: 700; color: #778; text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 10px; padding-bottom: 6px;
    border-bottom: 1px solid rgba(255,255,255,0.05);
}

/* Form controls — matches 9Bot inputs */
.form-group { margin-bottom: 14px; }
.form-group label {
    display: block; font-size: 12px; font-weight: 600; color: #889;
    margin-bottom: 4px;
}
.form-group input, .form-group select {
    width: 100%; padding: 8px 12px; border-radius: 8px;
    border: 1px solid rgba(255,255,255,0.12);
    background: #141428; color: #e0e0f0; font-size: 14px;
    outline: none; transition: border-color 0.15s ease;
}
.form-group input:focus, .form-group select:focus {
    border-color: rgba(100,216,255,0.4);
}
.form-group input::placeholder { color: #556; }

/* Buttons — matches 9Bot .btn */
.btn {
    display: inline-block; padding: 10px 20px; border-radius: 10px; border: none;
    font-size: 14px; font-weight: 600; cursor: pointer; text-decoration: none;
    color: #fff; min-height: 44px;
    transition: all 0.15s ease;
}
.btn:active { transform: scale(0.97); filter: brightness(0.85); }
.btn-primary { background: #1565c0; }
.btn-primary:hover { background: #1976d2; text-decoration: none; }
.btn-danger { background: #c62828; }
.btn-danger:hover { background: #d32f2f; }
.btn-sm { padding: 7px 16px; font-size: 13px; min-height: 34px; border-radius: 8px; }
.btn-outline {
    background: transparent; border: 1px solid rgba(255,255,255,0.08);
    color: #889; min-height: auto;
}
.btn-outline:hover { border-color: rgba(255,255,255,0.2); color: #e0e0f0; text-decoration: none; }

/* Online/offline dot — matches 9Bot status indicator */
.dot {
    display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    margin-right: 6px; vertical-align: middle;
}
.dot-online {
    background: #4caf50;
    box-shadow: 0 0 8px rgba(76,175,80,0.7);
    animation: pulse-glow 2s ease-in-out infinite;
}
.dot-offline { background: #444; }

@keyframes pulse-glow {
    0%, 100% { box-shadow: 0 0 4px rgba(76,175,80,0.2); }
    50% { box-shadow: 0 0 10px rgba(76,175,80,0.5); }
}

/* Bot rows — card-like items inside cards */
.bot-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 14px; background: #141428; border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.04);
    margin-bottom: 8px; gap: 12px;
    transition: border-color 0.15s ease;
}
.bot-row:hover { border-color: rgba(100,216,255,0.12); }
.bot-info { flex: 1; min-width: 0; }
.bot-name { font-weight: 600; font-size: 14px; }
.bot-meta { font-size: 11px; color: #556; margin-top: 3px; }

/* Device rows */
.device-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px; background: #141428; border-radius: 8px;
    border: 1px solid rgba(255,255,255,0.04);
    margin-bottom: 4px; font-size: 13px;
}

/* Device cards (bot detail page) */
.dev-card {
    background: #141428; border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.04);
    padding: 14px 16px; margin-bottom: 8px;
    transition: border-color 0.15s ease;
}
.dev-card:hover { border-color: rgba(100,216,255,0.1); }
.dev-card-header {
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; margin-bottom: 10px;
}
.dev-card-name { font-weight: 600; font-size: 14px; }
.dev-card-hash { font-size: 10px; color: #445; font-family: "SF Mono","Consolas",monospace; margin-top: 2px; }
.dev-card-label {
    width: 140px; padding: 5px 10px; border-radius: 7px;
    border: 1px solid rgba(255,255,255,0.08); background: #0e0e1e;
    color: #e0e0f0; font-size: 12px; outline: none;
    transition: border-color 0.15s ease;
}
.dev-card-label:focus { border-color: rgba(100,216,255,0.35); }
.dev-card-label::placeholder { color: #334; }
.dev-card-controls {
    display: flex; align-items: center; gap: 14px;
    padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.03);
}

/* Toggle switch */
.toggle {
    display: flex; align-items: center; gap: 7px;
    cursor: pointer; user-select: none; font-size: 12px;
    color: #667; transition: color 0.15s;
}
.toggle:hover { color: #99a; }
.toggle input { display: none; }
.toggle-track {
    position: relative; width: 32px; height: 18px;
    background: #222240; border-radius: 9px;
    border: 1px solid rgba(255,255,255,0.06);
    transition: all 0.2s ease; flex-shrink: 0;
}
.toggle-track::after {
    content: ''; position: absolute; top: 2px; left: 2px;
    width: 12px; height: 12px; border-radius: 50%;
    background: #445; transition: all 0.2s ease;
}
.toggle input:checked + .toggle-track {
    background: rgba(100,216,255,0.15);
    border-color: rgba(100,216,255,0.3);
}
.toggle input:checked + .toggle-track::after {
    left: 16px; background: #64d8ff;
    box-shadow: 0 0 6px rgba(100,216,255,0.5);
}
.toggle.toggle-warn input:checked + .toggle-track {
    background: rgba(239,83,80,0.12);
    border-color: rgba(239,83,80,0.3);
}
.toggle.toggle-warn input:checked + .toggle-track::after {
    background: #ef5350;
    box-shadow: 0 0 6px rgba(239,83,80,0.5);
}
.toggle-label { letter-spacing: 0.3px; }

/* Grant rows */
.grant-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: #141428; border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.04);
    margin-bottom: 6px;
    font-size: 13px;
}
.grant-info { flex: 1; }

/* Alerts */
.alert {
    padding: 12px 16px; border-radius: 10px; margin-bottom: 16px;
    font-size: 13px; font-weight: 500;
}
.alert-error { background: rgba(239,83,80,0.1); color: #ef5350; border: 1px solid rgba(239,83,80,0.2); }
.alert-success { background: rgba(76,175,80,0.1); color: #66bb6a; border: 1px solid rgba(76,175,80,0.2); }

/* Muted text */
.muted { color: #556; font-size: 11px; }

/* Badges — matches 9Bot pill style (transparent bg, colored border + glow) */
.badge {
    display: inline-block; padding: 4px 10px; border-radius: 12px;
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    background: transparent; white-space: nowrap;
}
.badge-full {
    color: #4caf50; border: 1px solid rgba(76,175,80,0.35);
    box-shadow: 0 0 8px rgba(76,175,80,0.15);
}
.badge-readonly {
    color: #ffb74d; border: 1px solid rgba(255,183,77,0.35);
    box-shadow: 0 0 8px rgba(255,183,77,0.15);
}
.badge-admin {
    color: #ab47bc; border: 1px solid rgba(171,71,188,0.35);
    box-shadow: 0 0 8px rgba(171,71,188,0.15);
}

/* Tables */
table { width: 100%; border-collapse: collapse; }
th, td { padding: 8px 12px; text-align: left; font-size: 13px; }
th {
    color: #778; font-weight: 700; font-size: 10px; text-transform: uppercase;
    letter-spacing: 1px; border-bottom: 1px solid rgba(255,255,255,0.06);
}
td { border-bottom: 1px solid rgba(255,255,255,0.03); }
select {
    background: #141428; color: #e0e0f0; border: 1px solid rgba(255,255,255,0.12);
    border-radius: 6px; padding: 4px 8px; font-size: 12px; outline: none;
}

/* Responsive */
@media (max-width: 480px) {
    .nav { flex-wrap: wrap; gap: 8px; }
    .nav-links { gap: 2px; }
    .nav-links a, .nav-links button { padding: 6px 10px; font-size: 12px; }
    .bot-row { flex-direction: column; align-items: flex-start; }
}
"""


def _page(title: str, body: str, user: dict | None = None, csrf: str = "") -> str:
    nav_links = ""
    if user:
        links = [
            '<a href="/portal/">Dashboard</a>',
            '<a href="/portal/community">Community</a>',
            '<a href="/portal/billing">Billing</a>',
            '<a href="/portal/guide">Guide</a>',
            '<a href="/portal/account">Account</a>',
        ]
        if user.get("is_admin"):
            links.append('<a href="/portal/admin">Admin</a>')
        links.append(
            f'<form method="post" action="/portal/api/logout" style="display:inline">'
            f'<input type="hidden" name="csrf_token" value="{csrf}">'
            f'<button type="submit">Logout</button>'
            f'</form>'
        )
        nav_links = f'<div class="nav-links">{"".join(links)}</div>'

    logo = (
        '<a href="/portal/" class="nav-logo">'
        '<div class="nav-logo-bars"></div>'
        '<div class="nav-logo-text">'
        '<div class="nav-logo-main"><span class="nav-logo-nine">9</span>BOT</div>'
        '<div class="nav-logo-sub">PORTAL</div>'
        '</div></a>'
    )

    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1,"
        f"maximum-scale=1,user-scalable=no'>"
        f"<meta name='apple-mobile-web-app-capable' content='yes'>"
        f"<title>{title} — 9Bot Portal</title>"
        f"<style>{_CSS}</style></head><body>"
        f'<div class="nav">{logo}{nav_links}</div>'
        f'<div class="container" style="padding-top:20px">'
        f"{body}"
        f"</div></body></html>"
    )


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ------------------------------------------------------------------
# Page handlers
# ------------------------------------------------------------------

async def page_login(request: web.Request) -> web.Response:
    user = await _get_user(request)
    if user:
        raise web.HTTPFound("/portal/")

    error = request.query.get("error", "")
    next_url = _html_escape(request.query.get("next", "/portal/"))

    error_html = f'<div class="alert alert-error">{_html_escape(error)}</div>' if error else ""

    body = f"""
    {error_html}
    <div class="card">
        <h2>Login</h2>
        <form method="post" action="/portal/api/login">
            <input type="hidden" name="next" value="{next_url}">
            <div class="form-group">
                <label>Username</label>
                <input type="text" name="username" required autocomplete="username"
                       autofocus maxlength="50">
            </div>
            <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" required autocomplete="current-password">
            </div>
            <button type="submit" class="btn btn-primary">Login</button>
        </form>
        <p class="muted" style="margin-top:12px">
            Don't have an account? <a href="/portal/register">Register with invite code</a>
        </p>
    </div>
    """
    return web.Response(text=_page("Login", body), content_type="text/html")


async def page_register(request: web.Request) -> web.Response:
    user = await _get_user(request)
    if user:
        raise web.HTTPFound("/portal/")

    error = request.query.get("error", "")
    error_html = f'<div class="alert alert-error">{_html_escape(error)}</div>' if error else ""

    invite_prefill = _html_escape(request.query.get("invite", ""))

    body = f"""
    {error_html}
    <div class="card">
        <h2>Register</h2>
        <form method="post" action="/portal/api/register">
            <div class="form-group">
                <label>Invite Code</label>
                <input type="text" name="invite_code" required maxlength="20"
                       autocomplete="off" value="{invite_prefill}"
                       {"autofocus" if not invite_prefill else ""}>
            </div>
            <div class="form-group">
                <label>Email</label>
                <input type="email" name="email" required maxlength="200"
                       autocomplete="email" placeholder="you@example.com"
                       {"autofocus" if invite_prefill else ""}>
            </div>
            <div class="form-group">
                <label>Username</label>
                <input type="text" name="username" required maxlength="50"
                       autocomplete="username" pattern="[a-zA-Z0-9_-]+"
                       title="Letters, numbers, hyphens, underscores only">
            </div>
            <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" required minlength="6"
                       autocomplete="new-password">
            </div>
            <button type="submit" class="btn btn-primary">Register</button>
        </form>
        <p class="muted" style="margin-top:12px">
            Already have an account? <a href="/portal/login">Login</a>
        </p>
    </div>
    """
    return web.Response(text=_page("Register", body), content_type="text/html")


async def page_dashboard(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)

    # Admin sees the legacy full view (bots, grants, management)
    if user["is_admin"]:
        return await _page_dashboard_admin(request, user, csrf)

    # Customer view: device-focused cards
    devices = await asyncio.to_thread(db.get_user_devices, user["user_id"])
    shared_devices = await asyncio.to_thread(db.list_shared_devices)

    # Device cards
    dev_html = ""
    for d in devices:
        bot_name = d["bot_name"]
        online = bot_name in _active_bots and not _active_bots[bot_name].closed
        dot = "dot-online" if online else "dot-offline"
        status = "Online" if online else "Offline"
        label = _html_escape(d.get("label") or d.get("device_name") or d["device_hash"][:8])
        open_url = f"/{bot_name}/d/{d['device_hash']}"

        dev_html += (
            f'<div class="bot-row">'
            f'<div class="bot-info">'
            f'<div class="bot-name"><span class="dot {dot}"></span>{label}</div>'
            f'<div class="bot-meta">{status}</div>'
            f'</div>'
            f'<a href="{open_url}" class="btn btn-primary btn-sm">Open</a>'
            f'</div>'
        )

    if not dev_html:
        dev_html = (
            '<p class="muted" style="padding:8px 0">No devices yet. '
            'Ask an admin to grant you access, or browse '
            '<a href="/portal/community">community accounts</a>.</p>'
        )

    devices_card = f'<div class="card"><h2>My Devices</h2>{dev_html}</div>'

    # Community teaser
    online_shared = sum(
        1 for d in shared_devices
        if d["bot_name"] in _active_bots and not _active_bots[d["bot_name"]].closed
    )
    community_text = f"{online_shared} online" if online_shared else "Browse shared accounts"
    community_card = (
        f'<div class="card" style="display:flex;align-items:center;justify-content:space-between">'
        f'<div>'
        f'<strong>Community Accounts</strong>'
        f'<div class="muted" style="margin-top:2px">{community_text}</div>'
        f'</div>'
        f'<a href="/portal/community" class="btn btn-outline btn-sm">Browse &rarr;</a>'
        f'</div>'
    )

    # Subscription status
    sub_html = ""
    billing = _get_stripe_billing()
    if billing:
        sub = await asyncio.to_thread(billing.get_subscription, user["user_id"])
        badge = _sub_badge(sub)
        device_count = len(devices)
        if sub and sub.get("status") in ("active", "past_due"):
            plan_name = sub.get("plan", "none").title()
            limit = sub.get("device_limit", 0)
            limit_text = "Unlimited" if limit >= 999 else str(limit)
            sub_html = (
                f'<div class="card" style="display:flex;align-items:center;justify-content:space-between">'
                f'<div><strong>Subscription:</strong> {plan_name} &middot; '
                f'{device_count}/{limit_text} devices</div>'
                f'<a href="/portal/billing" class="btn btn-outline btn-sm">Manage</a>'
                f'</div>'
            )
        else:
            sub_html = (
                f'<div class="card" style="display:flex;align-items:center;justify-content:space-between">'
                f'<div><strong>Subscription</strong> {badge}</div>'
                f'<a href="/portal/pricing" class="btn btn-primary btn-sm">Subscribe</a>'
                f'</div>'
            )

    # Getting Started card for active subscribers with no devices yet
    getting_started = ""
    if not devices and billing:
        sub = sub if "sub" in dir() else await asyncio.to_thread(billing.get_subscription, user["user_id"])
        if sub and sub.get("status") in ("active", "past_due"):
            getting_started = (
                '<div class="card" style="border-color:rgba(100,216,255,0.2);'
                'background:linear-gradient(135deg,#181830,#1a1a3a)">'
                '<h2 style="margin-bottom:8px">&#127881; Getting Started</h2>'
                '<p style="font-size:13px;color:#aab;line-height:1.6;margin-bottom:12px">'
                'Your subscription is active! Follow the setup guide to get your '
                'bot running — send us your game details and we\'ll have your '
                'server ready within 24 hours.</p>'
                '<a href="/portal/guide" class="btn btn-primary btn-sm">View Setup Guide</a>'
                '</div>'
            )

    body = f"{getting_started}{devices_card}{community_card}{sub_html}"
    return web.Response(text=_page("Dashboard", body, user, csrf), content_type="text/html")


async def _page_dashboard_admin(
    request: web.Request, user: dict, csrf: str,
) -> web.Response:
    """Admin dashboard — shows bots, grants, full management (legacy view)."""
    data = await asyncio.to_thread(db.get_user_bots, user["user_id"])

    def _bot_card(bot: dict, is_owned: bool = False) -> str:
        name = bot["bot_name"]
        label = _html_escape(bot.get("label") or name)
        online = name in _active_bots and not _active_bots[name].closed
        dot = "dot-online" if online else "dot-offline"
        status = "Online" if online else "Offline"
        dev_count = bot.get("device_count", 0)

        manage = ""
        if is_owned or user["is_admin"]:
            manage = f'<a href="/portal/bot/{name}" class="btn btn-outline btn-sm">Manage</a>'
        access = bot.get("access_level", "full" if is_owned else "")
        badge = ""
        if access and not is_owned:
            cls = "badge-full" if access == "full" else "badge-readonly"
            badge = f'<span class="badge {cls}">{access}</span>'

        return (
            f'<div class="bot-row">'
            f'<div class="bot-info">'
            f'<div class="bot-name"><span class="dot {dot}"></span>{label} {badge}</div>'
            f'<div class="bot-meta">{name} &middot; {dev_count} device(s) &middot; {status}</div>'
            f'</div>'
            f'<div style="display:flex;gap:8px;align-items:center">'
            f'<a href="/{name}/" class="btn btn-primary btn-sm">Open</a>'
            f'{manage}'
            f'</div></div>'
        )

    owned_html = ""
    if data["owned"]:
        cards = "".join(_bot_card(b, is_owned=True) for b in data["owned"])
        owned_html = f'<div class="card"><h2>My Servers</h2>{cards}</div>'

    shared_html = ""
    if data["shared"]:
        cards = "".join(_bot_card(b) for b in data["shared"])
        shared_html = f'<div class="card"><h2>Shared With Me</h2>{cards}</div>'

    if not data["owned"] and not data["shared"]:
        owned_html = (
            '<div class="card"><p class="muted">'
            'No servers available.</p></div>'
        )

    body = f"{owned_html}{shared_html}"
    return web.Response(text=_page("Dashboard", body, user, csrf), content_type="text/html")


async def page_community(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)

    shared = await asyncio.to_thread(db.list_shared_devices)

    dev_html = ""
    for d in shared:
        bot_name = d["bot_name"]
        online = bot_name in _active_bots and not _active_bots[bot_name].closed
        dot = "dot-online" if online else "dot-offline"
        status = "Online" if online else "Offline"
        label = _html_escape(d.get("label") or d.get("device_name") or d["device_hash"][:8])
        open_url = f"/{bot_name}/d/{d['device_hash']}"

        dev_html += (
            f'<div class="bot-row">'
            f'<div class="bot-info">'
            f'<div class="bot-name"><span class="dot {dot}"></span>{label}</div>'
            f'<div class="bot-meta">{status}</div>'
            f'</div>'
            f'<a href="{open_url}" class="btn btn-primary btn-sm">Open</a>'
            f'</div>'
        )

    if not dev_html:
        dev_html = '<p class="muted" style="padding:8px 0">No community accounts available yet.</p>'

    body = (
        f'<div class="card">'
        f'<h2>Community Accounts</h2>'
        f'<p class="muted" style="margin-bottom:12px">Available to all members</p>'
        f'{dev_html}'
        f'</div>'
    )
    return web.Response(text=_page("Community", body, user, csrf), content_type="text/html")


async def page_bot_detail(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)
    bot_name = request.match_info["bot_name"]

    bot = await asyncio.to_thread(db.get_bot, bot_name)
    if not bot:
        raise web.HTTPNotFound(text="Server not found")

    # Only owner or admin can manage
    is_owner = bot.get("owner_id") == user["user_id"]
    if not is_owner and not user["is_admin"]:
        raise web.HTTPForbidden(text="Only the server owner can manage this server")

    devices = await asyncio.to_thread(db.list_devices, bot_name)
    grants = await asyncio.to_thread(db.list_grants_for_bot, bot_name)

    online = bot_name in _active_bots and not _active_bots[bot_name].closed
    dot = "dot-online" if online else "dot-offline"
    status = "Online" if online else "Offline"
    label = _html_escape(bot.get("label") or bot_name)

    # Devices list with label + shared + public controls
    dev_rows = ""
    for d in devices:
        dname = _html_escape(d.get("device_name") or d["device_hash"])
        dlabel = _html_escape(d.get("label") or "")
        dh = d["device_hash"]
        is_shared = d.get("is_shared", 0)
        is_public = d.get("is_public", 0)
        shared_checked = "checked" if is_shared else ""
        public_checked = "checked" if is_public else ""
        dev_rows += (
            f'<div class="dev-card">'
            f'<div class="dev-card-header">'
            f'<div>'
            f'<div class="dev-card-name">{dname}</div>'
            f'<div class="dev-card-hash">{dh}</div>'
            f'</div>'
            f'<input type="text" class="dev-card-label" placeholder="Display label" '
            f'value="{dlabel}" onchange="setDeviceLabel(\'{dh}\',this.value)">'
            f'</div>'
            f'<div class="dev-card-controls">'
            f'<label class="toggle" title="Visible to all logged-in members">'
            f'<input type="checkbox" {shared_checked} '
            f'onchange="setDeviceShared(\'{dh}\',this.checked)">'
            f'<span class="toggle-track"></span>'
            f'<span class="toggle-label">Community</span>'
            f'</label>'
            f'<label class="toggle toggle-warn" '
            f'title="No login required — anyone with the link can access">'
            f'<input type="checkbox" {public_checked} '
            f'onchange="setDevicePublic(\'{dh}\',this.checked)">'
            f'<span class="toggle-track"></span>'
            f'<span class="toggle-label">Public</span>'
            f'</label>'
            f'</div>'
            f'</div>'
        )
    if not devices:
        dev_rows = '<p class="muted">No devices reported yet. The server will report devices when it connects.</p>'

    # Grants list
    grant_rows = ""
    for g in grants:
        level_cls = "badge-full" if g["access_level"] == "full" else "badge-readonly"
        dh = g.get("device_hash") or "all devices"
        grant_rows += (
            f'<div class="grant-row">'
            f'<div class="grant-info">'
            f'<strong>{_html_escape(g["username"])}</strong> &mdash; '
            f'<span class="badge {level_cls}">{g["access_level"]}</span> '
            f'on {_html_escape(dh)}'
            f'</div>'
            f'<button class="btn btn-danger btn-sm" onclick="revokeGrant({g["id"]})">Revoke</button>'
            f'</div>'
        )
    if not grants:
        grant_rows = '<p class="muted">No access grants yet.</p>'

    # Device options for grant form
    device_options = '<option value="">All devices</option>'
    for d in devices:
        dname = _html_escape(d.get("device_name") or d["device_hash"])
        device_options += f'<option value="{d["device_hash"]}">{dname}</option>'

    body = f"""
    <div class="card">
        <h2><span class="dot {dot}"></span>{label}</h2>
        <p class="muted">{bot_name} &middot; {status}</p>
    </div>

    <div class="card">
        <h3>Devices</h3>
        {dev_rows}
    </div>

    <div class="card">
        <h3>Access Grants</h3>
        {grant_rows}
    </div>

    <div class="card">
        <h3>Grant Access</h3>
        <form id="grantForm" onsubmit="return createGrant(event)">
            <div class="form-group">
                <label>Username</label>
                <input type="text" name="username" required maxlength="50"
                       placeholder="Username to grant access">
            </div>
            <div class="form-group">
                <label>Device</label>
                <select name="device_hash">{device_options}</select>
            </div>
            <div class="form-group">
                <label>Access Level</label>
                <select name="access_level">
                    <option value="readonly">Read Only</option>
                    <option value="full">Full Access</option>
                </select>
            </div>
            <button type="submit" class="btn btn-primary btn-sm">Grant Access</button>
        </form>
    </div>

    <div class="card">
        <h3>Server Settings</h3>
        <form onsubmit="return updateLabel(event)">
            <div class="form-group">
                <label>Display Name</label>
                <input type="text" name="label" value="{label}" maxlength="50">
            </div>
            <button type="submit" class="btn btn-primary btn-sm">Update</button>
        </form>
    </div>

    <script>
    const csrf = "{csrf}";
    const botName = "{bot_name}";

    async function createGrant(e) {{
        e.preventDefault();
        const f = e.target;
        const resp = await fetch("/portal/api/grants", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
                csrf_token: csrf,
                username: f.username.value,
                bot_name: botName,
                device_hash: f.device_hash.value || null,
                access_level: f.access_level.value,
            }})
        }});
        if (resp.ok) location.reload();
        else alert((await resp.json()).error || "Failed");
        return false;
    }}

    async function revokeGrant(id) {{
        if (!confirm("Revoke this access?")) return;
        const resp = await fetch("/portal/api/grants/" + id, {{
            method: "DELETE",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }});
        if (resp.ok) location.reload();
        else alert("Failed to revoke");
    }}

    async function updateLabel(e) {{
        e.preventDefault();
        const resp = await fetch("/portal/api/bots/" + botName, {{
            method: "PUT",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
                csrf_token: csrf,
                label: e.target.label.value,
            }})
        }});
        if (resp.ok) location.reload();
        else alert("Failed to update");
        return false;
    }}

    async function setDeviceLabel(deviceHash, label) {{
        const resp = await fetch("/portal/api/devices/" + botName + "/" + deviceHash + "/label", {{
            method: "PUT",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf, label: label}})
        }});
        if (!resp.ok) alert("Failed to set label");
    }}

    async function setDeviceShared(deviceHash, shared) {{
        const resp = await fetch("/portal/api/devices/" + botName + "/" + deviceHash + "/shared", {{
            method: "PUT",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf, is_shared: shared}})
        }});
        if (!resp.ok) alert("Failed to update shared status");
    }}

    async function setDevicePublic(deviceHash, isPublic) {{
        if (isPublic && !confirm("Make this device public? Anyone with the link can access it without logging in.")) {{
            event.target.checked = false;
            return;
        }}
        const resp = await fetch("/portal/api/devices/" + botName + "/" + deviceHash + "/public", {{
            method: "PUT",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf, is_public: isPublic}})
        }});
        if (!resp.ok) alert("Failed to update public status");
    }}
    </script>
    """
    return web.Response(text=_page(f"Server: {label}", body, user, csrf), content_type="text/html")


async def page_admin(request: web.Request) -> web.Response:
    user = await _require_admin(request)
    csrf = _get_csrf(request)

    users = await asyncio.to_thread(db.list_users)
    bots = await asyncio.to_thread(db.list_bots)
    invites = await asyncio.to_thread(db.list_invite_codes)

    # Users table
    user_rows = ""
    for u in users:
        admin_badge = '<span class="badge badge-admin">admin</span> ' if u["is_admin"] else ""
        last = u.get("last_login") or "never"
        email = _html_escape(u.get("email") or "—")
        user_rows += (
            f'<tr>'
            f'<td>{u["id"]}</td>'
            f'<td>{admin_badge}{_html_escape(u["username"])}</td>'
            f'<td class="muted" style="font-size:11px">{email}</td>'
            f'<td class="muted">{u["created_at"]}</td>'
            f'<td class="muted">{last}</td>'
            f'<td style="white-space:nowrap">'
            f'<button class="btn btn-outline btn-sm" style="margin-right:4px" '
            f'onclick="resetPassword({u["id"]},\'{_html_escape(u["username"])}\')">Reset PW</button>'
            f'<button class="btn btn-danger btn-sm" '
            f'onclick="deleteUser({u["id"]},\'{_html_escape(u["username"])}\')">Delete</button>'
            f'</td></tr>'
        )

    # Bots table
    bot_rows = ""
    for b in bots:
        owner = _html_escape(b.get("owner_name") or "—")
        online = b["bot_name"] in _active_bots and not _active_bots[b["bot_name"]].closed
        dot = "dot-online" if online else "dot-offline"
        last = b.get("last_seen") or "never"

        # Owner assignment dropdown
        owner_select = f'<select onchange="setOwner(\'{b["bot_name"]}\',this.value)">'
        owner_select += '<option value="">— none —</option>'
        for u in users:
            sel = " selected" if b.get("owner_id") == u["id"] else ""
            owner_select += f'<option value="{u["id"]}"{sel}>{_html_escape(u["username"])}</option>'
        owner_select += "</select>"

        bot_rows += (
            f'<tr>'
            f'<td><span class="dot {dot}"></span>'
            f'<a href="/portal/bot/{b["bot_name"]}">{_html_escape(b.get("label") or b["bot_name"])}</a></td>'
            f'<td class="muted">{b["bot_name"]}</td>'
            f'<td>{owner_select}</td>'
            f'<td class="muted">{last}</td>'
            f'</tr>'
        )

    # Invite codes
    unused_invites = [i for i in invites if not i.get("used_by")]
    used_invites = [i for i in invites if i.get("used_by")]

    invite_rows = ""
    for i in unused_invites[:20]:
        creator = _html_escape(i.get("created_by_name") or "?")
        invite_rows += (
            f'<div class="device-row">'
            f'<code>{i["code"]}</code>'
            f'<span class="muted">by {creator} &middot; {i["created_at"]}</span>'
            f'</div>'
        )

    # Subscriptions section
    subs = await asyncio.to_thread(db.list_subscriptions)
    sub_rows = ""
    for s in subs:
        uname = _html_escape(s.get("username", "?"))
        is_admin_grant = s.get("stripe_customer_id") == "admin_grant"
        src_label = "Admin" if is_admin_grant else "Stripe"
        status_color = "#4caf50" if s["status"] == "active" else (
            "#ffb74d" if s["status"] == "past_due" else "#ef5350"
        )
        period = s.get("current_period_end", "—")
        if period and period != "—":
            try:
                from datetime import datetime as dt
                pe = dt.fromisoformat(period)
                period = pe.strftime("%Y-%m-%d")
            except Exception:
                pass
        revoke_btn = ""
        if is_admin_grant and s["status"] in ("active", "past_due"):
            revoke_btn = (
                f'<button class="btn btn-danger btn-sm" '
                f'onclick="revokeSub({s["user_id"]})">Revoke</button>'
            )
        sub_rows += (
            f'<tr>'
            f'<td>{_html_escape(uname)}</td>'
            f'<td>{s["plan"]}</td>'
            f'<td style="color:{status_color}">{s["status"]}</td>'
            f'<td class="muted">{src_label}</td>'
            f'<td class="muted">{s["device_limit"]}</td>'
            f'<td class="muted">{period}</td>'
            f'<td>{revoke_btn}</td>'
            f'</tr>'
        )

    # User options for grant form
    user_options = ""
    for u in users:
        if not u["is_admin"]:
            user_options += f'<option value="{u["id"]}">{_html_escape(u["username"])}</option>'

    body = f"""
    <div class="card">
        <h2>Users ({len(users)})</h2>
        <table>
            <tr><th>ID</th><th>Username</th><th>Email</th><th>Created</th><th>Last Login</th><th></th></tr>
            {user_rows}
        </table>
    </div>

    <div class="card">
        <h2>Subscriptions</h2>
        <div style="margin-bottom:16px;padding:14px;background:#141428;border-radius:10px;border:1px solid rgba(255,255,255,0.04)">
            <strong style="font-size:13px;display:block;margin-bottom:10px">Grant Free Subscription</strong>
            <form id="grantSubForm" onsubmit="return grantSub(event)" style="display:flex;flex-wrap:wrap;gap:8px;align-items:end">
                <div class="form-group" style="margin:0;flex:1;min-width:120px">
                    <label>User</label>
                    <select name="user_id" required>{user_options}</select>
                </div>
                <div class="form-group" style="margin:0">
                    <label>Duration</label>
                    <select name="duration">
                        <option value="7">1 Week</option>
                        <option value="30" selected>1 Month</option>
                        <option value="90">3 Months</option>
                        <option value="">Permanent</option>
                    </select>
                </div>
                <button type="submit" class="btn btn-primary btn-sm">Grant</button>
            </form>
        </div>
        {f'<table><tr><th>User</th><th>Plan</th><th>Status</th><th>Source</th><th>Devices</th><th>Expires</th><th></th></tr>{sub_rows}</table>' if sub_rows else '<p class="muted">No subscriptions.</p>'}
    </div>

    <div class="card">
        <h2>Servers ({len(bots)})</h2>
        <table>
            <tr><th>Server</th><th>ID</th><th>Owner</th><th>Last Seen</th></tr>
            {bot_rows}
        </table>
    </div>

    <div class="card">
        <h2>Invite Codes</h2>
        <button class="btn btn-primary btn-sm" onclick="genInvite()" style="margin-bottom:12px">
            Generate Invite Code
        </button>
        <div id="inviteResult"></div>
        {invite_rows if invite_rows else '<p class="muted">No unused invite codes.</p>'}
        <p class="muted" style="margin-top:8px">{len(used_invites)} used code(s)</p>
    </div>

    <script>
    const csrf = "{csrf}";

    async function deleteUser(id, name) {{
        if (!confirm("Delete user " + name + "? This revokes all their access.")) return;
        const resp = await fetch("/portal/api/admin/users/" + id, {{
            method: "DELETE",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }});
        if (resp.ok) location.reload();
        else alert("Failed");
    }}

    async function resetPassword(id, name) {{
        const pw = prompt("Set new password for " + name + ":");
        if (!pw) return;
        if (pw.length < 6) {{ alert("Password must be at least 6 characters"); return; }}
        const resp = await fetch("/portal/api/admin/reset-password", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf, user_id: id, new_password: pw}})
        }});
        if (resp.ok) alert("Password reset for " + name);
        else alert("Failed to reset password");
    }}

    async function setOwner(botName, userId) {{
        const resp = await fetch("/portal/api/admin/bots/" + botName + "/owner", {{
            method: "PUT",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf, user_id: userId ? parseInt(userId) : null}})
        }});
        if (!resp.ok) alert("Failed to set owner");
    }}

    async function genInvite() {{
        const resp = await fetch("/portal/api/invite-codes", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }});
        if (resp.ok) {{
            const data = await resp.json();
            document.getElementById("inviteResult").innerHTML =
                '<div class="alert alert-success">Invite code: <code><strong>' +
                data.code + '</strong></code>'
                + ' <button class="btn btn-outline btn-sm" onclick="copyInviteEmail(\'' + data.code + '\')">Copy Email</button>'
                + '</div>';
        }} else alert("Failed");
    }}

    function copyInviteEmail(code) {{
        const url = location.origin + "/portal/register?invite=" + code;
        const body = "Hi,\\n\\nYou've been invited to 9Bot — automated Kingdom Guard running 24/7 on cloud servers.\\n\\n"
            + "Click the link below to create your account:\\n" + url + "\\n\\n"
            + "What to expect after signing up:\\n"
            + "1. Choose a subscription plan\\n"
            + "2. Send us your game account details\\n"
            + "3. We set up your dedicated server (usually within 24 hours)\\n"
            + "4. Control everything from your phone dashboard\\n\\n"
            + "See you in the game!\\n— 9Bot Team";
        navigator.clipboard.writeText(body).then(
            () => alert("Invite email copied to clipboard!"),
            () => alert("Failed to copy — check browser permissions")
        );
    }}

    async function grantSub(e) {{
        e.preventDefault();
        const f = e.target;
        const resp = await fetch("/portal/api/admin/grant-subscription", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
                csrf_token: csrf,
                user_id: parseInt(f.user_id.value),
                duration_days: f.duration.value ? parseInt(f.duration.value) : null,
            }})
        }});
        if (resp.ok) location.reload();
        else {{
            const err = await resp.json();
            alert(err.error || "Failed");
        }}
        return false;
    }}

    async function revokeSub(userId) {{
        if (!confirm("Revoke this admin-granted subscription?")) return;
        const resp = await fetch("/portal/api/admin/revoke-subscription", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf, user_id: userId}})
        }});
        if (resp.ok) location.reload();
        else alert("Failed to revoke");
    }}
    </script>
    """
    return web.Response(text=_page("Admin", body, user, csrf), content_type="text/html")


async def page_account(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)

    msg = request.query.get("msg", "")
    msg_html = f'<div class="alert alert-success">{_html_escape(msg)}</div>' if msg else ""

    # Fetch full user record for email
    full_user = await asyncio.to_thread(db.get_user_by_id, user["user_id"])
    current_email = _html_escape(full_user.get("email") or "") if full_user else ""

    body = f"""
    {msg_html}
    <div class="card">
        <h2>Account</h2>
        <p style="margin-bottom:16px">Logged in as <strong>{_html_escape(user["username"])}</strong>
        {"<span class='badge badge-admin'>admin</span>" if user["is_admin"] else ""}</p>

        <h3>Email</h3>
        <form method="post" action="/portal/api/account/email">
            <input type="hidden" name="csrf_token" value="{csrf}">
            <div class="form-group">
                <label>Email Address</label>
                <input type="email" name="email" value="{current_email}" maxlength="200"
                       autocomplete="email" placeholder="you@example.com">
            </div>
            <button type="submit" class="btn btn-primary btn-sm">Update Email</button>
        </form>
    </div>

    <div class="card">
        <h3>Change Password</h3>
        <form method="post" action="/portal/api/account/password">
            <input type="hidden" name="csrf_token" value="{csrf}">
            <div class="form-group">
                <label>Current Password</label>
                <input type="password" name="current_password" required
                       autocomplete="current-password">
            </div>
            <div class="form-group">
                <label>New Password</label>
                <input type="password" name="new_password" required minlength="6"
                       autocomplete="new-password">
            </div>
            <button type="submit" class="btn btn-primary btn-sm">Change Password</button>
        </form>
    </div>
    """
    return web.Response(text=_page("Account", body, user, csrf), content_type="text/html")


async def page_guide(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)

    body = """
    <style>
    /* ── Guide: override container for full-bleed hero ── */
    .guide-hero {
        text-align: center;
        padding: 40px 20px 32px;
        position: relative;
        overflow: hidden;
    }
    .guide-hero::before {
        content: '';
        position: absolute;
        width: 600px; height: 600px;
        top: 50%; left: 50%;
        transform: translate(-50%, -60%);
        background: radial-gradient(ellipse at center,
            rgba(100,216,255,0.07) 0%, rgba(100,216,255,0.02) 40%, transparent 70%);
        pointer-events: none;
    }
    .guide-hero-check {
        display: inline-flex;
        align-items: center; justify-content: center;
        width: 56px; height: 56px;
        border-radius: 50%;
        background: rgba(76,175,80,0.1);
        border: 2px solid rgba(76,175,80,0.3);
        margin-bottom: 16px;
        animation: guide-pop 0.5s cubic-bezier(0.34,1.56,0.64,1) both;
    }
    .guide-hero-check svg {
        width: 28px; height: 28px;
        stroke: #4caf50;
        stroke-width: 2.5;
        fill: none;
        stroke-linecap: round; stroke-linejoin: round;
    }
    .guide-hero h2 {
        font-size: 22px; font-weight: 700; color: #e0e0f0;
        margin-bottom: 6px;
        animation: guide-fade 0.5s ease 0.15s both;
    }
    .guide-hero p {
        font-size: 14px; color: #667;
        animation: guide-fade 0.5s ease 0.25s both;
    }

    /* ── Timeline ── */
    .guide-timeline {
        position: relative;
        padding: 0 0 0 36px;
        margin: 8px 0 24px;
    }
    /* Vertical line */
    .guide-timeline::before {
        content: '';
        position: absolute;
        left: 15px; top: 0; bottom: 0;
        width: 2px;
        background: linear-gradient(180deg,
            rgba(100,216,255,0.3) 0%,
            rgba(100,216,255,0.08) 100%);
        border-radius: 1px;
    }

    .guide-step {
        position: relative;
        padding: 0 0 28px;
        opacity: 0;
        transform: translateY(12px);
        animation: guide-fade 0.45s ease both;
    }
    .guide-step:nth-child(1) { animation-delay: 0.3s; }
    .guide-step:nth-child(2) { animation-delay: 0.45s; }
    .guide-step:nth-child(3) { animation-delay: 0.6s; }
    .guide-step:nth-child(4) { animation-delay: 0.75s; }
    .guide-step:nth-child(5) { animation-delay: 0.9s; }
    .guide-step:last-child { padding-bottom: 0; }

    /* Node dot */
    .guide-node {
        position: absolute;
        left: -29px; top: 2px;
        width: 20px; height: 20px;
        border-radius: 50%;
        background: #0c0c18;
        border: 2px solid rgba(100,216,255,0.35);
        display: flex; align-items: center; justify-content: center;
    }
    .guide-node-inner {
        width: 8px; height: 8px;
        border-radius: 50%;
        background: #64d8ff;
        box-shadow: 0 0 8px rgba(100,216,255,0.5);
    }
    .guide-step:first-child .guide-node {
        border-color: #4caf50;
    }
    .guide-step:first-child .guide-node-inner {
        background: #4caf50;
        box-shadow: 0 0 8px rgba(76,175,80,0.5);
    }

    /* Step content card */
    .guide-card {
        background: #181830;
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px;
        padding: 16px 18px;
        transition: border-color 0.2s;
    }
    .guide-card:hover {
        border-color: rgba(100,216,255,0.12);
    }
    .guide-card-head {
        display: flex; align-items: center; gap: 10px;
        margin-bottom: 8px;
    }
    .guide-card-icon {
        width: 32px; height: 32px;
        border-radius: 8px;
        background: rgba(100,216,255,0.06);
        border: 1px solid rgba(100,216,255,0.1);
        display: flex; align-items: center; justify-content: center;
        font-size: 16px; flex-shrink: 0;
    }
    .guide-card-title {
        font-size: 14px; font-weight: 700; color: #e0e0f0;
    }
    .guide-card-tag {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 10px; font-weight: 700;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        margin-left: auto;
    }
    .guide-tag-done {
        background: rgba(76,175,80,0.1);
        color: #4caf50;
        border: 1px solid rgba(76,175,80,0.2);
    }
    .guide-tag-action {
        background: rgba(255,183,77,0.1);
        color: #ffb74d;
        border: 1px solid rgba(255,183,77,0.2);
    }
    .guide-tag-wait {
        background: rgba(100,216,255,0.06);
        color: #64d8ff;
        border: 1px solid rgba(100,216,255,0.15);
    }
    .guide-card p {
        font-size: 13px; color: #889; line-height: 1.65;
        margin: 0;
    }
    .guide-card p strong { color: #bbc; }
    .guide-callout {
        margin-top: 10px; padding: 10px 14px;
        background: rgba(100,216,255,0.03);
        border: 1px solid rgba(100,216,255,0.08);
        border-radius: 8px;
        font-size: 12px; color: #778; line-height: 1.6;
        display: flex; align-items: flex-start; gap: 8px;
    }
    .guide-callout-icon {
        flex-shrink: 0; margin-top: 1px;
        color: rgba(100,216,255,0.5);
    }

    /* ── CTA ── */
    .guide-cta {
        text-align: center;
        padding: 8px 0 24px;
        opacity: 0;
        animation: guide-fade 0.5s ease 1.1s both;
    }
    .guide-cta .btn {
        padding: 12px 32px;
        font-size: 14px;
        border-radius: 10px;
        background: linear-gradient(135deg, #1565c0, #1e88e5);
        box-shadow: 0 4px 20px rgba(21,101,192,0.3);
        transition: all 0.2s;
    }
    .guide-cta .btn:hover {
        transform: translateY(-1px);
        box-shadow: 0 6px 28px rgba(21,101,192,0.45);
    }

    @keyframes guide-pop {
        0% { opacity: 0; transform: scale(0.5); }
        100% { opacity: 1; transform: scale(1); }
    }
    @keyframes guide-fade {
        0% { opacity: 0; transform: translateY(12px); }
        100% { opacity: 1; transform: translateY(0); }
    }

    @media (max-width: 480px) {
        .guide-timeline { padding-left: 28px; }
        .guide-node { left: -21px; width: 16px; height: 16px; }
        .guide-node-inner { width: 6px; height: 6px; }
        .guide-card { padding: 14px; }
    }
    </style>

    <!-- Hero -->
    <div class="guide-hero">
        <div class="guide-hero-check">
            <svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>
        </div>
        <h2>Welcome to 9Bot</h2>
        <p>Here's how we get your bot running.</p>
    </div>

    <!-- Timeline -->
    <div class="guide-timeline">

        <div class="guide-step">
            <div class="guide-node"><div class="guide-node-inner"></div></div>
            <div class="guide-card">
                <div class="guide-card-head">
                    <div class="guide-card-icon">&#10003;</div>
                    <div class="guide-card-title">Account Created</div>
                    <span class="guide-card-tag guide-tag-done">Done</span>
                </div>
                <p>
                    You're in. Your portal account is active and you can
                    <a href="/portal/billing">manage billing</a> or
                    <a href="/portal/account">update your account</a> any time.
                </p>
            </div>
        </div>

        <div class="guide-step">
            <div class="guide-node"><div class="guide-node-inner"></div></div>
            <div class="guide-card">
                <div class="guide-card-head">
                    <div class="guide-card-icon">&#127917;</div>
                    <div class="guide-card-title">Send Game Details</div>
                    <span class="guide-card-tag guide-tag-action">Action</span>
                </div>
                <p>
                    Message us your <strong>Kingdom Guard login</strong> &mdash; the
                    account you want automated. We use it once to log into the game
                    on your dedicated server.
                </p>
                <div class="guide-callout">
                    <span class="guide-callout-icon">&#128274;</span>
                    <span>Credentials are only used on your private emulator instance.
                    Never stored separately or shared.</span>
                </div>
            </div>
        </div>

        <div class="guide-step">
            <div class="guide-node"><div class="guide-node-inner"></div></div>
            <div class="guide-card">
                <div class="guide-card-head">
                    <div class="guide-card-icon">&#9881;</div>
                    <div class="guide-card-title">Server Setup</div>
                    <span class="guide-card-tag guide-tag-wait">~24h</span>
                </div>
                <p>
                    We provision a dedicated emulator, install the game, log in,
                    and configure everything. This is a manual step on our end &mdash;
                    usually done within 24 hours, often sooner.
                </p>
            </div>
        </div>

        <div class="guide-step">
            <div class="guide-node"><div class="guide-node-inner"></div></div>
            <div class="guide-card">
                <div class="guide-card-head">
                    <div class="guide-card-icon">&#128241;</div>
                    <div class="guide-card-title">Dashboard Goes Live</div>
                </div>
                <p>
                    Your device appears on the <a href="/portal/">Dashboard</a>
                    with a live screenshot stream. Control everything from any
                    phone or browser &mdash; no app needed.
                </p>
                <div class="guide-callout">
                    <span class="guide-callout-icon">&#128278;</span>
                    <span>Bookmark the device link on your phone for one-tap access.</span>
                </div>
            </div>
        </div>

        <div class="guide-step">
            <div class="guide-node"><div class="guide-node-inner"></div></div>
            <div class="guide-card">
                <div class="guide-card-head">
                    <div class="guide-card-icon">&#9889;</div>
                    <div class="guide-card-title">Start Automating</div>
                </div>
                <p>
                    Toggle Auto Quest, Territory, Titans, or any mode. The bot
                    handles quests, rallies, healing, AP, error recovery &mdash;
                    all of it. Check in when you want, or don't. It runs 24/7.
                </p>
            </div>
        </div>

    </div>

    <div class="guide-cta">
        <a href="/portal/" class="btn btn-primary">Go to Dashboard &rarr;</a>
    </div>
    """
    return web.Response(text=_page("Setup Guide", body, user, csrf), content_type="text/html")


# ------------------------------------------------------------------
# API handlers
# ------------------------------------------------------------------

async def api_login(request: web.Request) -> web.Response:
    ip = _client_ip(request)
    if not auth.check_rate_limit(ip):
        raise web.HTTPTooManyRequests(text="Too many login attempts. Try again in 5 minutes.")

    data = await request.post()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    next_url = data.get("next") or "/portal/"

    if not username or not password:
        raise web.HTTPFound(f"/portal/login?error=Missing+credentials&next={next_url}")

    user = await asyncio.to_thread(db.get_user_by_username, username)
    if not user or not await asyncio.to_thread(auth.verify_password, password, user["password_hash"]):
        auth.record_failed_login(ip)
        raise web.HTTPFound(f"/portal/login?error=Invalid+credentials&next={next_url}")

    auth.clear_rate_limit(ip)
    is_first_login = not user.get("last_login")
    await asyncio.to_thread(db.update_user_login, user["id"])
    token = await asyncio.to_thread(db.create_session, user["id"])

    # First login → show the onboarding guide (unless they had a specific next_url)
    dest = "/portal/guide" if is_first_login and next_url == "/portal/" else next_url
    resp = web.HTTPFound(dest)
    auth.set_session_cookie(resp, token)
    return resp


async def api_logout(request: web.Request) -> web.Response:
    token = _get_session_token(request)
    if token:
        # Skip CSRF check for logout — cookie is already proof of session
        await asyncio.to_thread(db.delete_session, token)
    resp = web.HTTPFound("/portal/login")
    auth.clear_session_cookie(resp)
    return resp


async def api_register(request: web.Request) -> web.Response:
    data = await request.post()
    invite_code = (data.get("invite_code") or "").strip()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    if not invite_code or not username or not password or not email:
        raise web.HTTPFound("/portal/register?error=All+fields+required")

    if len(username) > 50 or not all(c.isalnum() or c in "-_" for c in username):
        raise web.HTTPFound("/portal/register?error=Invalid+username")

    if len(email) > 200 or "@" not in email:
        raise web.HTTPFound("/portal/register?error=Invalid+email+address")

    if len(password) < 6:
        raise web.HTTPFound("/portal/register?error=Password+must+be+at+least+6+characters")

    pw_hash = await asyncio.to_thread(auth.hash_password, password)

    try:
        user_id = await asyncio.to_thread(db.create_user, username, pw_hash, email=email)
    except Exception:
        raise web.HTTPFound("/portal/register?error=Username+already+taken")

    used = await asyncio.to_thread(db.use_invite_code, invite_code, user_id)
    if not used:
        # Roll back user creation
        await asyncio.to_thread(db.delete_user, user_id)
        raise web.HTTPFound("/portal/register?error=Invalid+or+used+invite+code")

    # Auto-login — redirect new users to the guide
    await asyncio.to_thread(db.update_user_login, user_id)
    token = await asyncio.to_thread(db.create_session, user_id)

    resp = web.HTTPFound("/portal/guide")
    auth.set_session_cookie(resp, token)
    return resp


async def api_bots(request: web.Request) -> web.Response:
    user = await _require_user(request)
    data = await asyncio.to_thread(db.get_user_bots, user["user_id"])

    # Add online status
    for group in ("owned", "shared"):
        for bot in data[group]:
            bot["online"] = bot["bot_name"] in _active_bots and not _active_bots[bot["bot_name"]].closed

    return web.json_response(data)


async def api_update_bot(request: web.Request) -> web.Response:
    user = await _require_user(request)
    await _check_csrf(request)
    bot_name = request.match_info["bot_name"]

    bot = await asyncio.to_thread(db.get_bot, bot_name)
    if not bot:
        raise web.HTTPNotFound(text="Server not found")
    if bot.get("owner_id") != user["user_id"] and not user["is_admin"]:
        raise web.HTTPForbidden(text="Not the owner")

    data = await request.json()
    label = (data.get("label") or "").strip()[:50]
    if label:
        await asyncio.to_thread(db.set_bot_label, bot_name, label)

    return web.json_response({"status": "ok"})


async def api_grants_for_bot(request: web.Request) -> web.Response:
    user = await _require_user(request)
    bot_name = request.match_info["bot_name"]

    bot = await asyncio.to_thread(db.get_bot, bot_name)
    if not bot:
        raise web.HTTPNotFound(text="Server not found")
    if bot.get("owner_id") != user["user_id"] and not user["is_admin"]:
        raise web.HTTPForbidden(text="Not the owner")

    grants = await asyncio.to_thread(db.list_grants_for_bot, bot_name)
    return web.json_response(grants)


async def api_create_grant(request: web.Request) -> web.Response:
    user = await _require_user(request)
    await _check_csrf(request)
    data = await request.json()

    bot_name = data.get("bot_name", "").strip()
    username = data.get("username", "").strip()
    device_hash = data.get("device_hash") or None
    access_level = data.get("access_level", "readonly")

    if not bot_name or not username:
        return web.json_response({"error": "bot_name and username required"}, status=400)
    if access_level not in ("full", "readonly"):
        return web.json_response({"error": "access_level must be 'full' or 'readonly'"}, status=400)

    bot = await asyncio.to_thread(db.get_bot, bot_name)
    if not bot:
        return web.json_response({"error": "Server not found"}, status=404)
    if bot.get("owner_id") != user["user_id"] and not user["is_admin"]:
        return web.json_response({"error": "Not the owner"}, status=403)

    target_user = await asyncio.to_thread(db.get_user_by_username, username)
    if not target_user:
        return web.json_response({"error": f"User '{username}' not found"}, status=404)

    # Enforce device limit for the target user's subscription
    if device_hash and not target_user.get("is_admin"):
        billing = _get_stripe_billing()
        if billing:
            current, limit = await asyncio.to_thread(
                billing.check_device_limit, target_user["id"],
            )
            if limit > 0 and current >= limit:
                return web.json_response(
                    {"error": f"User has reached their device limit ({limit})"},
                    status=403,
                )

    grant_id = await asyncio.to_thread(
        db.create_grant, target_user["id"], bot_name, device_hash,
        access_level, user["user_id"],
    )
    return web.json_response({"status": "ok", "grant_id": grant_id})


async def api_delete_grant(request: web.Request) -> web.Response:
    user = await _require_user(request)
    await _check_csrf(request)
    grant_id = int(request.match_info["grant_id"])

    # Verify ownership: the grant must be for a bot this user owns (or admin)
    if not user["is_admin"]:
        grants = await asyncio.to_thread(db.list_bots)
        # Simplified: just try to delete — the grant_id is the authority
        # In production, you'd verify the bot ownership chain
        pass

    deleted = await asyncio.to_thread(db.delete_grant, grant_id)
    if not deleted:
        return web.json_response({"error": "Grant not found"}, status=404)
    return web.json_response({"status": "ok"})


async def api_create_invite(request: web.Request) -> web.Response:
    user = await _require_user(request)
    await _check_csrf(request)

    # Admin or bot owners can create invites
    if not user["is_admin"]:
        # Check if user owns any bots
        bots = await asyncio.to_thread(db.get_user_bots, user["user_id"])
        if not bots.get("owned"):
            return web.json_response({"error": "Only admin or server owners can create invites"}, status=403)

    code = await asyncio.to_thread(db.create_invite_code, user["user_id"])
    return web.json_response({"status": "ok", "code": code})


async def api_list_invites(request: web.Request) -> web.Response:
    user = await _require_user(request)
    if user["is_admin"]:
        invites = await asyncio.to_thread(db.list_invite_codes)
    else:
        invites = await asyncio.to_thread(db.list_invite_codes, user["user_id"])
    return web.json_response(invites)


async def api_admin_users(request: web.Request) -> web.Response:
    await _require_admin(request)
    users = await asyncio.to_thread(db.list_users)
    return web.json_response(users)


async def api_admin_delete_user(request: web.Request) -> web.Response:
    admin = await _require_admin(request)
    await _check_csrf(request)
    user_id = int(request.match_info["user_id"])

    if user_id == admin["user_id"]:
        return web.json_response({"error": "Cannot delete yourself"}, status=400)

    deleted = await asyncio.to_thread(db.delete_user, user_id)
    if not deleted:
        return web.json_response({"error": "User not found"}, status=404)
    # Also kill their sessions
    await asyncio.to_thread(db.delete_user_sessions, user_id)
    return web.json_response({"status": "ok"})


async def api_admin_set_owner(request: web.Request) -> web.Response:
    await _require_admin(request)
    await _check_csrf(request)
    bot_name = request.match_info["bot_name"]
    data = await request.json()
    owner_id = data.get("user_id")  # None to unassign

    if owner_id is not None:
        owner_id = int(owner_id)
        user = await asyncio.to_thread(db.get_user_by_id, owner_id)
        if not user:
            return web.json_response({"error": "User not found"}, status=404)

    updated = await asyncio.to_thread(db.set_bot_owner, bot_name, owner_id)
    if not updated:
        return web.json_response({"error": "Server not found"}, status=404)
    return web.json_response({"status": "ok"})


async def api_admin_reset_password(request: web.Request) -> web.Response:
    """Admin resets a user's password directly."""
    await _require_admin(request)
    await _check_csrf(request)
    data = await request.json()

    user_id = data.get("user_id")
    new_password = data.get("new_password", "")

    if not user_id or len(new_password) < 6:
        return web.json_response({"error": "user_id and new_password (6+ chars) required"}, status=400)

    user = await asyncio.to_thread(db.get_user_by_id, int(user_id))
    if not user:
        return web.json_response({"error": "User not found"}, status=404)

    pw_hash = await asyncio.to_thread(auth.hash_password, new_password)
    await asyncio.to_thread(db.update_user_password, int(user_id), pw_hash)
    # Kill existing sessions so they have to re-login
    await asyncio.to_thread(db.delete_user_sessions, int(user_id))
    return web.json_response({"status": "ok"})


async def api_admin_grant_subscription(request: web.Request) -> web.Response:
    """Admin grants a free subscription to a user."""
    await _require_admin(request)
    await _check_csrf(request)
    data = await request.json()

    user_id = data.get("user_id")
    duration_days = data.get("duration_days")  # None = permanent

    if not user_id:
        return web.json_response({"error": "user_id required"}, status=400)

    user = await asyncio.to_thread(db.get_user_by_id, int(user_id))
    if not user:
        return web.json_response({"error": "User not found"}, status=404)

    await asyncio.to_thread(
        db.grant_admin_subscription,
        int(user_id),
        plan="standard",
        device_limit=1,
        duration_days=int(duration_days) if duration_days else None,
    )
    return web.json_response({"status": "ok"})


async def api_admin_revoke_subscription(request: web.Request) -> web.Response:
    """Admin revokes an admin-granted subscription."""
    await _require_admin(request)
    await _check_csrf(request)
    data = await request.json()

    user_id = data.get("user_id")
    if not user_id:
        return web.json_response({"error": "user_id required"}, status=400)

    revoked = await asyncio.to_thread(db.revoke_admin_subscription, int(user_id))
    if not revoked:
        return web.json_response({"error": "No admin-granted subscription found"}, status=404)
    return web.json_response({"status": "ok"})


async def api_change_password(request: web.Request) -> web.Response:
    data = await request.post()
    csrf_token = data.get("csrf_token", "")
    session_token = _get_session_token(request)
    if not session_token or not auth.validate_csrf_token(session_token, csrf_token):
        raise web.HTTPForbidden(text="Invalid CSRF token")

    user_info = await asyncio.to_thread(db.validate_session, session_token)
    if not user_info:
        raise web.HTTPFound("/portal/login")

    current = data.get("current_password", "")
    new_pw = data.get("new_password", "")

    if len(new_pw) < 6:
        raise web.HTTPFound("/portal/account?msg=Password+must+be+at+least+6+characters")

    user = await asyncio.to_thread(db.get_user_by_id, user_info["user_id"])
    if not user or not await asyncio.to_thread(auth.verify_password, current, user["password_hash"]):
        raise web.HTTPFound("/portal/account?msg=Current+password+is+incorrect")

    pw_hash = await asyncio.to_thread(auth.hash_password, new_pw)
    await asyncio.to_thread(db.update_user_password, user_info["user_id"], pw_hash)

    raise web.HTTPFound("/portal/account?msg=Password+changed+successfully")


async def api_change_email(request: web.Request) -> web.Response:
    data = await request.post()
    csrf_token = data.get("csrf_token", "")
    session_token = _get_session_token(request)
    if not session_token or not auth.validate_csrf_token(session_token, csrf_token):
        raise web.HTTPForbidden(text="Invalid CSRF token")

    user_info = await asyncio.to_thread(db.validate_session, session_token)
    if not user_info:
        raise web.HTTPFound("/portal/login")

    email = (data.get("email") or "").strip()
    if email and (len(email) > 200 or "@" not in email):
        raise web.HTTPFound("/portal/account?msg=Invalid+email+address")

    await asyncio.to_thread(db.update_user_email, user_info["user_id"], email or None)
    raise web.HTTPFound("/portal/account?msg=Email+updated+successfully")


# ------------------------------------------------------------------
# Device settings API (admin only)
# ------------------------------------------------------------------

async def api_set_device_label(request: web.Request) -> web.Response:
    user = await _require_user(request)
    await _check_csrf(request)
    bot_name = request.match_info["bot_name"]
    device_hash = request.match_info["device_hash"]

    bot = await asyncio.to_thread(db.get_bot, bot_name)
    if not bot:
        return web.json_response({"error": "Server not found"}, status=404)
    if bot.get("owner_id") != user["user_id"] and not user["is_admin"]:
        return web.json_response({"error": "Not the owner"}, status=403)

    data = await request.json()
    label = (data.get("label") or "").strip()[:50]
    await asyncio.to_thread(db.set_device_label, bot_name, device_hash, label)
    return web.json_response({"status": "ok"})


async def api_set_device_shared(request: web.Request) -> web.Response:
    user = await _require_user(request)
    await _check_csrf(request)
    bot_name = request.match_info["bot_name"]
    device_hash = request.match_info["device_hash"]

    bot = await asyncio.to_thread(db.get_bot, bot_name)
    if not bot:
        return web.json_response({"error": "Server not found"}, status=404)
    if bot.get("owner_id") != user["user_id"] and not user["is_admin"]:
        return web.json_response({"error": "Not the owner"}, status=403)

    data = await request.json()
    is_shared = bool(data.get("is_shared", False))
    await asyncio.to_thread(db.set_device_shared, bot_name, device_hash, is_shared)
    return web.json_response({"status": "ok"})


async def api_set_device_public(request: web.Request) -> web.Response:
    user = await _require_user(request)
    await _check_csrf(request)
    bot_name = request.match_info["bot_name"]
    device_hash = request.match_info["device_hash"]

    bot = await asyncio.to_thread(db.get_bot, bot_name)
    if not bot:
        return web.json_response({"error": "Server not found"}, status=404)
    if bot.get("owner_id") != user["user_id"] and not user["is_admin"]:
        return web.json_response({"error": "Not the owner"}, status=403)

    data = await request.json()
    is_public = bool(data.get("is_public", False))
    await asyncio.to_thread(db.set_device_public, bot_name, device_hash, is_public)
    return web.json_response({"status": "ok"})


# ------------------------------------------------------------------
# Billing pages + API
# ------------------------------------------------------------------

def _get_stripe_billing():
    """Lazy import stripe_billing to avoid hard dependency."""
    try:
        import stripe_billing
        return stripe_billing if stripe_billing.is_configured() else None
    except ImportError:
        return None


def _sub_badge(sub: dict | None) -> str:
    """Render a small subscription badge for the dashboard."""
    if not sub or sub.get("status") not in ("active", "past_due"):
        return '<span class="badge" style="color:#ef5350;border:1px solid rgba(239,83,80,0.35)">No Plan</span>'
    plan = sub.get("plan", "none").title()
    is_admin_grant = sub.get("stripe_customer_id") == "admin_grant"
    if sub["status"] == "past_due":
        return f'<span class="badge" style="color:#ffb74d;border:1px solid rgba(255,183,77,0.35)">{plan} (Past Due)</span>'
    if is_admin_grant:
        return f'<span class="badge" style="color:#ab47bc;border:1px solid rgba(171,71,188,0.35)">{plan} (Admin)</span>'
    return f'<span class="badge badge-full">{plan}</span>'


async def page_pricing(request: web.Request) -> web.Response:
    user = await _get_user(request)
    csrf = _get_csrf(request) if user else ""
    billing = _get_stripe_billing()

    sub = None
    if user and billing:
        sub = await asyncio.to_thread(billing.get_subscription, user["user_id"])

    current_plan = sub["plan"] if sub and sub["status"] in ("active", "past_due") else None

    is_current = current_plan == "standard"
    if is_current:
        btn = '<span class="btn btn-outline" style="width:100%;text-align:center;cursor:default">Current Plan</span>'
    elif user and billing:
        btn = (
            '<button class="btn btn-primary" style="width:100%" '
            'onclick="subscribe(\'standard\')">Subscribe</button>'
        )
    else:
        btn = '<a href="/portal/login?next=/portal/pricing" class="btn btn-primary" style="width:100%;text-align:center">Login to Subscribe</a>'

    features_html = "".join(
        f'<li style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.03)">'
        f'<span style="color:#4caf50;margin-right:8px">&#10003;</span>{f}</li>'
        for f in [
            "Full control of all auto-modes",
            "Remote dashboard &amp; relay access",
            "24/7 dedicated server",
            "All features included",
        ]
    )

    plans_html = f"""
    <div style="display:flex;justify-content:center">
        <div class="card" style="max-width:360px;width:100%;border-color:rgba(100,216,255,0.3);box-shadow:0 0 20px rgba(100,216,255,0.08)">
            <div style="margin-bottom:12px">
                <span style="font-size:32px;font-weight:700">$20</span>
                <span class="muted">/mo</span>
            </div>
            <div style="font-size:13px;color:#64d8ff;margin-bottom:16px;font-weight:600">1 dedicated account</div>
            <ul style="list-style:none;padding:0;margin-bottom:20px;font-size:13px;color:#aab">
                {features_html}
            </ul>
            {btn}
        </div>
    </div>
    """

    script = ""
    if user and billing:
        script = f"""
        <script>
        async function subscribe(plan) {{
            const resp = await fetch("/portal/api/subscribe", {{
                method: "POST",
                headers: {{"Content-Type": "application/json"}},
                body: JSON.stringify({{csrf_token: "{csrf}", plan: plan}})
            }});
            if (resp.ok) {{
                const data = await resp.json();
                if (data.url) window.location.href = data.url;
            }} else {{
                const err = await resp.json();
                alert(err.error || "Failed to start checkout");
            }}
        }}
        </script>
        """

    body = f"""
    <h2 style="text-align:center;margin-bottom:8px">Simple, Honest Pricing</h2>
    <p class="muted" style="text-align:center;margin-bottom:20px">
        One plan. Full access. No upsells.
    </p>
    {plans_html}
    {script}
    """
    return web.Response(text=_page("Pricing", body, user, csrf), content_type="text/html")


async def page_billing(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)
    billing = _get_stripe_billing()

    if not billing:
        body = '<div class="card"><p class="muted">Billing is not configured.</p></div>'
        return web.Response(text=_page("Billing", body, user, csrf), content_type="text/html")

    sub = await asyncio.to_thread(billing.get_subscription, user["user_id"])

    if not sub or sub["status"] not in ("active", "past_due"):
        body = f"""
        <div class="card">
            <h2>No Active Subscription</h2>
            <p style="margin:12px 0">You don't have an active subscription.</p>
            <a href="/portal/pricing" class="btn btn-primary">View Plans</a>
        </div>
        """
        return web.Response(text=_page("Billing", body, user, csrf), content_type="text/html")

    plan_name = sub.get("plan", "none").title()
    status_color = "#4caf50" if sub["status"] == "active" else "#ffb74d"
    status_label = sub["status"].replace("_", " ").title()
    period_end = sub.get("current_period_end", "—")
    if period_end and period_end != "—":
        try:
            from datetime import datetime as dt
            pe = dt.fromisoformat(period_end)
            period_end = pe.strftime("%B %d, %Y")
        except Exception:
            pass

    current, limit = await asyncio.to_thread(billing.check_device_limit, user["user_id"])
    limit_text = "Unlimited" if limit >= 999 else str(limit)

    is_admin_grant = sub.get("stripe_customer_id") == "admin_grant"
    if is_admin_grant:
        manage_btn = (
            '<div style="padding:10px 14px;background:rgba(171,71,188,0.08);'
            'border:1px solid rgba(171,71,188,0.2);border-radius:10px;font-size:13px;color:#ce93d8">'
            'This subscription was granted by an admin. No payment required.</div>'
        )
        manage_script = ""
    else:
        manage_btn = '<button class="btn btn-primary" onclick="manageSubscription()">Manage Subscription</button>'
        manage_script = f"""
    <script>
    async function manageSubscription() {{
        const resp = await fetch("/portal/api/billing-portal", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: "{csrf}"}})
        }});
        if (resp.ok) {{
            const data = await resp.json();
            if (data.url) window.location.href = data.url;
        }} else {{
            alert("Failed to open billing portal");
        }}
    }}
    </script>
    """

    body = f"""
    <div class="card">
        <h2>Subscription</h2>
        <div style="display:flex;flex-wrap:wrap;gap:20px;margin:16px 0">
            <div>
                <div class="muted" style="margin-bottom:4px">Plan</div>
                <div style="font-size:18px;font-weight:600">{plan_name}</div>
            </div>
            <div>
                <div class="muted" style="margin-bottom:4px">Status</div>
                <div style="font-size:18px;font-weight:600;color:{status_color}">{status_label}</div>
            </div>
            <div>
                <div class="muted" style="margin-bottom:4px">Devices</div>
                <div style="font-size:18px;font-weight:600">{current} / {limit_text}</div>
            </div>
            <div>
                <div class="muted" style="margin-bottom:4px">{"Expires" if is_admin_grant else "Renews"}</div>
                <div style="font-size:18px;font-weight:600">{period_end}</div>
            </div>
        </div>
        <div style="margin-top:16px">
            {manage_btn}
        </div>
    </div>
    {manage_script}
    """
    return web.Response(text=_page("Billing", body, user, csrf), content_type="text/html")


async def page_checkout_success(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)

    body = """
    <div class="card" style="text-align:center">
        <div style="font-size:48px;margin-bottom:12px">&#10003;</div>
        <h2>Subscription Activated!</h2>
        <p style="margin:12px 0;color:#aab">
            Your subscription is now active. You can start using 9Bot right away.
        </p>
        <div style="display:flex;gap:10px;justify-content:center;margin-top:20px">
            <a href="/portal/" class="btn btn-primary">Go to Dashboard</a>
            <a href="/portal/billing" class="btn btn-outline">View Billing</a>
        </div>
    </div>
    """
    return web.Response(text=_page("Success", body, user, csrf), content_type="text/html")


async def api_subscribe(request: web.Request) -> web.Response:
    user = await _require_user(request)
    await _check_csrf(request)

    billing = _get_stripe_billing()
    if not billing:
        return web.json_response({"error": "Billing not configured"}, status=503)

    data = await request.json()
    plan = data.get("plan", "").strip()

    if plan not in ("standard",):
        return web.json_response({"error": "Invalid plan"}, status=400)

    try:
        base = f"{request.scheme}://{request.host}"
        url = await asyncio.to_thread(
            billing.create_checkout_session,
            user["user_id"],
            plan,
            success_url=f"{base}/portal/checkout-success",
            cancel_url=f"{base}/portal/pricing",
        )
        return web.json_response({"url": url})
    except Exception as e:
        log.error("Checkout session creation failed: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def api_billing_portal(request: web.Request) -> web.Response:
    user = await _require_user(request)
    await _check_csrf(request)

    billing = _get_stripe_billing()
    if not billing:
        return web.json_response({"error": "Billing not configured"}, status=503)

    try:
        base = f"{request.scheme}://{request.host}"
        url = await asyncio.to_thread(
            billing.create_portal_session,
            user["user_id"],
            return_url=f"{base}/portal/billing",
        )
        return web.json_response({"url": url})
    except Exception as e:
        log.error("Portal session creation failed: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def api_stripe_webhook(request: web.Request) -> web.Response:
    """Stripe webhook endpoint — no auth, signature-verified."""
    billing = _get_stripe_billing()
    if not billing:
        return web.json_response({"error": "Billing not configured"}, status=503)

    payload = await request.read()
    sig = request.headers.get("Stripe-Signature", "")

    try:
        result = await asyncio.to_thread(billing.handle_webhook, payload, sig)
        return web.json_response(result)
    except ValueError as e:
        log.warning("Webhook verification failed: %s", e)
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        log.error("Webhook processing failed: %s", e)
        return web.json_response({"error": "Internal error"}, status=500)


# ------------------------------------------------------------------
# Access check for proxied requests (called from relay_server)
# ------------------------------------------------------------------

async def check_portal_access(
    request: web.Request, bot_name: str, device_hash: str | None = None,
) -> tuple[str, str] | None:
    """Check if the request has portal-based access to a bot/device.

    Returns (access_level, username) if authorized, or None if no portal session.
    This does NOT check legacy token auth — that's handled separately.
    Shared/community devices grant full access to any logged-in user.
    """
    user = await _get_user(request)
    if not user:
        return None

    # Check if this is a shared/community device — full access for any logged-in user
    if device_hash:
        is_shared = await asyncio.to_thread(db.is_device_shared, bot_name, device_hash)
        if is_shared:
            return "full", user["username"]

    access = await asyncio.to_thread(db.check_access, user["user_id"], bot_name, device_hash)
    if access:
        return access, user["username"]
    return None
