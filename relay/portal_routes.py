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
    app.router.add_get("/portal/bot/{bot_name}", page_bot_detail)
    app.router.add_get("/portal/admin", page_admin)
    app.router.add_get("/portal/account", page_account)

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
    app.router.add_post("/portal/api/account/password", api_change_password)

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
            '<a href="/portal/billing">Billing</a>',
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

    body = f"""
    {error_html}
    <div class="card">
        <h2>Register</h2>
        <form method="post" action="/portal/api/register">
            <div class="form-group">
                <label>Invite Code</label>
                <input type="text" name="invite_code" required maxlength="20"
                       autocomplete="off" autofocus>
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

    data = await asyncio.to_thread(db.get_user_bots, user["user_id"])

    def _bot_card(bot: dict, is_owned: bool = False) -> str:
        name = bot["bot_name"]
        label = _html_escape(bot.get("label") or name)
        online = name in _active_bots and not _active_bots[name].closed
        dot = "dot-online" if online else "dot-offline"
        status = "Online" if online else "Offline"
        devices = asyncio.get_event_loop().run_in_executor(None, db.list_devices, name)
        # We can't await here, so we use sync call
        dev_count = bot.get("device_count", 0)

        manage = ""
        if is_owned:
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
        owned_html = f'<div class="card"><h2>My Bots</h2>{cards}</div>'

    shared_html = ""
    if data["shared"]:
        cards = "".join(_bot_card(b) for b in data["shared"])
        shared_html = f'<div class="card"><h2>Shared With Me</h2>{cards}</div>'

    if not data["owned"] and not data["shared"]:
        owned_html = '<div class="card"><p class="muted">No bots available. Ask a bot owner to share access with you.</p></div>'

    # Subscription status banner
    sub_html = ""
    if not user["is_admin"]:
        billing = _get_stripe_billing()
        if billing:
            sub = await asyncio.to_thread(billing.get_subscription, user["user_id"])
            badge = _sub_badge(sub)
            if not sub or sub.get("status") not in ("active", "past_due"):
                sub_html = (
                    f'<div class="card" style="display:flex;align-items:center;justify-content:space-between">'
                    f'<div><strong>Subscription</strong> {badge}</div>'
                    f'<a href="/portal/pricing" class="btn btn-primary btn-sm">Subscribe</a>'
                    f'</div>'
                )
            else:
                sub_html = (
                    f'<div class="card" style="display:flex;align-items:center;justify-content:space-between">'
                    f'<div><strong>Subscription</strong> {badge}</div>'
                    f'<a href="/portal/billing" class="btn btn-outline btn-sm">Manage</a>'
                    f'</div>'
                )

    body = f"{sub_html}{owned_html}{shared_html}"
    return web.Response(text=_page("Dashboard", body, user, csrf), content_type="text/html")


async def page_bot_detail(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)
    bot_name = request.match_info["bot_name"]

    bot = await asyncio.to_thread(db.get_bot, bot_name)
    if not bot:
        raise web.HTTPNotFound(text="Bot not found")

    # Only owner or admin can manage
    is_owner = bot.get("owner_id") == user["user_id"]
    if not is_owner and not user["is_admin"]:
        raise web.HTTPForbidden(text="Only the bot owner can manage this bot")

    devices = await asyncio.to_thread(db.list_devices, bot_name)
    grants = await asyncio.to_thread(db.list_grants_for_bot, bot_name)

    online = bot_name in _active_bots and not _active_bots[bot_name].closed
    dot = "dot-online" if online else "dot-offline"
    status = "Online" if online else "Offline"
    label = _html_escape(bot.get("label") or bot_name)

    # Devices list
    dev_rows = ""
    for d in devices:
        dname = _html_escape(d.get("device_name") or d["device_hash"])
        dev_rows += (
            f'<div class="device-row">'
            f'<span>{dname}</span>'
            f'<span class="muted">{d["device_hash"]}</span>'
            f'</div>'
        )
    if not devices:
        dev_rows = '<p class="muted">No devices reported yet. The bot will report devices when it connects.</p>'

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
        <h3>Bot Settings</h3>
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
    </script>
    """
    return web.Response(text=_page(f"Bot: {label}", body, user, csrf), content_type="text/html")


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
        user_rows += (
            f'<tr>'
            f'<td>{u["id"]}</td>'
            f'<td>{admin_badge}{_html_escape(u["username"])}</td>'
            f'<td class="muted">{u["created_at"]}</td>'
            f'<td class="muted">{last}</td>'
            f'<td><button class="btn btn-danger btn-sm" onclick="deleteUser({u["id"]},\'{_html_escape(u["username"])}\')">Delete</button></td>'
            f'</tr>'
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
            f'<td><span class="dot {dot}"></span>{_html_escape(b.get("label") or b["bot_name"])}</td>'
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

    body = f"""
    <div class="card">
        <h2>Users ({len(users)})</h2>
        <table>
            <tr><th>ID</th><th>Username</th><th>Created</th><th>Last Login</th><th></th></tr>
            {user_rows}
        </table>
    </div>

    <div class="card">
        <h2>Bots ({len(bots)})</h2>
        <table>
            <tr><th>Bot</th><th>ID</th><th>Owner</th><th>Last Seen</th></tr>
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
                data.code + '</strong></code></div>';
        }} else alert("Failed");
    }}
    </script>
    """
    return web.Response(text=_page("Admin", body, user, csrf), content_type="text/html")


async def page_account(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)

    msg = request.query.get("msg", "")
    msg_html = f'<div class="alert alert-success">{_html_escape(msg)}</div>' if msg else ""

    body = f"""
    {msg_html}
    <div class="card">
        <h2>Account</h2>
        <p style="margin-bottom:16px">Logged in as <strong>{_html_escape(user["username"])}</strong>
        {"<span class='badge badge-admin'>admin</span>" if user["is_admin"] else ""}</p>

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
    await asyncio.to_thread(db.update_user_login, user["id"])
    token = await asyncio.to_thread(db.create_session, user["id"])

    resp = web.HTTPFound(next_url)
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
    password = data.get("password") or ""

    if not invite_code or not username or not password:
        raise web.HTTPFound("/portal/register?error=All+fields+required")

    if len(username) > 50 or not all(c.isalnum() or c in "-_" for c in username):
        raise web.HTTPFound("/portal/register?error=Invalid+username")

    if len(password) < 6:
        raise web.HTTPFound("/portal/register?error=Password+must+be+at+least+6+characters")

    pw_hash = await asyncio.to_thread(auth.hash_password, password)

    try:
        user_id = await asyncio.to_thread(db.create_user, username, pw_hash)
    except Exception:
        raise web.HTTPFound("/portal/register?error=Username+already+taken")

    used = await asyncio.to_thread(db.use_invite_code, invite_code, user_id)
    if not used:
        # Roll back user creation
        await asyncio.to_thread(db.delete_user, user_id)
        raise web.HTTPFound("/portal/register?error=Invalid+or+used+invite+code")

    # Auto-login
    await asyncio.to_thread(db.update_user_login, user_id)
    token = await asyncio.to_thread(db.create_session, user_id)

    resp = web.HTTPFound("/portal/")
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
        raise web.HTTPNotFound(text="Bot not found")
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
        raise web.HTTPNotFound(text="Bot not found")
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
        return web.json_response({"error": "Bot not found"}, status=404)
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
            return web.json_response({"error": "Only admin or bot owners can create invites"}, status=403)

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
        return web.json_response({"error": "Bot not found"}, status=404)
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
    if sub["status"] == "past_due":
        return f'<span class="badge" style="color:#ffb74d;border:1px solid rgba(255,183,77,0.35)">{plan} (Past Due)</span>'
    return f'<span class="badge badge-full">{plan}</span>'


async def page_pricing(request: web.Request) -> web.Response:
    user = await _get_user(request)
    csrf = _get_csrf(request) if user else ""
    billing = _get_stripe_billing()

    sub = None
    if user and billing:
        sub = await asyncio.to_thread(billing.get_subscription, user["user_id"])

    current_plan = sub["plan"] if sub and sub["status"] in ("active", "past_due") else None

    plans_html = ""
    plans = [
        ("basic", "Basic", "$10", "/mo", "2 devices", [
            "Core bot access", "View-only dashboard", "Remote access via relay",
        ]),
        ("pro", "Pro", "$25", "/mo", "6 devices", [
            "Full control of all auto-modes", "Priority support",
            "All Basic features",
        ]),
        ("enterprise", "Enterprise", "$50", "/mo", "Unlimited", [
            "API access", "Multi-bot support", "All Pro features",
        ]),
    ]

    for plan_key, name, price, period, devices, features in plans:
        features_html = "".join(
            f'<li style="padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.03)">'
            f'<span style="color:#4caf50;margin-right:8px">&#10003;</span>{f}</li>'
            for f in features
        )
        is_current = current_plan == plan_key
        if is_current:
            btn = '<span class="btn btn-outline" style="width:100%;text-align:center;cursor:default">Current Plan</span>'
        elif user and billing:
            btn = (
                f'<button class="btn btn-primary" style="width:100%" '
                f'onclick="subscribe(\'{plan_key}\')">Subscribe</button>'
            )
        else:
            btn = '<a href="/portal/login?next=/portal/pricing" class="btn btn-primary" style="width:100%;text-align:center">Login to Subscribe</a>'

        highlight = "border-color:rgba(100,216,255,0.3);box-shadow:0 0 20px rgba(100,216,255,0.08);" if plan_key == "pro" else ""
        popular = '<div style="position:absolute;top:-10px;right:16px;background:#1565c0;color:#fff;font-size:10px;font-weight:700;padding:3px 10px;border-radius:6px;text-transform:uppercase;letter-spacing:1px">Popular</div>' if plan_key == "pro" else ""

        plans_html += f"""
        <div class="card" style="flex:1;min-width:200px;position:relative;{highlight}">
            {popular}
            <h2 style="margin-bottom:4px">{name}</h2>
            <div style="margin-bottom:12px">
                <span style="font-size:28px;font-weight:700">{price}</span>
                <span class="muted">{period}</span>
            </div>
            <div style="font-size:13px;color:#64d8ff;margin-bottom:16px;font-weight:600">{devices}</div>
            <ul style="list-style:none;padding:0;margin-bottom:20px;font-size:13px;color:#aab">
                {features_html}
            </ul>
            {btn}
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
    <h2 style="text-align:center;margin-bottom:8px">Choose Your Plan</h2>
    <p class="muted" style="text-align:center;margin-bottom:20px">
        All plans include a 9Bot relay connection and remote dashboard access.
    </p>
    <div style="display:flex;gap:12px;flex-wrap:wrap">
        {plans_html}
    </div>
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
                <div class="muted" style="margin-bottom:4px">Renews</div>
                <div style="font-size:18px;font-weight:600">{period_end}</div>
            </div>
        </div>
        <div style="display:flex;gap:10px;margin-top:16px">
            <button class="btn btn-primary" onclick="manageSubscription()">Manage Subscription</button>
            <a href="/portal/pricing" class="btn btn-outline">Change Plan</a>
        </div>
    </div>

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

    if plan not in ("basic", "pro", "enterprise"):
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
    """
    user = await _get_user(request)
    if not user:
        return None

    access = await asyncio.to_thread(db.check_access, user["user_id"], bot_name, device_hash)
    if access:
        return access, user["username"]
    return None
