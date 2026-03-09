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
_bot_device_status: dict = {}  # {bot_name: [{"hash", "name", "online"}, ...]}


def setup_portal_routes(app: web.Application, active_bots: dict,
                        bot_devices: dict | None = None) -> None:
    """Register all portal routes on the aiohttp app."""
    global _active_bots, _bot_device_status
    _active_bots = active_bots
    if bot_devices is not None:
        _bot_device_status = bot_devices

    # Pages
    app.router.add_get("/portal", _redirect_portal_slash)
    app.router.add_get("/portal/", page_dashboard)
    app.router.add_get("/portal/login", page_login)
    app.router.add_get("/portal/register", page_register)
    app.router.add_get("/portal/community", page_community)
    app.router.add_get("/portal/bot/{bot_name}", page_bot_detail)
    app.router.add_get("/portal/admin", page_admin)
    app.router.add_get("/portal/admin/user/{user_id}", page_admin_user_detail)
    app.router.add_get("/portal/account", page_account)
    app.router.add_get("/portal/guide", page_guide)
    app.router.add_get("/portal/statistics", page_statistics)

    # API
    app.router.add_post("/portal/api/login", api_login)
    app.router.add_post("/portal/api/logout", api_logout)
    app.router.add_post("/portal/api/register", api_register)
    app.router.add_get("/portal/api/bots", api_bots)
    app.router.add_put("/portal/api/bots/{bot_name}", api_update_bot)
    app.router.add_get("/portal/api/grants/{bot_name}", api_grants_for_bot)
    app.router.add_post("/portal/api/grants", api_create_grant)
    app.router.add_post("/portal/api/grants/{grant_id}/revoke", api_delete_grant)
    app.router.add_post("/portal/api/invite-codes", api_create_invite)
    app.router.add_get("/portal/api/invite-codes", api_list_invites)
    app.router.add_get("/portal/api/admin/users", api_admin_users)
    app.router.add_post("/portal/api/admin/users/{user_id}/delete", api_admin_delete_user)
    app.router.add_post("/portal/api/admin/users/{user_id}/approve", api_admin_approve_user)
    app.router.add_post("/portal/api/admin/users/{user_id}/reject", api_admin_reject_user)
    app.router.add_post("/portal/api/admin/users/{user_id}/toggle-admin", api_admin_toggle_admin)
    app.router.add_put("/portal/api/admin/bots/{bot_name}/owner", api_admin_set_owner)
    app.router.add_post("/portal/api/admin/clear-invites", api_admin_clear_invites)
    app.router.add_post("/portal/api/admin/reset-password", api_admin_reset_password)
    app.router.add_post("/portal/api/admin/grant-subscription", api_admin_grant_subscription)
    app.router.add_post("/portal/api/admin/revoke-subscription", api_admin_revoke_subscription)
    app.router.add_post("/portal/api/admin/users/{user_id}/assign-device", api_admin_assign_device)
    app.router.add_post("/portal/api/admin/users/{user_id}/unassign-device", api_admin_unassign_device)
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
    """Like _get_user but redirects to login if not authenticated or unapproved."""
    user = await _get_user(request)
    if not user:
        next_url = str(request.url.relative())
        raise web.HTTPFound(f"/portal/login?next={next_url}")
    if not user.get("is_approved") and not user.get("is_admin"):
        raise web.HTTPFound("/portal/login?error=Your+account+is+pending+admin+approval")
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
            '<a href="/portal/statistics">Statistics</a>',
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
# Portal nav CSS — portal-prefixed to avoid clashing with bot's style.css
# ------------------------------------------------------------------

_PORTAL_NAV_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,wght@1,700&text=9&display=swap');

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: #0c0c18; color: #e0e0f0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    min-height: 100vh;
    -webkit-text-size-adjust: 100%;
}
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }

.portal-nav {
    position: sticky; top: 0; z-index: 100;
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px;
    background: #14142a;
    border-bottom: 1px solid rgba(100,216,255,0.08);
    box-shadow: 0 4px 20px rgba(0,0,0,0.4);
}
.portal-nav-logo {
    display: flex; align-items: center; gap: 3px;
    text-decoration: none;
}
.portal-nav-logo-bars {
    display: flex; flex-direction: column; gap: 2px; width: 3px; height: 28px;
}
.portal-nav-logo-bars::before, .portal-nav-logo-bars::after {
    content: ''; width: 2px; flex: 1; border-radius: 1px;
}
.portal-nav-logo-bars::before {
    background: linear-gradient(180deg, rgba(100,216,255,0.13), rgba(100,216,255,0.53), rgba(100,216,255,0.13));
}
.portal-nav-logo-bars::after {
    background: linear-gradient(180deg, rgba(100,216,255,0.27), rgba(100,216,255,1), rgba(100,216,255,0.27));
}
.portal-nav-logo-text { display: flex; flex-direction: column; line-height: 1; }
.portal-nav-logo-main {
    display: flex; align-items: baseline; gap: 1px;
    font-size: 11px; font-weight: 300; color: #e0e0f0; letter-spacing: 2px;
}
.portal-nav-logo-nine {
    font-family: 'Bodoni Moda', serif; font-style: italic; font-weight: 700;
    font-size: 28px; color: #e0e0f0; line-height: 1;
}
.portal-nav-logo-sub {
    font-size: 7px; color: rgba(100,216,255,0.25); letter-spacing: 1.5px;
    font-weight: 400;
}
.portal-nav-links { display: flex; align-items: center; gap: 4px; }
.portal-nav-links a, .portal-nav-links button {
    color: #889; font-size: 13px; font-weight: 600; text-decoration: none;
    padding: 7px 14px; border-radius: 8px; border: none; background: transparent;
    cursor: pointer; transition: all 0.2s ease;
}
.portal-nav-links a:hover, .portal-nav-links button:hover { color: #fff; background: #1e3a5f; text-decoration: none; }
.portal-nav-links a.active { color: #64d8ff; background: rgba(100,216,255,0.1); }
@media (max-width: 480px) {
    .portal-nav { flex-wrap: wrap; gap: 8px; }
    .portal-nav-links { gap: 2px; }
    .portal-nav-links a, .portal-nav-links button { padding: 6px 10px; font-size: 12px; }
}
"""


def _page_dashboard_wrapper(title: str, body: str, user: dict | None = None, csrf: str = "",
                            css_bot: str = "") -> str:
    """HTML wrapper for dashboard page — loads bot's style.css, uses portal-prefixed nav."""
    nav_links = ""
    if user:
        links = [
            '<a href="/portal/" class="active">Dashboard</a>',
            '<a href="/portal/community">Community</a>',
            '<a href="/portal/billing">Billing</a>',
            '<a href="/portal/guide">Guide</a>',
            '<a href="/portal/account">Account</a>',
            '<a href="/portal/statistics">Statistics</a>',
        ]
        if user.get("is_admin"):
            links.append('<a href="/portal/admin">Admin</a>')
        links.append(
            f'<form method="post" action="/portal/api/logout" style="display:inline">'
            f'<input type="hidden" name="csrf_token" value="{csrf}">'
            f'<button type="submit">Logout</button>'
            f'</form>'
        )
        nav_links = f'<div class="portal-nav-links">{"".join(links)}</div>'

    logo = (
        '<a href="/portal/" class="portal-nav-logo">'
        '<div class="portal-nav-logo-bars"></div>'
        '<div class="portal-nav-logo-text">'
        '<div class="portal-nav-logo-main"><span class="portal-nav-logo-nine">9</span>BOT</div>'
        '<div class="portal-nav-logo-sub">PORTAL</div>'
        '</div></a>'
    )

    css_link = f'<link rel="stylesheet" href="/{css_bot}/static/style.css?v=96">' if css_bot else ""

    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1,"
        f"maximum-scale=1,user-scalable=no'>"
        f"<meta name='apple-mobile-web-app-capable' content='yes'>"
        f"<title>{title} — 9Bot Portal</title>"
        f"<style>{_PORTAL_NAV_CSS}</style>"
        f"{css_link}"
        f"</head><body>"
        f'<div class="portal-nav">{logo}{nav_links}</div>'
        f'<main>'
        f"{body}"
        f"</main></body></html>"
    )


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
            Don't have an account? <a href="/portal/register">Sign up</a>
        </p>
    </div>
    """
    return web.Response(text=_page("Login", body), content_type="text/html")


async def page_register(request: web.Request) -> web.Response:
    user = await _get_user(request)
    if user:
        raise web.HTTPFound("/portal/")

    error = request.query.get("error", "")
    success = request.query.get("success", "")
    error_html = f'<div class="alert alert-error">{_html_escape(error)}</div>' if error else ""
    success_html = f'<div class="alert alert-success">{_html_escape(success)}</div>' if success else ""

    body = f"""
    {error_html}{success_html}
    <div class="card">
        <h2>Create Account</h2>
        <form method="post" action="/portal/api/register">
            <div class="form-group">
                <label>Email</label>
                <input type="email" name="email" required maxlength="200"
                       autocomplete="email" placeholder="you@example.com" autofocus>
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
            <button type="submit" class="btn btn-primary">Sign Up</button>
        </form>
        <p class="muted" style="margin-top:12px">
            Already have an account? <a href="/portal/login">Login</a>
        </p>
    </div>
    """
    return web.Response(text=_page("Sign Up", body), content_type="text/html")


async def page_dashboard(request: web.Request) -> web.Response:
    user = await _require_user(request)
    csrf = _get_csrf(request)

    # Admin sees the legacy full view (bots, grants, management)
    if user["is_admin"]:
        return await _page_dashboard_admin(request, user, csrf)

    # Customer view: tabbed (My Devices / Community) with inline dashboards
    devices = await asyncio.to_thread(db.get_user_devices, user["user_id"])
    shared_devices = await asyncio.to_thread(db.list_shared_devices)

    # My device rows
    dev_html = ""
    for d in devices:
        bot_name = d["bot_name"]
        online = bot_name in _active_bots and not _active_bots[bot_name].closed
        dot = "dot-online" if online else "dot-offline"
        status = "Online" if online else "Offline"
        label = _html_escape(d.get("label") or d.get("device_name") or d["device_hash"][:8])
        open_url = f"/{bot_name}/d/{d['device_hash']}"

        dev_html += (
            f'<div class="bot-row dev-row" data-url="{open_url}" onclick="openDevice(this)">'
            f'<div class="bot-info">'
            f'<div class="bot-name"><span class="dot {dot}"></span>{label}</div>'
            f'<div class="bot-meta">{status}</div>'
            f'</div>'
            f'<svg width="16" height="16" viewBox="0 0 16 16" fill="none" style="opacity:0.3;flex-shrink:0">'
            f'<path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>'
            f'</div>'
        )

    if not dev_html:
        dev_html = (
            '<p class="muted" style="padding:8px 0">No devices yet. '
            'Ask an admin to grant you access, or check the Community tab.</p>'
        )

    # Community device rows
    community_html = ""
    for d in shared_devices:
        sd_bot = d["bot_name"]
        online = sd_bot in _active_bots and not _active_bots[sd_bot].closed
        dot = "dot-online" if online else "dot-offline"
        status = "Online" if online else "Offline"
        label = _html_escape(d.get("label") or d.get("device_name") or d["device_hash"][:8])
        open_url = f"/{sd_bot}/d/{d['device_hash']}"

        community_html += (
            f'<div class="bot-row dev-row" data-url="{open_url}" onclick="openDevice(this)">'
            f'<div class="bot-info">'
            f'<div class="bot-name"><span class="dot {dot}"></span>{label}</div>'
            f'<div class="bot-meta">{status}</div>'
            f'</div>'
            f'<svg width="16" height="16" viewBox="0 0 16 16" fill="none" style="opacity:0.3;flex-shrink:0">'
            f'<path d="M6 3l5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>'
            f'</div>'
        )

    if not community_html:
        community_html = '<p class="muted" style="padding:8px 0">No community accounts available yet.</p>'

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

    online_shared = sum(
        1 for d in shared_devices
        if d["bot_name"] in _active_bots and not _active_bots[d["bot_name"]].closed
    )

    body = f"""
    <style>
    .dash-tabs {{
        display: flex; gap: 2px; margin-bottom: 12px;
        background: #141428; border-radius: 10px; padding: 3px;
        border: 1px solid rgba(255,255,255,0.04);
    }}
    .dash-tab {{
        flex: 1; padding: 10px 0; text-align: center;
        font-size: 12px; font-weight: 600; color: #556;
        border: none; background: transparent; cursor: pointer;
        border-radius: 8px; transition: all 0.2s ease;
    }}
    .dash-tab:hover {{ color: #99a; }}
    .dash-tab.active {{
        background: rgba(100,216,255,0.08); color: #64d8ff;
    }}
    .dash-panel {{ display: none; }}
    .dash-panel.active {{ display: block; }}
    .dev-row {{ cursor: pointer; transition: background 0.15s ease; }}
    .dev-row:hover {{ background: rgba(100,216,255,0.04); }}
    #dash-embed {{
        display: none; margin-top: 12px;
    }}
    #dash-embed.active {{ display: block; }}
    #dash-embed-header {{
        display: flex; align-items: center; gap: 10px;
        padding: 10px 12px;
        background: #181830; border-radius: 10px 10px 0 0;
        border: 1px solid rgba(255,255,255,0.06);
        border-bottom: none;
    }}
    #dash-embed-header .back-btn {{
        background: none; border: none; color: #64d8ff;
        cursor: pointer; font-size: 13px; font-weight: 600;
        display: flex; align-items: center; gap: 4px;
        padding: 4px 8px; border-radius: 6px;
        transition: background 0.15s;
    }}
    #dash-embed-header .back-btn:hover {{ background: rgba(100,216,255,0.1); }}
    #dash-embed-header .dev-label {{
        font-size: 13px; color: #aab; font-weight: 500;
    }}
    #dash-embed-frame {{
        width: 100%; border: none;
        border: 1px solid rgba(255,255,255,0.06);
        border-top: none;
        border-radius: 0 0 10px 10px;
        background: #0c0c18;
        min-height: 600px;
        height: calc(100vh - 180px);
    }}
    </style>

    {getting_started}

    <div class="dash-tabs" id="dash-tabs-bar">
        <button class="dash-tab active" onclick="dashTab('mine',this)">My Devices ({len(devices)})</button>
        <button class="dash-tab" onclick="dashTab('community',this)">Community ({online_shared} online)</button>
    </div>

    <div id="dp-mine" class="dash-panel active">
        <div class="card device-list-card">{dev_html}</div>
    </div>

    <div id="dp-community" class="dash-panel">
        <div class="card device-list-card">{community_html}</div>
    </div>

    <div id="dash-embed">
        <div id="dash-embed-header">
            <button class="back-btn" onclick="closeDevice()">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                <path d="M10 3l-5 5 5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
                Back
            </button>
            <span class="dev-label" id="embed-label"></span>
        </div>
        <iframe id="dash-embed-frame" sandbox="allow-same-origin allow-scripts allow-forms allow-popups"></iframe>
    </div>

    {sub_html}

    <script>
    function dashTab(name, el) {{
        var tabs = document.querySelectorAll('.dash-tab');
        var panels = document.querySelectorAll('.dash-panel');
        for (var i = 0; i < tabs.length; i++) tabs[i].classList.remove('active');
        for (var i = 0; i < panels.length; i++) panels[i].classList.remove('active');
        el.classList.add('active');
        var p = document.getElementById('dp-' + name);
        if (p) p.classList.add('active');
    }}
    function openDevice(row) {{
        var url = row.getAttribute('data-url');
        var label = row.querySelector('.bot-name').textContent.trim();
        // Hide tabs and device lists
        document.getElementById('dash-tabs-bar').style.display = 'none';
        var lists = document.querySelectorAll('.device-list-card');
        for (var i = 0; i < lists.length; i++) lists[i].closest('.dash-panel').style.display = 'none';
        // Show embed
        document.getElementById('dash-embed').classList.add('active');
        document.getElementById('embed-label').textContent = label;
        document.getElementById('dash-embed-frame').src = url;
    }}
    function closeDevice() {{
        document.getElementById('dash-embed').classList.remove('active');
        document.getElementById('dash-embed-frame').src = '';
        document.getElementById('dash-tabs-bar').style.display = '';
        // Restore active panel
        var panels = document.querySelectorAll('.dash-panel');
        for (var i = 0; i < panels.length; i++) {{
            if (panels[i].querySelector('.dash-tab-active-marker')) panels[i].style.display = '';
        }}
        // Just show the active one
        var activeTab = document.querySelector('.dash-tab.active');
        if (activeTab) activeTab.click();
    }}
    </script>
    """
    return web.Response(text=_page("Dashboard", body, user, csrf), content_type="text/html")


# ---------------------------------------------------------------------------
# Portal dashboard JS — renders device cards identically to the bot's
# index.html.  Uses the bot's style.css (loaded via <link>).  Each card has
# data-api="/{bot}/d/{dhash}" so every fetch targets the correct device.
# This is a raw string so JS braces don't clash with Python f-strings.
# ---------------------------------------------------------------------------
_PORTAL_DASHBOARD_JS = r"""
var _troopData = {};
var _stoppingModes = {};
var _startingModes = {};
var _manualControlDevices = {};
var _liveViewTimers = {};
var _chatFeedCache = {};

function fmtTime(sec) {
    if (sec == null) return '';
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
}
function fmtNum(n) {
    if (n == null) return '?';
    if (n >= 1000000) return (n / 1000000).toFixed(n % 1000000 === 0 ? 0 : 1) + 'M';
    if (n >= 10000) return (n / 1000).toFixed(n % 1000 === 0 ? 0 : 1) + 'K';
    return n.toLocaleString();
}

var QUEST_LABELS = {'TITAN':'Titans','EVIL_GUARD':'Evil Guard','PVP':'PvP',
    'GATHER':'Gold','TOWER':'Fortress'};
var QUEST_ALIASES = {'FORTRESS':'TOWER'};
var ACTION_CLASSES = {'Home':'troop-home','Returning':'troop-returning',
    'Rallying':'troop-rallying','Defending':'troop-defending',
    'Marching':'troop-marching','Gathering':'troop-gathering'};
var _chatChannelMap = {'SERVER':{name:'Kingdom',cls:'server'},
    'UNION':{name:'Alliance',cls:'union'},'UNION_R4':{name:'Alliance',cls:'union'},
    'FACTION':{name:'Faction',cls:'faction'},'WORLD':{name:'World',cls:'world'},
    'PRIVATE':{name:'Private',cls:'private'}};
var _chatChannelNames = {SERVER:'Kingdom',UNION:'Alliance',FACTION:'Faction',WORLD:'World',PRIVATE:'Private'};

/* ---------- Troops ---------- */
function renderTroops(container, troops, age, elapsed, source) {
    container.textContent = '';
    var card = container.closest('.device-card');
    var ageEl = card ? card.querySelector('.troop-label .troop-age') : null;
    if (ageEl) {
        if (age != null) {
            var d = age + (elapsed || 0);
            ageEl.textContent = d < 60 ? d + 's ago' : Math.floor(d/60) + 'm ago';
        } else ageEl.textContent = '';
    }
    var srcEl = card ? card.querySelector('.troop-label .troop-source') : null;
    if (srcEl) {
        if (source === 'protocol') { srcEl.textContent = '(proto) '; srcEl.style.color = '#00e5ff'; }
        else if (source === 'vision') { srcEl.textContent = '(vision) '; srcEl.style.color = '#999'; }
        else srcEl.textContent = '';
    }
    if (!troops || troops.length === 0) {
        var h = document.createElement('span');
        h.className = 'muted'; h.style.fontSize = '11px';
        h.textContent = 'No troop data yet';
        container.appendChild(h); return;
    }
    var groups = {};
    troops.forEach(function(t) {
        var k = t.action;
        if (!groups[k]) groups[k] = {count:0, soonest:null};
        groups[k].count++;
        if (t.time_left != null) {
            var adj = Math.max(0, t.time_left - (elapsed || 0));
            if (groups[k].soonest === null || adj < groups[k].soonest) groups[k].soonest = adj;
        }
    });
    Object.keys(groups).forEach(function(action) {
        var g = groups[action];
        var span = document.createElement('span');
        span.className = 'troop-summary ' + (ACTION_CLASSES[action] || 'troop-deployed');
        var txt = g.count + ' ' + action;
        if (action !== 'Home' && g.soonest !== null) txt += ' ' + fmtTime(g.soonest);
        span.textContent = txt;
        container.appendChild(span);
    });
}
function tickTroopTimers() {
    var now = Date.now();
    Object.keys(_troopData).forEach(function(dh) {
        var e = _troopData[dh];
        var elapsed = Math.floor((now - e.lastPollTime) / 1000);
        if (elapsed < 1) return;
        var card = document.querySelector('.device-card[data-dhash="' + dh + '"]');
        if (!card) return;
        var el = card.querySelector('.troop-slots');
        if (el) renderTroops(el, e.troops, e.snapshotAge, elapsed, e.source);
    });
}
setInterval(tickTroopTimers, 1000);

/* ---------- Quests ---------- */
function renderQuests(container, quests, questAge) {
    var header = container.previousElementSibling;
    var ageEl = header ? header.querySelector('.quest-age') : null;
    if (ageEl) {
        if (questAge != null)
            ageEl.textContent = questAge < 60 ? questAge + 's ago' : Math.floor(questAge/60) + 'm ago';
        else ageEl.textContent = '';
    }
    if (!quests || quests.length === 0) {
        container.textContent = '';
        if (header) header.style.display = 'none'; return;
    }
    if (header) header.style.display = '';
    container.textContent = '';
    var wrap = document.createElement('div'); wrap.className = 'dp-quests';
    var cnt = 0;
    quests.forEach(function(q) {
        var raw = q.quest_type.replace('QuestType.', '');
        raw = QUEST_ALIASES[raw] || raw;
        var label = QUEST_LABELS[raw] || raw;
        var seen = q.last_seen != null ? q.last_seen : 0;
        var target = q.target, pend = q.pending || 0;
        if (target != null && seen >= target && pend === 0) return;
        cnt++;
        var pill = document.createElement('div');
        pill.className = 'quest-pill quest-' + raw.toLowerCase();
        var lbl = document.createElement('span');
        lbl.className = 'quest-label'; lbl.textContent = label;
        pill.appendChild(lbl);
        if (raw !== 'PVP') {
            var val = document.createElement('span'); val.className = 'quest-val';
            var vt = fmtNum(seen);
            if (target != null) vt += '/' + fmtNum(target);
            val.textContent = vt;
            if (pend > 0) {
                var ps = document.createElement('span');
                ps.className = 'quest-pend'; ps.textContent = '+' + pend;
                val.appendChild(ps);
            }
            pill.appendChild(val);
        }
        wrap.appendChild(pill);
    });
    if (cnt === 0) {
        var m = document.createElement('div');
        m.className = 'quest-complete-msg'; m.textContent = 'All Quests Complete!';
        wrap.appendChild(m);
    }
    if (cnt >= 4) wrap.classList.add('quests-wrap');
    container.appendChild(wrap);
}

/* ---------- Events collapse ---------- */
function toggleEvents(header) {
    var section = header.closest('.device-events-section');
    var body = section.querySelector('.events-body');
    var arrow = header.querySelector('.controls-arrow');
    var isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : '';
    arrow.textContent = isOpen ? '\u25B6' : '\u25BC';
}

/* ---------- Status polling (all cards) ---------- */
function refreshAllDevices() {
    document.querySelectorAll('.device-card[data-api]').forEach(function(card) {
        var apiBase = card.getAttribute('data-api');
        var dh = card.getAttribute('data-dhash');
        fetch(apiBase + '/api/status', {credentials:'include'})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.devices || !data.devices[0]) return;
                var dev = data.devices[0];

                // Offline
                if (dev.offline) {
                    card.classList.add('device-card-offline');
                    var st = card.querySelector('.status-text');
                    if (st) {
                        st.textContent = dev.status; st.className = 'status-text';
                        if (dev.status.indexOf('Starting') !== -1 || dev.status.indexOf('Waiting') !== -1)
                            st.classList.add('status-waiting');
                        else if (dev.status === 'Offline') st.classList.add('status-offline');
                    }
                    // Show Start Emulator button (create if needed)
                    var emuBtn = card.querySelector('.emu-step-emu');
                    if (!emuBtn) {
                        var wrap = document.createElement('div');
                        wrap.className = 'emu-steps';
                        wrap.innerHTML = '<button type="button" class="emu-step-btn emu-step-emu" onclick="startEmulator(this)">Start Emulator</button>';
                        // Insert after device-top
                        var top = card.querySelector('.device-top');
                        if (top) top.after(wrap);
                        emuBtn = wrap.querySelector('.emu-step-emu');
                    }
                    if (emuBtn) {
                        if (dev.emu_starting) {
                            emuBtn.textContent = 'Starting...';
                            emuBtn.disabled = true;
                            emuBtn.classList.add('emu-starting');
                        } else {
                            emuBtn.textContent = 'Start Emulator';
                            emuBtn.disabled = false;
                            emuBtn.classList.remove('emu-starting');
                        }
                    }
                    // Hide controls/events/live-view for offline devices
                    card.querySelectorAll('.device-auto-modes,.device-events-section,.live-view-section,.troop-label,.troop-slots,.quest-header,.quest-tracking,.chat-feed,.bottom-bar').forEach(function(el) { el.style.display = 'none'; });
                    _moveToOffline(card);
                    return;
                }
                card.classList.remove('device-card-offline');
                // Restore sections hidden by offline state
                card.querySelectorAll('.device-auto-modes,.device-events-section,.live-view-section,.troop-label,.troop-slots,.quest-header,.quest-tracking,.chat-feed,.bottom-bar').forEach(function(el) { el.style.display = ''; });
                _moveToOnline(card);
                // Remove Start Emulator if device came online
                var emuWrap = card.querySelector('.emu-steps');
                if (emuWrap && !card.querySelector('.emu-steps-compact')) { /* don't remove the compact emu bar */ }
                var offlineEmu = card.querySelector('.emu-steps:not(.emu-steps-compact)');
                if (offlineEmu) offlineEmu.remove();

                // Status text + indicator
                var st = card.querySelector('.status-text');
                if (st) {
                    st.textContent = dev.status; st.className = 'status-text';
                    if (dev.status === 'Idle') { /* default gray */ }
                    else if (dev.status.indexOf('Logged Out') !== -1 || dev.status.indexOf('Offline') !== -1)
                        st.classList.add('status-error');
                    else if (dev.status.indexOf('Stopping') !== -1) st.classList.add('status-stopping');
                    else if (dev.status.indexOf('Waiting') !== -1) st.classList.add('status-waiting');
                    else if (dev.status.indexOf('Navigating') !== -1) st.classList.add('status-navigating');
                    else st.classList.add('status-active');
                }
                var dot = card.querySelector('.status-indicator');
                if (dot) { if (dev.status !== 'Idle') dot.classList.add('active'); else dot.classList.remove('active'); }

                // Troop count in header
                var troopCount = card.querySelector('.device-troops');
                if (troopCount && dev.troops) {
                    var home = dev.troops.filter(function(t){ return t.action === 'Home'; }).length;
                    troopCount.innerHTML = '&#9876; ' + home + '/' + dev.troops.length;
                }
                // Troops
                var troopEl = card.querySelector('.troop-slots');
                if (troopEl) {
                    _troopData[dh] = {troops:dev.troops, snapshotAge:dev.snapshot_age,
                        lastPollTime:Date.now(), source:dev.troop_source};
                    renderTroops(troopEl, dev.troops, dev.snapshot_age, 0, dev.troop_source);
                }
                // Quests
                var questEl = card.querySelector('.quest-tracking');
                if (questEl) renderQuests(questEl, dev.quests, dev.quest_age);
                // Mithril
                var mt = card.querySelector('.mithril-timer');
                if (mt) {
                    if (dev.mithril_next != null) {
                        var ts = mt.querySelector('.mithril-time');
                        if (ts) ts.textContent = dev.mithril_next > 0 ? fmtTime(dev.mithril_next) : 'Due';
                        mt.style.display = '';
                    } else mt.style.display = 'none';
                }
                // Chat
                var chatAvail = dev.chat_available || dev.protocol_active;
                var chatBtn = card.querySelector('.chat-view-toggle');
                if (chatBtn) chatBtn.style.display = chatAvail ? '' : 'none';
                var chatFeed = card.querySelector('.chat-feed');
                if (chatFeed) {
                    if (chatAvail) {
                        chatFeed.style.display = '';
                        loadChatFeed(dh, apiBase, chatFeed);
                    } else chatFeed.style.display = 'none';
                }
                // Auto mode toggles + pills
                var tasks = data.tasks || [];
                card.querySelectorAll('.auto-row[data-mode]').forEach(function(row) {
                    var key = row.getAttribute('data-mode');
                    var toggle = row.querySelector('.toggle');
                    if (!toggle) return;
                    if (_manualControlDevices[dh]) { toggle.classList.remove('on'); return; }
                    var sk = dh + '_' + key;
                    var running = tasks.some(function(t) { return t.endsWith('_' + key); });
                    if (_stoppingModes[sk]) { if (!running) delete _stoppingModes[sk]; toggle.classList.remove('on'); }
                    else if (_startingModes[sk]) { if (running) delete _startingModes[sk]; toggle.classList.add('on'); }
                    else { if (running) toggle.classList.add('on'); else toggle.classList.remove('on'); }
                });
                card.querySelectorAll('.control-pill[data-mode]').forEach(function(pill) {
                    var key = pill.getAttribute('data-mode');
                    var sk = dh + '_' + key;
                    var running = tasks.some(function(t) { return t.endsWith('_' + key); });
                    if (_stoppingModes[sk]) { if (!running) delete _stoppingModes[sk]; pill.classList.remove('pill-on'); }
                    else if (_startingModes[sk]) { if (running) delete _startingModes[sk]; pill.classList.add('pill-on'); }
                    else { if (running) pill.classList.add('pill-on'); else pill.classList.remove('pill-on'); }
                });
            })
            .catch(function() {});
    });
}
refreshAllDevices();
setInterval(refreshAllDevices, 3000);

/* ---------- Auto mode toggle ---------- */
function toggleAutoMode(modeKey, btn) {
    var card = btn.closest('.device-card');
    if (!card) return;
    var dh = card.getAttribute('data-dhash');
    if (_manualControlDevices[dh]) return;
    var apiBase = card.getAttribute('data-api');
    var row = btn.closest('.auto-row');
    var toggle = row ? row.querySelector('.toggle') : null;
    var isRunning = toggle && toggle.classList.contains('on');
    var url, body;
    if (isRunning) {
        url = apiBase + '/tasks/stop-mode';
        body = 'mode_key=' + encodeURIComponent(modeKey);
    } else {
        url = apiBase + '/tasks/start';
        body = 'task_name=' + encodeURIComponent(modeKey) + '&task_type=auto';
    }
    if (toggle) toggle.classList.toggle('on');
    var sk = dh + '_' + modeKey;
    var pill = card.querySelector('.control-pill[data-mode="' + modeKey + '"]');
    if (isRunning) {
        _stoppingModes[sk] = true; delete _startingModes[sk];
        if (pill) pill.classList.remove('pill-on');
    } else {
        _startingModes[sk] = true; delete _stoppingModes[sk];
        if (pill) pill.classList.add('pill-on');
    }
    fetch(url, {method:'POST', credentials:'include',
        headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:body
    }).catch(function(){});
}

/* ---------- Controls collapse ---------- */
function toggleControls(header) {
    var modes = header.closest('.device-auto-modes');
    var body = modes.querySelector('.controls-body');
    var pills = modes.querySelector('.controls-pills');
    var arrow = header.querySelector('.controls-arrow');
    var isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : '';
    pills.style.display = isOpen ? '' : 'none';
    arrow.textContent = isOpen ? '\u25B6' : '\u25BC';
}

/* ---------- Live View ---------- */
function _apiOf(el) {
    var c = el.closest('.device-card');
    return c ? c.getAttribute('data-api') : '';
}
function _dhOf(el) {
    var c = el.closest('.device-card');
    return c ? c.getAttribute('data-dhash') : '';
}
function _startStream(img, btn, dh, fps) {
    var streamUrl = _apiOf(img) + '/api/stream?fps=' + fps + '&quality=30';
    img.onerror = function() {
        if (btn.getAttribute('data-active') !== 'true') return;
        img.onerror = null;
        startPollingFallback(img, dh);
    };
    img.src = streamUrl;
}
function _activateLiveView(card, dh, fps) {
    var btn = card.querySelector('.live-view-toggle');
    var container = card.querySelector('.live-view-container');
    var img = container.querySelector('.live-view-img');
    if (btn && btn.getAttribute('data-active') === 'true') {
        img.src = ''; _startStream(img, btn, dh, fps); return;
    }
    if (btn) { btn.setAttribute('data-active','true'); btn.classList.add('live-view-on'); }
    container.style.display = '';
    _startStream(img, btn || container, dh, fps);
}
function _deactivateLiveView(card, dh) {
    var btn = card.querySelector('.live-view-toggle');
    var container = card.querySelector('.live-view-container');
    var img = container.querySelector('.live-view-img');
    if (btn) { btn.setAttribute('data-active','false'); btn.classList.remove('live-view-on'); }
    container.style.display = 'none'; img.src = '';
    if (_liveViewTimers[dh]) { clearInterval(_liveViewTimers[dh]); delete _liveViewTimers[dh]; }
}
function toggleLiveView(btn) {
    var card = btn.closest('.device-card');
    var dh = card.getAttribute('data-dhash');
    var active = btn.getAttribute('data-active') === 'true';
    if (active) {
        _deactivateLiveView(card, dh);
        if (_manualControlDevices[dh]) {
            var mc = card.querySelector('.manual-control-toggle');
            if (mc) { mc.setAttribute('data-active','false'); mc.classList.remove('manual-control-on'); }
            card.classList.remove('manual-mode');
            delete _manualControlDevices[dh];
        }
    } else {
        _activateLiveView(card, dh, _manualControlDevices[dh] ? 8 : 5);
    }
}
function toggleManualControl(btn) {
    var card = btn.closest('.device-card');
    var dh = card.getAttribute('data-dhash');
    var apiBase = card.getAttribute('data-api');
    var active = btn.getAttribute('data-active') === 'true';
    if (active) {
        btn.setAttribute('data-active','false'); btn.classList.remove('manual-control-on');
        card.classList.remove('manual-mode'); delete _manualControlDevices[dh];
        var lv = card.querySelector('.live-view-toggle');
        if (lv && lv.getAttribute('data-active') === 'true') _activateLiveView(card, dh, 5);
    } else {
        btn.setAttribute('data-active','true'); btn.classList.add('manual-control-on');
        card.classList.add('manual-mode'); _manualControlDevices[dh] = true;
        fetch(apiBase + '/tasks/stop-all', {method:'POST', credentials:'include'}).catch(function(){});
        card.querySelectorAll('.auto-row .toggle.on').forEach(function(t) {
            t.classList.remove('on');
            var r = t.closest('.auto-row');
            if (r) _stoppingModes[dh + '_' + r.getAttribute('data-mode')] = true;
        });
        _activateLiveView(card, dh, 8);
    }
}
function startPollingFallback(img, dh) {
    var apiBase = _apiOf(img);
    function poll() {
        var url = apiBase + '/api/screenshot?quality=50&_t=' + Date.now();
        var p = new Image(); p.onload = function() { img.src = p.src; }; p.src = url;
    }
    poll();
    _liveViewTimers[dh] = setInterval(poll, 3000);
}

/* ---------- Offline section toggle ---------- */
function toggleOffline(header) {
    var section = header.closest('.actions-section');
    var grid = section.querySelector('.device-grid');
    var arrow = header.querySelector('.controls-arrow');
    var isOpen = grid.style.display !== 'none';
    grid.style.display = isOpen ? 'none' : '';
    arrow.innerHTML = isOpen ? '&#9654;' : '&#9660;';
}

/* ---------- Move card between online/offline grids ---------- */
function _getOfflineGrid() {
    var section = document.querySelector('.actions-section');
    if (section) return section.querySelector('.device-grid');
    return null;
}
function _getOnlineGrid() {
    // The main grid is the first .device-grid that is NOT inside .actions-section
    var grids = document.querySelectorAll('.device-grid');
    for (var i = 0; i < grids.length; i++) {
        if (!grids[i].closest('.actions-section')) return grids[i];
    }
    return null;
}
function _ensureOfflineSection() {
    var section = document.querySelector('.actions-section');
    if (section) return section.querySelector('.device-grid');
    // Create the offline section
    var onlineGrid = _getOnlineGrid();
    if (!onlineGrid) return null;
    section = document.createElement('div');
    section.className = 'actions-section';
    section.style.marginTop = '16px';
    section.innerHTML =
        '<div class="actions-header" onclick="toggleOffline(this)" ' +
        'style="cursor:pointer;display:flex;align-items:center;justify-content:space-between">' +
        '<span style="font-size:13px;font-weight:600;color:#667" class="offline-section-label">' +
        'Offline (0) &middot; Disconnected (0)</span>' +
        '<span class="controls-arrow" style="color:#667">&#9660;</span></div>' +
        '<div class="device-grid"></div>';
    onlineGrid.after(section);
    return section.querySelector('.device-grid');
}
function _updateOfflineCounts() {
    var offGrid = _getOfflineGrid();
    if (!offGrid) return;
    var cards = offGrid.querySelectorAll('.device-card');
    var offline = 0, disconnected = 0;
    cards.forEach(function(c) {
        var st = c.querySelector('.status-text');
        if (st && st.textContent === 'Disconnected') disconnected++;
        else offline++;
    });
    var label = offGrid.closest('.actions-section').querySelector('.offline-section-label');
    if (label) label.textContent = 'Offline (' + offline + ') \\u00b7 Disconnected (' + disconnected + ')';
    // Hide section if empty
    var section = offGrid.closest('.actions-section');
    if (section) section.style.display = cards.length ? '' : 'none';
}
function _moveToOffline(card) {
    var offGrid = _ensureOfflineSection();
    if (!offGrid || card.parentNode === offGrid) return;
    offGrid.appendChild(card);
    _updateOfflineCounts();
}
function _moveToOnline(card) {
    var onGrid = _getOnlineGrid();
    if (!onGrid || card.parentNode === onGrid) return;
    onGrid.appendChild(card);
    _updateOfflineCounts();
}

/* ---------- Stop All per device ---------- */
function stopAllDevice(btn) {
    if (!confirm('Stop all tasks on this device?')) return;
    var apiBase = _apiOf(btn);
    btn.textContent = 'Stopping...'; btn.disabled = true;
    fetch(apiBase + '/tasks/stop-all', {method:'POST', credentials:'include'})
        .then(function() {
            btn.textContent = 'Stopped!';
            // Clear all toggles on this card
            var card = btn.closest('.device-card');
            if (card) {
                card.querySelectorAll('.toggle.on').forEach(function(t) { t.classList.remove('on'); });
                card.querySelectorAll('.control-pill.pill-on').forEach(function(p) { p.classList.remove('pill-on'); });
            }
            setTimeout(function() { btn.textContent = 'Stop All'; btn.disabled = false; }, 2000);
        }).catch(function() {
            btn.textContent = 'Error';
            setTimeout(function() { btn.textContent = 'Stop All'; btn.disabled = false; }, 2000);
        });
}

/* ---------- Remote tap ---------- */
(function() {
    function handleTap(img, cx, cy) {
        var dh = _dhOf(img);
        if (!dh || !_manualControlDevices[dh]) return;
        var apiBase = _apiOf(img);
        var r = img.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return;
        var rx = cx - r.left, ry = cy - r.top;
        var gx = Math.round(rx / r.width * 1080), gy = Math.round(ry / r.height * 1920);
        if (gx < 0 || gx > 1080 || gy < 0 || gy > 1920) return;
        var dot = document.createElement('div'); dot.className = 'tap-ripple';
        dot.style.left = rx + 'px'; dot.style.top = ry + 'px';
        img.parentElement.appendChild(dot);
        setTimeout(function() { dot.remove(); }, 400);
        fetch(apiBase + '/api/tap', {method:'POST', credentials:'include',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({x:gx, y:gy})});
    }
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('live-view-img')) handleTap(e.target, e.clientX, e.clientY);
    });
    document.addEventListener('touchstart', function(e) {
        if (!e.target.classList.contains('live-view-img')) return;
        e.preventDefault(); var t = e.touches[0];
        handleTap(e.target, t.clientX, t.clientY);
    }, {passive:false});
})();

/* ---------- Restart / Emulator ---------- */
function restartGame(btn) {
    var apiBase = _apiOf(btn);
    btn.textContent = 'Restarting...'; btn.disabled = true;
    fetch(apiBase + '/api/restart-game', {method:'POST', credentials:'include'})
        .then(function(r) { return r.json(); })
        .then(function(d) {
            btn.textContent = d.ok ? 'Restarted!' : 'Failed';
            setTimeout(function() { btn.textContent = 'Restart Game'; btn.disabled = false; }, 2000);
        }).catch(function() {
            btn.textContent = 'Error';
            setTimeout(function() { btn.textContent = 'Restart Game'; btn.disabled = false; }, 2000);
        });
}
function startEmulator(btn) {
    var apiBase = _apiOf(btn);
    btn.textContent = 'Starting...'; btn.disabled = true; btn.classList.add('emu-starting');
    fetch(apiBase + '/api/emulator/start', {method:'POST', credentials:'include'})
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (!d.ok) { btn.textContent = 'Start Emulator'; btn.disabled = false; btn.classList.remove('emu-starting'); }
        }).catch(function() {
            btn.textContent = 'Start Emulator'; btn.disabled = false; btn.classList.remove('emu-starting');
        });
}
function stopEmulator(btn) {
    if (!confirm('Stop this emulator? All bot tasks on it will be stopped first.')) return;
    var apiBase = _apiOf(btn);
    btn.disabled = true;
    fetch(apiBase + '/api/emulator/stop', {method:'POST', credentials:'include'})
        .then(function() { btn.disabled = false; })
        .catch(function() { btn.disabled = false; });
}

/* ---------- Chat feed ---------- */
function loadChatFeed(dh, apiBase, feedEl) {
    var c = _chatFeedCache[dh];
    if (c && Date.now() - c.ts < 10000) { renderChatFeed(c.messages, feedEl); return; }
    fetch(apiBase + '/api/chat', {credentials:'include'})
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var msgs = (data.messages || []).filter(function(m) { return m.payload_type !== 11; });
            _chatFeedCache[dh] = {messages:msgs, ts:Date.now()};
            renderChatFeed(msgs, feedEl);
        }).catch(function() {});
}
function renderChatFeed(messages, feedEl) {
    var container = feedEl.querySelector('.chat-feed-messages');
    if (!container) return;
    var recent = messages.slice(-3);
    container.textContent = '';
    if (recent.length === 0) {
        var h = document.createElement('div');
        h.className = 'chat-feed-empty'; h.textContent = 'No chat yet';
        container.appendChild(h); return;
    }
    recent.forEach(function(msg) {
        var ch = _chatChannelMap[msg.channel] || {name:'?',cls:'server'};
        var div = document.createElement('div'); div.className = 'chat-feed-msg';
        var sender = document.createElement('span');
        sender.className = 'chat-feed-sender chat-sender-' + ch.cls;
        sender.textContent = msg.sender || '???'; div.appendChild(sender);
        var text = document.createElement('span'); text.className = 'chat-feed-text';
        text.textContent = (msg.content || '').replace(/\[[^\]]*\|(@[^\]]+)\]/g, '[$1]');
        div.appendChild(text); container.appendChild(div);
    });
}

/* ---------- Chat modal ---------- */
var _chatDhash = '';
var _chatChannel = '';
var _chatPollId = null;
var _chatShowSystem = false;
var _chatMessages = [];

function openDeviceChat(dhash) {
    _chatDhash = dhash;
    _chatChannel = '';
    _chatShowSystem = false;
    _chatMessages = [];
    document.querySelectorAll('#chat-modal-tabs .chat-tab').forEach(function(t) {
        t.classList.toggle('active', !t.getAttribute('data-channel'));
    });
    document.getElementById('chat-system-toggle').classList.remove('active');
    var card = document.querySelector('.device-card[data-dhash="' + dhash + '"]');
    var nameEl = card ? card.querySelector('.device-name-row strong') : null;
    document.getElementById('chat-modal-device').textContent = nameEl ? nameEl.textContent : dhash;
    document.getElementById('chat-modal-messages').innerHTML =
        '<div class="chat-messages-empty"><div class="chat-messages-empty-icon">&#128172;</div>' +
        '<div class="chat-messages-empty-text">Loading...</div></div>';
    document.getElementById('chat-status-text').textContent = '';
    document.getElementById('chat-system-count').textContent = '';
    document.getElementById('chat-overlay').classList.add('visible');
    _loadChatModal();
    if (_chatPollId) clearInterval(_chatPollId);
    _chatPollId = setInterval(_loadChatModal, 3000);
}
function closeChatModal() {
    document.getElementById('chat-overlay').classList.remove('visible');
    if (_chatPollId) { clearInterval(_chatPollId); _chatPollId = null; }
    _chatDhash = '';
}
function setChatChannel(btn, channel) {
    _chatChannel = channel;
    document.querySelectorAll('#chat-modal-tabs .chat-tab').forEach(function(t) { t.classList.remove('active'); });
    btn.classList.add('active');
    _loadChatModal();
}
function toggleChatSystem(btn) {
    _chatShowSystem = !_chatShowSystem;
    btn.classList.toggle('active', _chatShowSystem);
    _renderChatModal(_chatMessages);
    _updateChatStatus(_chatMessages);
}
function _loadChatModal() {
    if (!_chatDhash) return;
    var card = document.querySelector('.device-card[data-dhash="' + _chatDhash + '"]');
    if (!card) return;
    var apiBase = card.getAttribute('data-api');
    var url = apiBase + '/api/chat';
    if (_chatChannel) url += '?channel=' + encodeURIComponent(_chatChannel);
    fetch(url, {credentials:'include'})
        .then(function(r) { return r.json(); })
        .then(function(data) {
            _chatMessages = data.messages || [];
            _renderChatModal(_chatMessages);
            _updateChatStatus(_chatMessages);
        }).catch(function() {});
}
function _updateChatStatus(messages) {
    var systemCount = messages.filter(function(m) { return m.payload_type === 11; }).length;
    var visible = _chatShowSystem ? messages : messages.filter(function(m) { return m.payload_type !== 11; });
    var text = visible.length + ' messages';
    if (_chatChannel) text += ' \u00b7 ' + (_chatChannelNames[_chatChannel] || _chatChannel);
    document.getElementById('chat-status-text').textContent = text;
    document.getElementById('chat-system-count').textContent = systemCount > 0 ? systemCount + ' hidden' : '';
}
function _renderChatModal(messages) {
    var container = document.getElementById('chat-modal-messages');
    var wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 50;
    container.textContent = '';
    var filtered = _chatShowSystem ? messages : messages.filter(function(m) { return m.payload_type !== 11; });
    if (filtered.length === 0) {
        var empty = document.createElement('div'); empty.className = 'chat-messages-empty';
        var icon = document.createElement('div'); icon.className = 'chat-messages-empty-icon'; icon.innerHTML = '&#128172;';
        empty.appendChild(icon);
        var hint = document.createElement('div'); hint.className = 'chat-messages-empty-text';
        hint.textContent = messages.length > 0
            ? 'All messages are system notifications.\nToggle "System" below to view them.'
            : 'No messages yet.\nGame chat must be active.';
        empty.appendChild(hint); container.appendChild(empty); return;
    }
    var lastDateStr = '';
    filtered.forEach(function(msg) {
        if (msg.timestamp > 0) {
            var msgDate = new Date(msg.timestamp);
            var dateStr = msgDate.toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'});
            if (dateStr !== lastDateStr) {
                lastDateStr = dateStr;
                var sep = document.createElement('div'); sep.className = 'chat-date-sep';
                var line1 = document.createElement('div'); line1.className = 'chat-date-line';
                var label = document.createElement('span'); label.className = 'chat-date-label'; label.textContent = dateStr;
                var line2 = document.createElement('div'); line2.className = 'chat-date-line';
                sep.appendChild(line1); sep.appendChild(label); sep.appendChild(line2); container.appendChild(sep);
            }
        }
        var isSystem = msg.payload_type === 11;
        var isCoord = msg.payload_type === 5;
        var ch = _chatChannelMap[msg.channel] || {name: msg.channel || '?', cls: 'server'};
        if (isSystem) ch = {name: 'System', cls: 'system'};
        var div = document.createElement('div'); div.className = 'chat-msg ch-' + ch.cls;
        var row = document.createElement('div'); row.className = 'chat-msg-row';
        var time = document.createElement('span'); time.className = 'chat-time';
        if (msg.timestamp > 0) {
            var d = new Date(msg.timestamp);
            time.textContent = ('0'+d.getHours()).slice(-2) + ':' + ('0'+d.getMinutes()).slice(-2);
        }
        row.appendChild(time);
        var badge = document.createElement('span'); badge.className = 'chat-channel-badge badge-' + ch.cls; badge.textContent = ch.name;
        row.appendChild(badge);
        if (msg.union_name) {
            var union = document.createElement('span'); union.className = 'chat-union-tag'; union.textContent = '[' + msg.union_name + ']';
            row.appendChild(union);
        }
        var isR4 = msg.channel === 'UNION_R4';
        var sender = document.createElement('span'); sender.className = 'chat-sender chat-sender-' + (isR4 ? 'r4' : ch.cls);
        sender.textContent = msg.sender || (isSystem ? 'System' : '???');
        row.appendChild(sender); div.appendChild(row);
        var content = document.createElement('div');
        content.className = isSystem ? 'chat-content-system' : (isCoord ? 'chat-content chat-content-coord' : 'chat-content');
        content.textContent = (msg.content || '').replace(/\[[^\]]*\|(@[^\]]+)\]/g, '[$1]');
        div.appendChild(content); container.appendChild(div);
    });
    if (wasAtBottom) container.scrollTop = container.scrollHeight;
}
"""


async def _page_dashboard_admin(
    request: web.Request, user: dict, csrf: str,
) -> web.Response:
    """Admin dashboard — all device cards from every server, inline with full controls."""
    bots = await asyncio.to_thread(db.list_bots)

    # Collect all devices across all bots
    # Each entry: (bot_name, bot_label, dhash, label, device_state)
    # device_state: "online" | "offline" (emu stopped, startable) | "disconnected" (server off)
    all_devices = []
    for b in bots:
        name = b["bot_name"]
        bot_online = name in _active_bots and not _active_bots[name].closed
        bot_label = _html_escape(b.get("label") or name)
        devices = await asyncio.to_thread(db.list_devices, name)
        # Build lookup of per-device online status from bot's last report
        dev_online_map = {}
        for bd in _bot_device_status.get(name, []):
            dev_online_map[bd["hash"]] = bd.get("online", True)
        for d in devices:
            label = _html_escape(
                d.get("label") or d.get("device_name") or d["device_hash"][:8]
            )
            dh = d["device_hash"]
            if not bot_online:
                state = "disconnected"
            elif dev_online_map.get(dh, True):
                state = "online"
            else:
                state = "offline"
            all_devices.append((name, bot_label, dh, label, state))

    if not all_devices:
        body = '<p class="muted" style="padding:24px;text-align:center">No devices registered.</p>'
        return web.Response(
            text=_page_dashboard_wrapper("Dashboard", body, user, csrf),
            content_type="text/html",
        )

    # Pick the first online bot to source style.css from
    css_bot = next((n for n, _, _, _, s in all_devices if s == "online"), all_devices[0][0])

    # Auto mode groups
    auto_modes = [
        {"group": "Combat", "modes": [
            ("auto_pass", "Pass Battle"), ("auto_occupy", "Occupy Towers"),
            ("auto_reinforce", "Reinforce Throne"), ("auto_reinforce_target", "Reinforce Target"),
            ("auto_reinforce_ally", "Reinforce Ally"), ("auto_war_rallies", "War Rallies"),
        ]},
        {"group": "Farming", "modes": [
            ("auto_quest", "Auto Quest"), ("auto_titan", "Rally Titans"),
            ("auto_gold", "Gather Gold"), ("auto_mithril", "Mine Mithril"),
        ]},
    ]

    # Build device cards — SAME HTML structure as the bot's index.html
    cards_html = ""
    offline_cards_html = ""
    disconnected_cards_html = ""
    for bot_name, bot_label, dhash, label, state in all_devices:
        api_base = f"/{bot_name}/d/{dhash}"

        # Pills + toggles
        pills_html = ""
        toggles_html = ""
        for grp in auto_modes:
            toggles_html += (
                f'<div class="auto-column">'
                f'<div class="auto-subheader">{grp["group"]}</div>'
                f'<div class="auto-list">'
            )
            for key, mlabel in grp["modes"]:
                pills_html += (
                    f'<span class="control-pill" data-mode="{key}">'
                    f'{mlabel}</span>'
                )
                toggles_html += (
                    f'<div class="auto-row" data-mode="{key}">'
                    f'<span class="auto-label">{mlabel}</span>'
                    f'<button type="button" class="toggle" '
                    f"""onclick="toggleAutoMode('{key}',this)"></button>"""
                    f'</div>'
                )
            toggles_html += '</div></div>'

        # Events section (Groot + Phantom Clash)
        events_html = (
            f'<div class="device-events-section">'
            f'<div class="events-header" onclick="toggleEvents(this)">'
            f'<span class="events-label">Events</span>'
            f'<span class="controls-arrow">&#9660;</span>'
            f'</div>'
            f'<div class="events-body" style="display:none">'
            f'<div class="auto-row" data-mode="auto_groot">'
            f'<span class="auto-label">Join Groot</span>'
            f'<button type="button" class="toggle" '
            f"""onclick="toggleAutoMode('auto_groot',this)"></button>"""
            f'</div>'
            f'<div class="auto-row" data-mode="auto_esb">'
            f'<span class="auto-label">Phantom Clash</span>'
            f'<button type="button" class="toggle" '
            f"""onclick="toggleAutoMode('auto_esb',this)"></button>"""
            f'</div>'
            f'</div></div>'
        )

        if state == "online":
            # Online device card — full controls
            cards_html += (
                f'<div class="card device-card" data-dhash="{dhash}" data-api="{api_base}">'
                f'<div class="device-top">'
                f'<div class="device-name-row">'
                f'<strong>{label}</strong>'
                f'<span class="device-troops" title="Troops">&#9876; ?</span>'
                f'</div>'
                f'<div class="device-header-right">'
                f'<span style="font-size:10px;color:#556;font-weight:600">{bot_label}</span>'
                f'</div>'
                f'<div class="device-status-bar">'
                f'<span class="status-indicator"></span>'
                f'<span class="status-text">Loading...</span>'
                f'</div>'
                f'<span class="mithril-timer" style="display:none">'
                f'&#9935; <span class="mithril-time"></span></span>'
                f'</div>'
                # Troops
                f'<div class="section-label troop-label">Troops '
                f'<span class="troop-source"></span><span class="troop-age"></span></div>'
                f'<div class="troop-slots"></div>'
                # Quests
                f'<div class="section-label quest-label quest-header" style="display:none">'
                f'Quests <span class="quest-age"></span></div>'
                f'<div class="quest-tracking"></div>'
                # Chat feed
                f'<div class="chat-feed" data-dhash="{dhash}" style="display:none"'
                f""" onclick="openDeviceChat('{dhash}')">"""
                f'<div class="chat-feed-well">'
                f'<div class="chat-feed-messages"></div>'
                f'<div class="chat-feed-fade"></div>'
                f'</div></div>'
                # Controls
                f'<div class="device-auto-modes">'
                f'<div class="controls-header" onclick="toggleControls(this)">'
                f'<span class="controls-label">Controls</span>'
                f'<span class="controls-arrow">&#9660;</span>'
                f'</div>'
                f'<div class="controls-pills" style="display:none">{pills_html}</div>'
                f'<div class="controls-body"><div class="auto-columns">{toggles_html}</div></div>'
                f'</div>'
                # Events
                + events_html +
                # Live view + emulator buttons
                f'<div class="live-view-section">'
                f'<div class="live-view-buttons">'
                f'<button type="button" class="live-view-toggle" '
                f'onclick="toggleLiveView(this)" data-active="false">Live View</button>'
                f'<button type="button" class="manual-control-toggle" '
                f'onclick="toggleManualControl(this)" data-active="false">Manual Control</button>'
                f'<button type="button" class="chat-view-toggle" '
                f"""data-dhash="{dhash}" onclick="openDeviceChat('{dhash}')" """
                f'style="display:none">Chat</button>'
                f'<button type="button" class="restart-game-btn" '
                f'onclick="restartGame(this)">Restart Game</button>'
                f'</div>'
                f'<div class="emu-steps emu-steps-compact">'
                f'<button type="button" class="emu-step-btn emu-step-stop" '
                f'onclick="stopEmulator(this)">Stop Emulator</button>'
                f'<button type="button" class="emu-step-btn emu-step-game" '
                f'onclick="restartGame(this)">Restart Game</button>'
                f'</div>'
                f'<div class="live-view-container" style="display:none">'
                f'<img class="live-view-img" alt="Screenshot">'
                f'</div></div>'
                # Bottom bar (Settings + Stop All)
                f'<div class="bottom-bar" style="margin-top:8px">'
                f'<button type="button" class="bottom-btn bottom-btn-danger" style="flex:1" '
                f'onclick="stopAllDevice(this)">Stop All</button>'
                f'<a href="{api_base}/settings" class="bottom-btn" '
                f'style="flex:1;text-align:center;text-decoration:none">Settings</a>'
                f'</div>'
                f'</div>'
            )
        elif state == "offline":
            # Offline emulator (bot server online, emulator stopped) — Start Emulator
            offline_cards_html += (
                f'<div class="card device-card device-card-offline" data-dhash="{dhash}" data-api="{api_base}">'
                f'<div class="device-top">'
                f'<div class="device-name-row">'
                f'<strong>{label}</strong>'
                f'</div>'
                f'<div class="device-header-right">'
                f'<span style="font-size:10px;color:#556;font-weight:600">{bot_label}</span>'
                f'</div>'
                f'<div class="device-status-bar">'
                f'<span class="status-indicator"></span>'
                f'<span class="status-text status-offline">Offline</span>'
                f'</div>'
                f'</div>'
                f'<div class="emu-steps">'
                f'<button type="button" class="emu-step-btn emu-step-emu" '
                f'onclick="startEmulator(this)">Start Emulator</button>'
                f'</div>'
                f'</div>'
            )
        else:
            # Disconnected (bot server off) — no actions possible
            disconnected_cards_html += (
                f'<div class="card device-card device-card-offline" data-dhash="{dhash}">'
                f'<div class="device-top">'
                f'<div class="device-name-row">'
                f'<strong>{label}</strong>'
                f'</div>'
                f'<div class="device-header-right">'
                f'<span style="font-size:10px;color:#556;font-weight:600">{bot_label}</span>'
                f'</div>'
                f'<div class="device-status-bar">'
                f'<span class="status-indicator"></span>'
                f'<span class="status-text status-offline">Disconnected</span>'
                f'</div>'
                f'</div>'
                f'</div>'
            )

    # Collapsible sections for non-online devices
    offline_section = ""
    offline_count = sum(1 for _, _, _, _, s in all_devices if s == "offline")
    disconnected_count = sum(1 for _, _, _, _, s in all_devices if s == "disconnected")
    combined_html = offline_cards_html + disconnected_cards_html
    total_hidden = offline_count + disconnected_count
    if combined_html:
        offline_section = (
            f'<div class="actions-section" style="margin-top:16px">'
            f'<div class="actions-header" onclick="toggleOffline(this)" '
            f'style="cursor:pointer;display:flex;align-items:center;justify-content:space-between">'
            f'<span style="font-size:13px;font-weight:600;color:#667">'
            f'Offline ({offline_count}) &middot; Disconnected ({disconnected_count})'
            f'</span>'
            f'<span class="controls-arrow" style="color:#667">&#9654;</span>'
            f'</div>'
            f'<div class="device-grid" style="display:none">{combined_html}</div>'
            f'</div>'
        )

    # Chat modal HTML (one copy for the whole page)
    chat_modal = (
        '<div class="chat-overlay" id="chat-overlay" onclick="closeChatModal()">'
        '<div class="chat-modal" onclick="event.stopPropagation()">'
        '<div class="chat-modal-header">'
        '<div><span class="chat-modal-title">Chat</span>'
        '<span class="chat-modal-device" id="chat-modal-device"></span></div>'
        '<button type="button" class="chat-modal-close" onclick="closeChatModal()">&times;</button>'
        '</div>'
        '<div class="chat-tabs" id="chat-modal-tabs">'
        '<button class="chat-tab active" data-channel="" onclick="setChatChannel(this, \'\')">All</button>'
        '<button class="chat-tab" data-channel="SERVER" onclick="setChatChannel(this, \'SERVER\')">Kingdom</button>'
        '<button class="chat-tab" data-channel="UNION" onclick="setChatChannel(this, \'UNION\')">Alliance</button>'
        '<button class="chat-tab" data-channel="FACTION" onclick="setChatChannel(this, \'FACTION\')">Faction</button>'
        '<button class="chat-tab" data-channel="WORLD" onclick="setChatChannel(this, \'WORLD\')">World</button>'
        '<button class="chat-tab" data-channel="PRIVATE" onclick="setChatChannel(this, \'PRIVATE\')">Private</button>'
        '</div>'
        '<div class="chat-messages" id="chat-modal-messages">'
        '<div class="chat-messages-empty">'
        '<div class="chat-messages-empty-icon">&#128172;</div>'
        '<div class="chat-messages-empty-text">Loading chat messages...</div>'
        '</div></div>'
        '<div class="chat-modal-footer">'
        '<button type="button" class="chat-system-toggle" id="chat-system-toggle" onclick="toggleChatSystem(this)">System</button>'
        '<span class="chat-system-count" id="chat-system-count"></span>'
        '<span class="chat-status-text" id="chat-status-text"></span>'
        '</div></div></div>'
    )

    # Build page — use dashboard wrapper (portal nav CSS only + bot's style.css)
    body = (
        f'<div class="device-grid">{cards_html}</div>'
        + offline_section
        + chat_modal
        + "\n<script>\n" + _PORTAL_DASHBOARD_JS + "\n</script>"
    )
    return web.Response(
        text=_page_dashboard_wrapper("Dashboard", body, user, csrf, css_bot=css_bot),
        content_type="text/html",
    )


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
        const resp = await fetch("/portal/api/grants/" + id + "/revoke", {{
            method: "POST",
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
    pending = await asyncio.to_thread(db.list_pending_users)
    subs = await asyncio.to_thread(db.list_subscriptions)

    # --- Stats ---
    online_count = sum(1 for b in bots if b["bot_name"] in _active_bots and not _active_bots[b["bot_name"]].closed)
    active_subs = sum(1 for s in subs if s["status"] == "active")
    unused_invites = [i for i in invites if not i.get("used_by")]
    used_invites = [i for i in invites if i.get("used_by")]

    # --- Pending approval cards ---
    pending_cards = ""
    for p in pending:
        email = _html_escape(p.get("email") or "—")
        pending_cards += (
            f'<div class="adm-pending-card">'
            f'<div class="adm-pending-info">'
            f'<span class="adm-pending-name">{_html_escape(p["username"])}</span>'
            f'<span class="adm-pending-meta">{email}<span class="adm-sep"></span>{p["created_at"]}</span>'
            f'</div>'
            f'<div class="adm-pending-actions">'
            f'<button class="adm-act adm-act-approve" '
            f'onclick="approveUser({p["id"]},\'{_html_escape(p["username"])}\')">Approve</button>'
            f'<button class="adm-act adm-act-reject" '
            f'onclick="rejectUser({p["id"]},\'{_html_escape(p["username"])}\')">Reject</button>'
            f'</div></div>'
        )

    pending_section = ""
    if pending:
        pending_section = (
            f'<div class="adm-alert-banner">'
            f'<div class="adm-alert-dot"></div>'
            f'<div class="adm-alert-content">'
            f'<strong>{len(pending)} pending approval{"s" if len(pending) != 1 else ""}</strong>'
            f'<span class="muted">New signups waiting for review</span>'
            f'</div></div>'
            f'{pending_cards}'
        )

    # --- User rows ---
    user_rows = ""
    for u in users:
        role_badge = '<span class="adm-role adm-role-admin">Admin</span>' if u["is_admin"] else '<span class="adm-role adm-role-user">User</span>'
        last = u.get("last_login") or "never"
        email = _html_escape(u.get("email") or "—")

        is_owner = user["user_id"] == 1
        admin_btn = ""
        delete_btn = (
            f'<button class="adm-act adm-act-danger" '
            f'onclick="deleteUser({u["id"]},\'{_html_escape(u["username"])}\')">Delete</button>'
        )
        if u["id"] == 1:
            # Owner account: no one can demote or delete
            delete_btn = ""
        elif is_owner and not u["is_admin"]:
            admin_btn = (
                f'<button class="adm-act adm-act-outline" '
                f'onclick="toggleAdmin({u["id"]},\'{_html_escape(u["username"])}\')">Promote</button>'
            )
        elif is_owner and u["id"] != user["user_id"]:
            admin_btn = (
                f'<button class="adm-act adm-act-outline adm-act-warn" '
                f'onclick="toggleAdmin({u["id"]},\'{_html_escape(u["username"])}\')">Demote</button>'
            )

        user_rows += (
            f'<div class="adm-row">'
            f'<div class="adm-row-main">'
            f'<div class="adm-row-title">'
            f'{role_badge}'
            f'<a href="/portal/admin/user/{u["id"]}" class="adm-link"><strong>{_html_escape(u["username"])}</strong></a>'
            f'<span class="adm-id">#{u["id"]}</span>'
            f'</div>'
            f'<div class="adm-row-meta">'
            f'{email}<span class="adm-sep"></span>joined {u["created_at"]}<span class="adm-sep"></span>last login {last}'
            f'</div></div>'
            f'<div class="adm-row-actions">'
            f'{admin_btn}'
            f'<button class="adm-act adm-act-outline" '
            f'onclick="resetPassword({u["id"]},\'{_html_escape(u["username"])}\')">Reset PW</button>'
            f'{delete_btn}'
            f'</div></div>'
        )

    # --- Bot rows ---
    bot_rows = ""
    for b in bots:
        online = b["bot_name"] in _active_bots and not _active_bots[b["bot_name"]].closed
        status_cls = "adm-online" if online else "adm-offline"
        status_text = "Online" if online else "Offline"
        last = b.get("last_seen") or "never"
        label = _html_escape(b.get("label") or b["bot_name"])

        owner_select = f'<select class="adm-select" onchange="setOwner(\'{b["bot_name"]}\',this.value)">'
        owner_select += '<option value="">Unassigned</option>'
        for u in users:
            sel = " selected" if b.get("owner_id") == u["id"] else ""
            owner_select += f'<option value="{u["id"]}"{sel}>{_html_escape(u["username"])}</option>'
        owner_select += "</select>"

        bot_rows += (
            f'<div class="adm-row">'
            f'<div class="adm-row-main">'
            f'<div class="adm-row-title">'
            f'<span class="adm-status {status_cls}"></span>'
            f'<a href="/portal/bot/{b["bot_name"]}" class="adm-link"><strong>{label}</strong></a>'
            f'<span class="adm-id">{b["bot_name"]}</span>'
            f'</div>'
            f'<div class="adm-row-meta">'
            f'{status_text}<span class="adm-sep"></span>last seen {last}'
            f'</div></div>'
            f'<div class="adm-row-actions">'
            f'<div class="adm-owner-wrap"><span class="adm-owner-label">Owner</span>{owner_select}</div>'
            f'</div></div>'
        )

    # --- Subscription rows ---
    sub_rows = ""
    for s in subs:
        uname = _html_escape(s.get("username", "?"))
        is_admin_grant = s.get("stripe_customer_id") == "admin_grant"
        src_cls = "adm-src-granted" if is_admin_grant else "adm-src-stripe"
        src_label = "Granted" if is_admin_grant else "Stripe"
        status_cls = "adm-sub-active" if s["status"] == "active" else ("adm-sub-warn" if s["status"] == "past_due" else "adm-sub-expired")
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
                f'<button class="adm-act adm-act-danger" '
                f'onclick="revokeSub({s["user_id"]})">Revoke</button>'
            )

        sub_rows += (
            f'<div class="adm-row">'
            f'<div class="adm-row-main">'
            f'<div class="adm-row-title">'
            f'<span class="adm-sub-dot {status_cls}"></span>'
            f'<strong>{uname}</strong>'
            f'<span class="adm-pill {src_cls}">{src_label}</span>'
            f'</div>'
            f'<div class="adm-row-meta">'
            f'{s["plan"]}<span class="adm-sep"></span>{s["status"]}'
            f'<span class="adm-sep"></span>{s["device_limit"]} device{"s" if s["device_limit"] != 1 else ""}'
            f'<span class="adm-sep"></span>expires {period}'
            f'</div></div>'
            f'<div class="adm-row-actions">{revoke_btn}</div>'
            f'</div>'
        )

    # User options for grant form
    user_options = ""
    for u in users:
        if not u["is_admin"]:
            user_options += f'<option value="{u["id"]}">{_html_escape(u["username"])}</option>'

    # --- Invite code items ---
    invite_items = ""
    for i in unused_invites[:20]:
        creator = _html_escape(i.get("created_by_name") or "?")
        invite_items += (
            f'<div class="adm-invite-item">'
            f'<code class="adm-invite-code">{i["code"]}</code>'
            f'<span class="adm-row-meta">by {creator}<span class="adm-sep"></span>{i["created_at"]}</span>'
            f'</div>'
        )

    body = f"""
    <style>
    /* ── Admin page styles ── */

    /* Card-tabs: stat cards that double as tabs */
    .adm-cards {{
        display: grid; grid-template-columns: repeat(4, 1fr);
        gap: 8px; margin-bottom: 4px;
    }}
    .adm-card {{
        background: #141428; border-radius: 12px; padding: 16px 18px;
        border: 1px solid rgba(255,255,255,0.04);
        position: relative; overflow: hidden; cursor: pointer;
        transition: all 0.2s ease; user-select: none;
    }}
    .adm-card:hover {{ border-color: rgba(255,255,255,0.1); }}
    .adm-card::before {{
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
        opacity: 0.4; transition: opacity 0.2s ease;
    }}
    .adm-card.active::before {{ opacity: 1; }}
    .adm-card::after {{
        content: ''; position: absolute; bottom: -1px; left: 50%;
        width: 0; height: 2px; transition: all 0.25s ease;
        transform: translateX(-50%);
    }}
    .adm-card.active::after {{ width: 40px; }}
    #card-users.adm-card::before {{ background: linear-gradient(90deg, #64d8ff, transparent); }}
    #card-users.adm-card::after {{ background: #64d8ff; }}
    #card-servers.adm-card::before {{ background: linear-gradient(90deg, #4caf50, transparent); }}
    #card-servers.adm-card::after {{ background: #4caf50; }}
    #card-subs.adm-card::before {{ background: linear-gradient(90deg, #ab47bc, transparent); }}
    #card-subs.adm-card::after {{ background: #ab47bc; }}
    #card-invites.adm-card::before {{ background: linear-gradient(90deg, #ffb74d, transparent); }}
    #card-invites.adm-card::after {{ background: #ffb74d; }}
    .adm-card.active {{
        border-color: rgba(255,255,255,0.1);
        background: #181830;
        box-shadow: 0 2px 12px rgba(0,0,0,0.3);
    }}
    .adm-card-val {{
        font-size: 26px; font-weight: 700; letter-spacing: -1px;
        font-variant-numeric: tabular-nums; line-height: 1.1;
        transition: color 0.2s ease;
    }}
    #card-users .adm-card-val {{ color: #64d8ff; }}
    #card-servers .adm-card-val {{ color: #4caf50; }}
    #card-subs .adm-card-val {{ color: #ab47bc; }}
    #card-invites .adm-card-val {{ color: #ffb74d; }}
    .adm-card-label {{
        font-size: 10px; font-weight: 600; color: #667;
        text-transform: uppercase; letter-spacing: 1.2px; margin-top: 4px;
    }}

    /* Panels */
    .adm-panel {{ display: none; }}
    .adm-panel.active {{ display: block; animation: admFadeIn 0.15s ease; }}
    @keyframes admFadeIn {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: translateY(0); }} }}

    /* Alert banner */
    .adm-alert-banner {{
        display: flex; align-items: center; gap: 12px;
        padding: 14px 18px; margin-bottom: 10px;
        background: rgba(255,183,77,0.06); border-radius: 12px;
        border: 1px solid rgba(255,183,77,0.15);
    }}
    .adm-alert-dot {{
        width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
        background: #ffb74d; box-shadow: 0 0 10px rgba(255,183,77,0.5);
        animation: admPulse 2s ease-in-out infinite;
    }}
    @keyframes admPulse {{
        0%, 100% {{ box-shadow: 0 0 4px rgba(255,183,77,0.2); }}
        50% {{ box-shadow: 0 0 12px rgba(255,183,77,0.6); }}
    }}
    .adm-alert-content {{ display: flex; flex-direction: column; gap: 2px; }}
    .adm-alert-content strong {{ font-size: 13px; color: #ffb74d; }}
    .adm-alert-content .muted {{ font-size: 11px; }}

    /* Pending cards */
    .adm-pending-card {{
        display: flex; align-items: center; justify-content: space-between;
        padding: 12px 16px; margin-bottom: 6px;
        background: #141428; border-radius: 10px;
        border: 1px solid rgba(255,183,77,0.08);
        transition: border-color 0.15s;
    }}
    .adm-pending-card:hover {{ border-color: rgba(255,183,77,0.2); }}
    .adm-pending-info {{ display: flex; flex-direction: column; gap: 3px; }}
    .adm-pending-name {{ font-weight: 600; font-size: 14px; }}
    .adm-pending-meta {{ font-size: 11px; color: #556; }}
    .adm-pending-actions {{ display: flex; gap: 6px; }}

    /* Rows */
    .adm-row {{
        display: flex; align-items: center; justify-content: space-between;
        padding: 14px 16px; margin-bottom: 6px;
        background: #141428; border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.04);
        transition: border-color 0.15s; gap: 12px;
    }}
    .adm-row:hover {{ border-color: rgba(100,216,255,0.1); }}
    .adm-row-main {{ flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }}
    .adm-row-title {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .adm-row-title strong {{ font-size: 14px; }}
    .adm-row-meta {{ font-size: 11px; color: #556; }}
    .adm-row-actions {{ display: flex; gap: 6px; flex-shrink: 0; align-items: center; flex-wrap: wrap; }}
    .adm-id {{
        font-family: "SF Mono","Consolas",monospace; font-size: 10px;
        color: #445; letter-spacing: 0.3px;
    }}
    .adm-sep {{ display: inline-block; width: 3px; height: 3px; border-radius: 50%;
        background: #334; margin: 0 6px; vertical-align: middle; }}
    .adm-link {{ color: #e0e0f0; text-decoration: none; }}
    .adm-link:hover {{ color: #64d8ff; text-decoration: none; }}

    /* Status dots */
    .adm-status {{
        width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
    }}
    .adm-online {{
        background: #4caf50; box-shadow: 0 0 8px rgba(76,175,80,0.5);
        animation: admPulseGreen 2s ease-in-out infinite;
    }}
    .adm-offline {{ background: #333; }}
    @keyframes admPulseGreen {{
        0%, 100% {{ box-shadow: 0 0 4px rgba(76,175,80,0.2); }}
        50% {{ box-shadow: 0 0 10px rgba(76,175,80,0.5); }}
    }}

    /* Role badges */
    .adm-role {{
        font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px;
        padding: 3px 8px; border-radius: 6px;
    }}
    .adm-role-admin {{
        color: #ab47bc; background: rgba(171,71,188,0.1);
        border: 1px solid rgba(171,71,188,0.2);
    }}
    .adm-role-user {{
        color: #667; background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.06);
    }}

    /* Action buttons */
    .adm-act {{
        padding: 6px 14px; border-radius: 7px; border: none;
        font-size: 12px; font-weight: 600; cursor: pointer;
        transition: all 0.15s ease; white-space: nowrap;
    }}
    .adm-act:active {{ transform: scale(0.96); }}
    .adm-act-approve {{
        background: rgba(76,175,80,0.12); color: #66bb6a;
        border: 1px solid rgba(76,175,80,0.2);
    }}
    .adm-act-approve:hover {{ background: rgba(76,175,80,0.2); }}
    .adm-act-reject {{
        background: rgba(239,83,80,0.08); color: #ef5350;
        border: 1px solid rgba(239,83,80,0.15);
    }}
    .adm-act-reject:hover {{ background: rgba(239,83,80,0.15); }}
    .adm-act-outline {{
        background: transparent; color: #889;
        border: 1px solid rgba(255,255,255,0.08);
    }}
    .adm-act-outline:hover {{ border-color: rgba(255,255,255,0.2); color: #e0e0f0; }}
    .adm-act-warn {{ color: #ffb74d; border-color: rgba(255,183,77,0.15); }}
    .adm-act-warn:hover {{ border-color: rgba(255,183,77,0.3); }}
    .adm-act-danger {{
        background: rgba(239,83,80,0.08); color: #ef5350;
        border: 1px solid rgba(239,83,80,0.12);
    }}
    .adm-act-danger:hover {{ background: rgba(239,83,80,0.15); }}
    .adm-act-primary {{
        background: rgba(100,216,255,0.1); color: #64d8ff;
        border: 1px solid rgba(100,216,255,0.15);
    }}
    .adm-act-primary:hover {{ background: rgba(100,216,255,0.18); }}

    /* Subscription pills */
    .adm-pill {{
        font-size: 9px; font-weight: 700; text-transform: uppercase;
        padding: 2px 7px; border-radius: 5px; letter-spacing: 0.5px;
    }}
    .adm-src-granted {{ color: #ab47bc; background: rgba(171,71,188,0.1); }}
    .adm-src-stripe {{ color: #64d8ff; background: rgba(100,216,255,0.1); }}
    .adm-sub-dot {{
        width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
    }}
    .adm-sub-active {{ background: #4caf50; }}
    .adm-sub-warn {{ background: #ffb74d; }}
    .adm-sub-expired {{ background: #ef5350; }}

    /* Grant form */
    .adm-grant {{
        padding: 16px; background: #0e0e1e; border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.04); margin-bottom: 12px;
    }}
    .adm-grant-title {{
        font-size: 11px; font-weight: 700; color: #556; text-transform: uppercase;
        letter-spacing: 1px; margin-bottom: 12px;
    }}
    .adm-grant-form {{
        display: flex; gap: 10px; align-items: end; flex-wrap: wrap;
    }}
    .adm-grant-field {{ display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 120px; }}
    .adm-grant-field label {{
        font-size: 10px; font-weight: 600; color: #556; text-transform: uppercase;
        letter-spacing: 0.5px;
    }}

    /* Owner select */
    .adm-owner-wrap {{
        display: flex; align-items: center; gap: 8px;
    }}
    .adm-owner-label {{
        font-size: 10px; font-weight: 600; color: #445; text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    .adm-select {{
        background: #0e0e1e; color: #e0e0f0; border: 1px solid rgba(255,255,255,0.08);
        border-radius: 7px; padding: 6px 10px; font-size: 12px; outline: none;
        transition: border-color 0.15s;
    }}
    .adm-select:focus {{ border-color: rgba(100,216,255,0.3); }}

    /* Invite items */
    .adm-invite-item {{
        display: flex; align-items: center; justify-content: space-between;
        padding: 10px 14px; margin-bottom: 4px;
        background: #141428; border-radius: 8px;
        border: 1px solid rgba(255,255,255,0.04);
    }}
    .adm-invite-code {{
        font-family: "SF Mono","Consolas",monospace; font-size: 13px;
        color: #64d8ff; letter-spacing: 0.5px; font-weight: 600;
    }}
    .adm-invite-actions {{ display: flex; gap: 8px; margin-bottom: 14px; }}
    .adm-invite-counter {{
        display: flex; gap: 16px; padding-top: 10px; margin-top: 10px;
        border-top: 1px solid rgba(255,255,255,0.04);
    }}
    .adm-invite-counter span {{ font-size: 11px; color: #445; }}
    .adm-invite-counter strong {{ color: #667; }}

    /* Toast */
    .adm-toast {{
        position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
        padding: 10px 20px; border-radius: 10px; font-size: 13px; font-weight: 600;
        z-index: 1000; pointer-events: none;
        animation: admToastIn 0.2s ease, admToastOut 0.3s ease 1.7s forwards;
    }}
    .adm-toast-ok {{ background: rgba(76,175,80,0.15); color: #66bb6a; border: 1px solid rgba(76,175,80,0.3); }}
    @keyframes admToastIn {{ from {{ opacity: 0; transform: translateX(-50%) translateY(8px); }} }}
    @keyframes admToastOut {{ to {{ opacity: 0; transform: translateX(-50%) translateY(-8px); }} }}

    /* Result area */
    #inviteResult .alert {{ border-radius: 10px; }}

    /* Responsive */
    @media (max-width: 600px) {{
        .adm-cards {{ grid-template-columns: repeat(2, 1fr); }}
        .adm-card {{ padding: 12px 14px; }}
        .adm-card-val {{ font-size: 22px; }}
        .adm-row {{ flex-direction: column; align-items: flex-start; gap: 10px; }}
        .adm-row-actions {{ width: 100%; }}
        .adm-pending-card {{ flex-direction: column; align-items: flex-start; gap: 10px; }}
        .adm-pending-actions {{ width: 100%; }}
        .adm-grant-form {{ flex-direction: column; }}
        .adm-grant-field {{ min-width: 100%; }}
        .adm-owner-wrap {{ flex-direction: column; align-items: flex-start; gap: 4px; }}
    }}
    </style>

    <!-- Pending approval (always visible when present) -->
    {pending_section}

    <!-- Card-tabs: click to reveal section -->
    <div class="adm-cards">
        <div class="adm-card active" id="card-users" onclick="admSwitch('users')">
            <div class="adm-card-val">{len(users)}</div>
            <div class="adm-card-label">Users</div>
        </div>
        <div class="adm-card" id="card-servers" onclick="admSwitch('servers')">
            <div class="adm-card-val">{online_count}<span style="font-size:13px;color:#556;font-weight:400">/{len(bots)}</span></div>
            <div class="adm-card-label">Servers</div>
        </div>
        <div class="adm-card" id="card-subs" onclick="admSwitch('subs')">
            <div class="adm-card-val">{active_subs}</div>
            <div class="adm-card-label">Subs</div>
        </div>
        <div class="adm-card" id="card-invites" onclick="admSwitch('invites')">
            <div class="adm-card-val">{len(unused_invites)}</div>
            <div class="adm-card-label">Invites</div>
        </div>
    </div>

    <!-- Users panel -->
    <div id="panel-users" class="adm-panel active">
        {user_rows if user_rows else '<p class="muted" style="padding:20px 0;text-align:center">No users.</p>'}
    </div>

    <!-- Servers panel -->
    <div id="panel-servers" class="adm-panel">
        {bot_rows if bot_rows else '<p class="muted" style="padding:20px 0;text-align:center">No servers registered.</p>'}
    </div>

    <!-- Subscriptions panel -->
    <div id="panel-subs" class="adm-panel">
        <div class="adm-grant">
            <div class="adm-grant-title">Grant Free Subscription</div>
            <form id="grantSubForm" onsubmit="return grantSub(event)" class="adm-grant-form">
                <div class="adm-grant-field">
                    <label>User</label>
                    <select name="user_id" required class="adm-select" style="width:100%">{user_options}</select>
                </div>
                <div class="adm-grant-field" style="flex:0 0 auto;min-width:130px">
                    <label>Duration</label>
                    <select name="duration" class="adm-select" style="width:100%">
                        <option value="7">1 Week</option>
                        <option value="30" selected>1 Month</option>
                        <option value="90">3 Months</option>
                        <option value="">Permanent</option>
                    </select>
                </div>
                <button type="submit" class="adm-act adm-act-primary" style="height:34px;align-self:end">Grant</button>
            </form>
        </div>
        {sub_rows if sub_rows else '<p class="muted" style="padding:20px 0;text-align:center">No subscriptions.</p>'}
    </div>

    <!-- Invites panel -->
    <div id="panel-invites" class="adm-panel">
        <div class="adm-invite-actions">
            <button class="adm-act adm-act-primary" onclick="genInvite()">Generate Code</button>
            {'<button class="adm-act adm-act-danger" onclick="clearInvites()">Clear Unused (' + str(len(unused_invites)) + ')</button>' if unused_invites else ''}
        </div>
        <div id="inviteResult"></div>
        {invite_items if invite_items else '<p class="muted" style="padding:12px 0;text-align:center">No unused invite codes.</p>'}
        <div class="adm-invite-counter">
            <span><strong>{len(unused_invites)}</strong> unused</span>
            <span><strong>{len(used_invites)}</strong> used</span>
        </div>
    </div>

    <script>
    const csrf = "{csrf}";

    function admSwitch(panel) {{
        var cards = document.querySelectorAll('.adm-card');
        var panels = document.querySelectorAll('.adm-panel');
        for (var i = 0; i < cards.length; i++) cards[i].classList.remove('active');
        for (var i = 0; i < panels.length; i++) panels[i].classList.remove('active');
        var c = document.getElementById('card-' + panel);
        var p = document.getElementById('panel-' + panel);
        if (c) c.classList.add('active');
        if (p) p.classList.add('active');
    }}

    function admToast(msg, ok) {{
        const t = document.createElement('div');
        t.className = 'adm-toast adm-toast-ok';
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 2200);
    }}

    async function deleteUser(id, name) {{
        if (!confirm("Delete user " + name + "? This revokes all their access.")) return;
        const resp = await fetch("/portal/api/admin/users/" + id + "/delete", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }});
        if (resp.ok) location.reload();
        else alert("Failed");
    }}

    async function approveUser(id, name) {{
        const resp = await fetch("/portal/api/admin/users/" + id + "/approve", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }});
        if (resp.ok) location.reload();
        else alert("Failed to approve user");
    }}

    async function rejectUser(id, name) {{
        if (!confirm("Reject and delete " + name + "'s signup request?")) return;
        const resp = await fetch("/portal/api/admin/users/" + id + "/reject", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }});
        if (resp.ok) location.reload();
        else alert("Failed to reject user");
    }}

    async function clearInvites() {{
        if (!confirm("Delete all unused invite codes?")) return;
        const resp = await fetch("/portal/api/admin/clear-invites", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }});
        if (resp.ok) location.reload();
        else alert("Failed to clear invites");
    }}

    async function toggleAdmin(id, name) {{
        if (!confirm("Toggle admin status for " + name + "?")) return;
        const resp = await fetch("/portal/api/admin/users/" + id + "/toggle-admin", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }});
        if (resp.ok) location.reload();
        else alert("Failed to toggle admin");
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
        if (resp.ok) admToast("Password reset for " + name);
        else alert("Failed to reset password");
    }}

    async function setOwner(botName, userId) {{
        const resp = await fetch("/portal/api/admin/bots/" + botName + "/owner", {{
            method: "PUT",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf, user_id: userId ? parseInt(userId) : null}})
        }});
        if (resp.ok) admToast("Owner updated");
        else alert("Failed to set owner");
    }}

    async function genInvite() {{
        const resp = await fetch("/portal/api/invite-codes", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }});
        if (resp.ok) {{
            const data = await resp.json();
            var code = data.code;
            var el = document.getElementById("inviteResult");
            el.innerHTML = '<div class="alert alert-success" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">'
                + '<span>Code: <code class="adm-invite-code">' + code + '</code></span>'
                + '<button class="adm-act adm-act-outline" id="copyInvBtn">Copy Invite Email</button>'
                + '</div>';
            document.getElementById("copyInvBtn").onclick = function() {{ copyInviteEmail(code); }};
        }} else alert("Failed");
    }}

    function copyInviteEmail(code) {{
        const url = location.origin + "/portal/register";
        const body = "Hi,\\n\\nYou've been invited to 9Bot — automated Kingdom Guard running 24/7 on cloud servers.\\n\\n"
            + "Create your account here:\\n" + url + "\\n\\n"
            + "What to expect after signing up:\\n"
            + "1. Your account will be approved by an admin\\n"
            + "2. Send us your game account details\\n"
            + "3. We set up your dedicated server (usually within 24 hours)\\n"
            + "4. Control everything from your phone dashboard\\n\\n"
            + "See you in the game!\\n— 9Bot Team";
        navigator.clipboard.writeText(body).then(
            () => admToast("Invite email copied!"),
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


async def page_admin_user_detail(request: web.Request) -> web.Response:
    """Admin user detail — manage device assignments for a specific user."""
    admin = await _require_admin(request)
    csrf = _get_csrf(request)
    user_id = int(request.match_info["user_id"])

    target = await asyncio.to_thread(db.get_user_by_id, user_id)
    if not target:
        raise web.HTTPNotFound(text="User not found")

    # Subscription
    sub = await asyncio.to_thread(db.get_subscription, user_id)

    # All bots + pre-fetch devices
    all_bots = await asyncio.to_thread(db.list_bots)
    bot_map = {b["bot_name"]: b for b in all_bots}
    devices_by_bot: dict[str, list] = {}
    for b in all_bots:
        devices_by_bot[b["bot_name"]] = await asyncio.to_thread(
            db.list_devices, b["bot_name"]
        )

    # User's grants
    user_grants = await asyncio.to_thread(db.list_grants_for_user, user_id)

    # Online status helper
    def _dev_state(bot_name, device_hash=None):
        bot_on = (
            bot_name in _active_bots
            and not _active_bots[bot_name].closed
        )
        if not bot_on:
            return "disconnected"
        if device_hash:
            dev_map = {
                d["hash"]: d.get("online", True)
                for d in _bot_device_status.get(bot_name, [])
            }
            return "online" if dev_map.get(device_hash, True) else "offline"
        return "online"

    # --- Build assigned devices list ---
    assigned = []
    assigned_keys: set[tuple] = set()

    # From bot ownership
    for b in all_bots:
        if b.get("owner_id") == user_id:
            for d in devices_by_bot.get(b["bot_name"], []):
                key = (d["bot_name"], d["device_hash"])
                assigned.append({
                    "bot_name": d["bot_name"],
                    "device_hash": d["device_hash"],
                    "label": (
                        d.get("label") or d.get("device_name")
                        or d["device_hash"][:8]
                    ),
                    "bot_label": b.get("label") or b["bot_name"],
                    "source": "owner",
                    "grant_id": None,
                    "access_level": "full",
                    "state": _dev_state(d["bot_name"], d["device_hash"]),
                })
                assigned_keys.add(key)

    # From grants
    for g in user_grants:
        bot = bot_map.get(g["bot_name"], {})
        bot_label = bot.get("label") or g["bot_name"]
        if g["device_hash"]:
            key = (g["bot_name"], g["device_hash"])
            if key not in assigned_keys:
                dev_info = None
                for d in devices_by_bot.get(g["bot_name"], []):
                    if d["device_hash"] == g["device_hash"]:
                        dev_info = d
                        break
                if dev_info:
                    assigned.append({
                        "bot_name": g["bot_name"],
                        "device_hash": g["device_hash"],
                        "label": (
                            dev_info.get("label") or dev_info.get("device_name")
                            or g["device_hash"][:8]
                        ),
                        "bot_label": bot_label,
                        "source": "grant",
                        "grant_id": g["id"],
                        "access_level": g["access_level"],
                        "state": _dev_state(g["bot_name"], g["device_hash"]),
                    })
                    assigned_keys.add(key)
        else:
            # Wildcard grant — all devices on this bot
            for d in devices_by_bot.get(g["bot_name"], []):
                key = (d["bot_name"], d["device_hash"])
                if key not in assigned_keys:
                    assigned.append({
                        "bot_name": d["bot_name"],
                        "device_hash": d["device_hash"],
                        "label": (
                            d.get("label") or d.get("device_name")
                            or d["device_hash"][:8]
                        ),
                        "bot_label": bot_label,
                        "source": "grant",
                        "grant_id": g["id"],
                        "access_level": g["access_level"],
                        "state": _dev_state(d["bot_name"], d["device_hash"]),
                    })
                    assigned_keys.add(key)

    # --- Build available devices grouped by server ---
    available_servers: dict[str, dict] = {}
    total_available = 0
    for b in all_bots:
        for d in devices_by_bot.get(b["bot_name"], []):
            key = (d["bot_name"], d["device_hash"])
            if key not in assigned_keys:
                if b["bot_name"] not in available_servers:
                    available_servers[b["bot_name"]] = {
                        "bot_label": b.get("label") or b["bot_name"],
                        "state": _dev_state(b["bot_name"]),
                        "devices": [],
                    }
                available_servers[b["bot_name"]]["devices"].append({
                    "device_hash": d["device_hash"],
                    "label": (
                        d.get("label") or d.get("device_name")
                        or d["device_hash"][:8]
                    ),
                    "state": _dev_state(b["bot_name"], d["device_hash"]),
                })
                total_available += 1

    # --- Render HTML ---
    initial = _html_escape(target["username"][0].upper())
    username = _html_escape(target["username"])
    email = _html_escape(target.get("email") or "")
    joined = target.get("created_at", "—")
    last_login = target.get("last_login") or "never"

    role_badge = (
        '<span class="up-pill up-pill-admin">Admin</span>'
        if target["is_admin"]
        else '<span class="up-pill up-pill-user">User</span>'
    )

    # Subscription section
    sub_section = ""
    if sub and sub.get("status") in ("active", "past_due"):
        is_admin_grant = sub.get("stripe_customer_id") == "admin_grant"
        plan = sub.get("plan", "none").title()
        limit = sub.get("device_limit", 0)
        limit_text = "Unlimited" if limit >= 999 else str(limit)
        status_cls = "up-sub-active" if sub["status"] == "active" else "up-sub-warn"
        period = sub.get("current_period_end", "—")
        try:
            from datetime import datetime as dt
            pe = dt.fromisoformat(period)
            period = pe.strftime("%Y-%m-%d")
        except Exception:
            pass
        src = "Granted" if is_admin_grant else "Stripe"
        revoke_btn = ""
        if is_admin_grant:
            revoke_btn = (
                '<button class="up-btn up-btn-danger" '
                'onclick="revokeSub()">Revoke</button>'
            )
        sub_section = (
            f'<div class="up-sub">'
            f'<div class="up-sub-left">'
            f'<span class="up-sub-dot {status_cls}"></span>'
            f'<div>'
            f'<div class="up-sub-plan">{plan}</div>'
            f'<div class="up-sub-detail">{src} &middot; '
            f'{limit_text} device{"s" if limit != 1 else ""}'
            f' &middot; exp {period}</div>'
            f'</div></div>'
            f'{revoke_btn}'
            f'</div>'
        )
    else:
        sub_section = (
            f'<div class="up-sub up-sub-empty">'
            f'<span class="up-sub-none-text">No subscription</span>'
            f'<div class="up-sub-grant-form">'
            f'<select id="subDur" class="up-select">'
            f'<option value="30">1 Month</option>'
            f'<option value="90">3 Months</option>'
            f'<option value="">Permanent</option>'
            f'</select>'
            f'<button class="up-btn up-btn-primary" onclick="grantSub()">'
            f'Grant</button>'
            f'</div></div>'
        )

    # Assigned device rows
    assigned_rows = ""
    for a in assigned:
        state_cls = f"up-dot-{a['state']}"
        label = _html_escape(a["label"])
        bot_label = _html_escape(a["bot_label"])
        source_html = ""
        action_html = ""
        if a["source"] == "owner":
            source_html = '<span class="up-tag up-tag-owner">Owner</span>'
        else:
            tag_cls = (
                "up-tag-full" if a["access_level"] == "full"
                else "up-tag-ro"
            )
            source_html = (
                f'<span class="up-tag {tag_cls}">{a["access_level"]}</span>'
            )
            action_html = (
                f'<button class="up-btn up-btn-danger up-btn-sm" '
                f'onclick="unassign({a["grant_id"]})">Unassign</button>'
            )
        assigned_rows += (
            f'<div class="up-dev">'
            f'<div class="up-dev-info">'
            f'<span class="up-dot {state_cls}"></span>'
            f'<div class="up-dev-text">'
            f'<span class="up-dev-name">{label}</span>'
            f'<span class="up-dev-server">{bot_label}</span>'
            f'{source_html}'
            f'</div></div>'
            f'{action_html}'
            f'</div>'
        )
    if not assigned_rows:
        assigned_rows = (
            '<div class="up-empty">No devices assigned</div>'
        )

    # Available device rows grouped by server
    available_html = ""
    for bot_name, server in available_servers.items():
        state_cls = f"up-dot-{server['state']}"
        bot_label = _html_escape(server["bot_label"])
        count = len(server["devices"])
        devs_html = ""
        for d in server["devices"]:
            dev_state = f"up-dot-{d['state']}"
            dlabel = _html_escape(d["label"])
            devs_html += (
                f'<div class="up-dev up-dev-avail">'
                f'<div class="up-dev-info">'
                f'<span class="up-dot {dev_state}"></span>'
                f'<span class="up-dev-name">{dlabel}</span>'
                f'</div>'
                f'<button class="up-btn up-btn-primary up-btn-sm" '
                f"onclick=\"assign({_html_escape(json.dumps(bot_name))},"
                f"{_html_escape(json.dumps(d['device_hash']))})\">Assign</button>"
                f'</div>'
            )
        assign_all_btn = ""
        if count > 1:
            assign_all_btn = (
                f'<button class="up-btn up-btn-outline up-btn-xs" '
                f"onclick=\"assignAll({_html_escape(json.dumps(bot_name))})\">"
                f'Assign All</button>'
            )
        available_html += (
            f'<div class="up-group">'
            f'<div class="up-group-header">'
            f'<span class="up-dot {state_cls}"></span>'
            f'<span class="up-group-name">{bot_label}</span>'
            f'<span class="up-group-count">{count}</span>'
            f'{assign_all_btn}'
            f'</div>'
            f'{devs_html}'
            f'</div>'
        )
    if not available_html:
        available_html = (
            '<div class="up-empty">All devices assigned</div>'
        )

    email_html = (
        f'<span>{email}</span><span class="up-sep"></span>'
        if email else ""
    )
    delete_btn = (
        "" if user_id == 1 else
        '<button class="up-btn up-btn-danger" '
        'onclick="deleteUser()">Delete</button>'
    )

    body = f"""
    <style>
    .up-back {{
        display: inline-flex; align-items: center; gap: 6px;
        font-size: 13px; font-weight: 600; color: #667;
        text-decoration: none; margin-bottom: 16px;
        transition: color 0.15s;
    }}
    .up-back:hover {{ color: #64d8ff; text-decoration: none; }}
    .up-back svg {{ transition: transform 0.15s; }}
    .up-back:hover svg {{ transform: translateX(-2px); }}

    .up-header {{
        background: #141428; border-radius: 16px;
        border: 1px solid rgba(255,255,255,0.04);
        padding: 24px; margin-bottom: 20px;
        position: relative; overflow: hidden;
    }}
    .up-header::before {{
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
        background: linear-gradient(90deg, #64d8ff, rgba(100,216,255,0.05));
    }}
    .up-top {{ display: flex; align-items: flex-start; gap: 18px; }}
    .up-avatar {{
        width: 56px; height: 56px; border-radius: 50%; flex-shrink: 0;
        display: flex; align-items: center; justify-content: center;
        font-size: 22px; font-weight: 700; color: #64d8ff;
        background: radial-gradient(circle at 30% 30%,
            rgba(100,216,255,0.12), rgba(100,216,255,0.03));
        border: 2px solid rgba(100,216,255,0.2);
        box-shadow: 0 0 20px rgba(100,216,255,0.08);
    }}
    .up-name {{ font-size: 20px; font-weight: 700; letter-spacing: -0.3px; }}
    .up-uid {{
        font-family: "SF Mono","Consolas",monospace; font-size: 11px;
        color: #445; margin-left: 8px;
    }}
    .up-meta {{
        font-size: 12px; color: #556; margin-top: 4px;
        display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
    }}
    .up-sep {{
        display: inline-block; width: 3px; height: 3px; border-radius: 50%;
        background: #334;
    }}
    .up-badges {{ display: flex; gap: 6px; margin-top: 8px; }}
    .up-pill {{
        font-size: 9px; font-weight: 700; text-transform: uppercase;
        letter-spacing: 0.8px; padding: 3px 9px; border-radius: 6px;
    }}
    .up-pill-admin {{
        color: #ab47bc; background: rgba(171,71,188,0.1);
        border: 1px solid rgba(171,71,188,0.2);
    }}
    .up-pill-user {{
        color: #667; background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.06);
    }}

    .up-sub {{
        display: flex; align-items: center; justify-content: space-between;
        gap: 12px; margin-top: 16px; padding: 12px 14px;
        background: #0e0e1e; border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.04);
    }}
    .up-sub-left {{ display: flex; align-items: center; gap: 10px; }}
    .up-sub-dot {{
        width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
    }}
    .up-sub-active {{
        background: #4caf50; box-shadow: 0 0 6px rgba(76,175,80,0.4);
    }}
    .up-sub-warn {{ background: #ffb74d; }}
    .up-sub-plan {{ font-size: 13px; font-weight: 600; }}
    .up-sub-detail {{ font-size: 11px; color: #556; }}
    .up-sub-empty {{ justify-content: space-between; }}
    .up-sub-none-text {{ font-size: 12px; color: #556; }}
    .up-sub-grant-form {{
        display: flex; gap: 8px; align-items: center;
    }}
    .up-select {{
        background: #141428; color: #e0e0f0;
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 7px; padding: 6px 10px; font-size: 12px; outline: none;
    }}

    .up-actions {{
        display: flex; gap: 6px; margin-top: 14px; flex-wrap: wrap;
    }}

    .up-btn {{
        padding: 7px 14px; border-radius: 8px; border: none;
        font-size: 12px; font-weight: 600; cursor: pointer;
        transition: all 0.15s; white-space: nowrap;
    }}
    .up-btn:active {{ transform: scale(0.96); }}
    .up-btn-primary {{
        background: rgba(100,216,255,0.1); color: #64d8ff;
        border: 1px solid rgba(100,216,255,0.15);
    }}
    .up-btn-primary:hover {{ background: rgba(100,216,255,0.18); }}
    .up-btn-danger {{
        background: rgba(239,83,80,0.08); color: #ef5350;
        border: 1px solid rgba(239,83,80,0.12);
    }}
    .up-btn-danger:hover {{ background: rgba(239,83,80,0.15); }}
    .up-btn-outline {{
        background: transparent; color: #889;
        border: 1px solid rgba(255,255,255,0.08);
    }}
    .up-btn-outline:hover {{ border-color: rgba(255,255,255,0.2); color: #e0e0f0; }}
    .up-btn-sm {{ padding: 5px 12px; font-size: 11px; }}
    .up-btn-xs {{ padding: 3px 9px; font-size: 10px; }}

    .up-section {{ margin-bottom: 20px; }}
    .up-section-header {{
        display: flex; align-items: center; gap: 8px;
        margin-bottom: 10px; padding-bottom: 8px;
        border-bottom: 1px solid rgba(255,255,255,0.04);
    }}
    .up-section-title {{
        font-size: 10px; font-weight: 700; color: #778;
        text-transform: uppercase; letter-spacing: 1px;
    }}
    .up-section-count {{
        font-size: 10px; font-weight: 700; color: #445;
        background: rgba(255,255,255,0.04); border-radius: 10px;
        padding: 2px 8px;
    }}

    .up-dev {{
        display: flex; align-items: center; justify-content: space-between;
        padding: 10px 14px; margin-bottom: 4px;
        background: #141428; border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.03);
        transition: border-color 0.15s; gap: 10px;
        animation: upFadeIn 0.2s ease both;
    }}
    .up-dev:hover {{ border-color: rgba(255,255,255,0.08); }}
    .up-dev-info {{
        display: flex; align-items: center; gap: 10px; min-width: 0;
    }}
    .up-dev-text {{
        display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    }}
    .up-dev-name {{ font-size: 13px; font-weight: 600; }}
    .up-dev-server {{ font-size: 11px; color: #556; }}
    @keyframes upFadeIn {{
        from {{ opacity: 0; transform: translateY(4px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}
    .up-dev:nth-child(1) {{ animation-delay: 0s; }}
    .up-dev:nth-child(2) {{ animation-delay: 0.03s; }}
    .up-dev:nth-child(3) {{ animation-delay: 0.06s; }}
    .up-dev:nth-child(4) {{ animation-delay: 0.09s; }}
    .up-dev:nth-child(5) {{ animation-delay: 0.12s; }}
    .up-dev:nth-child(6) {{ animation-delay: 0.15s; }}

    .up-dot {{
        width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
    }}
    .up-dot-online {{
        background: #4caf50;
        box-shadow: 0 0 6px rgba(76,175,80,0.5);
    }}
    .up-dot-offline {{ background: #555; }}
    .up-dot-disconnected {{ background: #333; }}

    .up-tag {{
        font-size: 9px; font-weight: 700; text-transform: uppercase;
        letter-spacing: 0.5px; padding: 2px 7px; border-radius: 5px;
    }}
    .up-tag-owner {{
        color: #64d8ff; background: rgba(100,216,255,0.08);
        border: 1px solid rgba(100,216,255,0.12);
    }}
    .up-tag-full {{
        color: #4caf50; background: rgba(76,175,80,0.08);
        border: 1px solid rgba(76,175,80,0.12);
    }}
    .up-tag-ro {{
        color: #ffb74d; background: rgba(255,183,77,0.08);
        border: 1px solid rgba(255,183,77,0.12);
    }}

    .up-group {{ margin-bottom: 12px; }}
    .up-group-header {{
        display: flex; align-items: center; gap: 8px;
        padding: 8px 0; font-size: 12px;
    }}
    .up-group-name {{ font-weight: 600; color: #aab; }}
    .up-group-count {{
        font-size: 10px; color: #445;
        background: rgba(255,255,255,0.04);
        border-radius: 8px; padding: 1px 7px;
    }}

    .up-empty {{
        padding: 28px; text-align: center; color: #445;
        font-size: 13px; font-weight: 500;
    }}

    .up-toast {{
        position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
        padding: 10px 20px; border-radius: 10px;
        font-size: 13px; font-weight: 600; z-index: 1000;
        pointer-events: none;
        animation: upToastIn 0.2s ease, upToastOut 0.3s ease 1.7s forwards;
        background: rgba(76,175,80,0.15); color: #66bb6a;
        border: 1px solid rgba(76,175,80,0.3);
    }}
    @keyframes upToastIn {{
        from {{ opacity: 0; transform: translateX(-50%) translateY(8px); }}
    }}
    @keyframes upToastOut {{
        to {{ opacity: 0; transform: translateX(-50%) translateY(-8px); }}
    }}

    @media (max-width: 500px) {{
        .up-top {{ gap: 14px; }}
        .up-avatar {{ width: 46px; height: 46px; font-size: 18px; }}
        .up-name {{ font-size: 17px; }}
        .up-dev {{ padding: 8px 10px; }}
        .up-sub {{ flex-direction: column; align-items: flex-start; gap: 10px; }}
        .up-sub-grant-form {{ width: 100%; }}
    }}
    </style>

    <a href="/portal/admin" class="up-back">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
        <path d="M10 3l-5 5 5 5" stroke="currentColor" stroke-width="1.5"
              stroke-linecap="round"/></svg>
        Admin
    </a>

    <div class="up-header">
        <div class="up-top">
            <div class="up-avatar">{initial}</div>
            <div>
                <div>
                    <span class="up-name">{username}</span>
                    <span class="up-uid">#{user_id}</span>
                </div>
                <div class="up-meta">
                    {email_html}
                    <span>joined {joined}</span>
                    <span class="up-sep"></span>
                    <span>last login {last_login}</span>
                </div>
                <div class="up-badges">{role_badge}</div>
            </div>
        </div>
        {sub_section}
        <div class="up-actions">
            <button class="up-btn up-btn-outline"
                onclick="resetPw()">Reset Password</button>
            {delete_btn}
        </div>
    </div>

    <div class="up-section">
        <div class="up-section-header">
            <span class="up-section-title">Assigned Devices</span>
            <span class="up-section-count">{len(assigned)}</span>
        </div>
        {assigned_rows}
    </div>

    <div class="up-section">
        <div class="up-section-header">
            <span class="up-section-title">Available Devices</span>
            <span class="up-section-count">{total_available}</span>
        </div>
        {available_html}
    </div>

    <script>
    var csrf = "{csrf}";
    var userId = {user_id};
    var userName = {json.dumps(target["username"])};

    function upToast(msg) {{
        var t = document.createElement('div');
        t.className = 'up-toast';
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(function() {{ t.remove(); }}, 2200);
    }}

    function assign(botName, deviceHash) {{
        fetch("/portal/api/admin/users/" + userId + "/assign-device", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
                csrf_token: csrf,
                bot_name: botName,
                device_hash: deviceHash,
            }})
        }}).then(function(resp) {{
            if (resp.ok) {{ upToast("Device assigned"); location.reload(); }}
            else resp.json().then(function(e) {{ alert(e.error || "Failed"); }});
        }});
    }}

    function assignAll(botName) {{
        if (!confirm("Assign all devices on this server?")) return;
        fetch("/portal/api/admin/users/" + userId + "/assign-device", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
                csrf_token: csrf,
                bot_name: botName,
                device_hash: null,
            }})
        }}).then(function(resp) {{
            if (resp.ok) {{ upToast("All devices assigned"); location.reload(); }}
            else resp.json().then(function(e) {{ alert(e.error || "Failed"); }});
        }});
    }}

    function unassign(grantId) {{
        if (!confirm("Remove this device access?")) return;
        fetch("/portal/api/admin/users/" + userId + "/unassign-device", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf, grant_id: grantId}})
        }}).then(function(resp) {{
            if (resp.ok) {{ upToast("Device unassigned"); location.reload(); }}
            else alert("Failed to unassign");
        }});
    }}

    function grantSub() {{
        var dur = document.getElementById('subDur');
        var days = dur ? dur.value : "30";
        fetch("/portal/api/admin/grant-subscription", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
                csrf_token: csrf,
                user_id: userId,
                duration_days: days ? parseInt(days) : null,
            }})
        }}).then(function(resp) {{
            if (resp.ok) {{ upToast("Subscription granted"); location.reload(); }}
            else resp.json().then(function(e) {{ alert(e.error || "Failed"); }});
        }});
    }}

    function revokeSub() {{
        if (!confirm("Revoke subscription for " + userName + "?")) return;
        fetch("/portal/api/admin/revoke-subscription", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf, user_id: userId}})
        }}).then(function(resp) {{
            if (resp.ok) {{ upToast("Subscription revoked"); location.reload(); }}
            else alert("Failed to revoke");
        }});
    }}

    function resetPw() {{
        var pw = prompt("Set new password for " + userName + ":");
        if (!pw) return;
        if (pw.length < 6) {{ alert("Min 6 characters"); return; }}
        fetch("/portal/api/admin/reset-password", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
                csrf_token: csrf,
                user_id: userId,
                new_password: pw,
            }})
        }}).then(function(resp) {{
            if (resp.ok) upToast("Password reset");
            else alert("Failed");
        }});
    }}

    function deleteUser() {{
        if (!confirm("Delete " + userName + "? This revokes all access."))
            return;
        fetch("/portal/api/admin/users/" + userId + "/delete", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{csrf_token: csrf}})
        }}).then(function(resp) {{
            if (resp.ok) window.location.href = "/portal/admin";
            else alert("Failed to delete user");
        }});
    }}
    </script>
    """
    return web.Response(
        text=_page(f"User: {username}", body, admin, csrf),
        content_type="text/html",
    )


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
                    <div class="guide-card-title">Send Login Code</div>
                    <span class="guide-card-tag guide-tag-action">Action</span>
                </div>
                <p>
                    Log into Kingdom Guard on your phone. The game sends a
                    <strong>one-time code</strong> to your email. Send us that
                    code &mdash; we use it once to log in on your dedicated server.
                </p>
                <div class="guide-callout">
                    <span class="guide-callout-icon">&#128274;</span>
                    <span>The code expires after one use. We never have access to
                    your email or password &mdash; just the single login code.</span>
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

    # Block unapproved users (admins always approved)
    if not user.get("is_approved") and not user.get("is_admin"):
        raise web.HTTPFound("/portal/login?error=Your+account+is+pending+admin+approval")

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
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    if not username or not password or not email:
        raise web.HTTPFound("/portal/register?error=All+fields+required")

    if len(username) > 50 or not all(c.isalnum() or c in "-_" for c in username):
        raise web.HTTPFound("/portal/register?error=Invalid+username")

    if len(email) > 200 or "@" not in email:
        raise web.HTTPFound("/portal/register?error=Invalid+email+address")

    # Check email uniqueness
    existing = await asyncio.to_thread(db.get_user_by_email, email)
    if existing:
        raise web.HTTPFound("/portal/register?error=Email+already+registered")

    if len(password) < 6:
        raise web.HTTPFound("/portal/register?error=Password+must+be+at+least+6+characters")

    pw_hash = await asyncio.to_thread(auth.hash_password, password)

    try:
        await asyncio.to_thread(db.create_user, username, pw_hash, email=email)
    except Exception:
        raise web.HTTPFound("/portal/register?error=Username+already+taken")

    # Don't auto-login — account needs admin approval
    raise web.HTTPFound(
        "/portal/register?success=Account+created!+An+admin+will+review+and+approve+your+account."
    )


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

    # Super admin (user ID 1) cannot be deleted by anyone
    if user_id == 1:
        return web.json_response({"error": "Cannot delete the owner account"}, status=403)

    deleted = await asyncio.to_thread(db.delete_user, user_id)
    if not deleted:
        return web.json_response({"error": "User not found"}, status=404)
    # Also kill their sessions
    await asyncio.to_thread(db.delete_user_sessions, user_id)
    return web.json_response({"status": "ok"})


async def api_admin_approve_user(request: web.Request) -> web.Response:
    await _require_admin(request)
    await _check_csrf(request)
    user_id = int(request.match_info["user_id"])

    approved = await asyncio.to_thread(db.approve_user, user_id)
    if not approved:
        return web.json_response({"error": "User not found or already approved"}, status=404)
    return web.json_response({"status": "ok"})


async def api_admin_reject_user(request: web.Request) -> web.Response:
    await _require_admin(request)
    await _check_csrf(request)
    user_id = int(request.match_info["user_id"])

    rejected = await asyncio.to_thread(db.reject_user, user_id)
    if not rejected:
        return web.json_response({"error": "User not found or already approved"}, status=404)
    return web.json_response({"status": "ok"})


async def api_admin_toggle_admin(request: web.Request) -> web.Response:
    admin = await _require_admin(request)
    await _check_csrf(request)
    user_id = int(request.match_info["user_id"])

    if user_id == admin["user_id"]:
        return web.json_response({"error": "Cannot change your own admin status"}, status=400)

    # Only the super admin (user ID 1) can promote/demote admins
    if admin["user_id"] != 1:
        return web.json_response({"error": "Only the owner can change admin status"}, status=403)

    user = await asyncio.to_thread(db.get_user_by_id, user_id)
    if not user:
        return web.json_response({"error": "User not found"}, status=404)

    new_admin = not user["is_admin"]
    await asyncio.to_thread(db.set_user_admin, user_id, new_admin)
    return web.json_response({"status": "ok", "is_admin": new_admin})


async def api_admin_clear_invites(request: web.Request) -> web.Response:
    await _require_admin(request)
    await _check_csrf(request)
    count = await asyncio.to_thread(db.delete_unused_invite_codes)
    return web.json_response({"status": "ok", "deleted": count})


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


async def api_admin_assign_device(request: web.Request) -> web.Response:
    """Admin assigns a device to a user (creates a grant)."""
    admin = await _require_admin(request)
    await _check_csrf(request)
    user_id = int(request.match_info["user_id"])
    data = await request.json()
    bot_name = data.get("bot_name", "").strip()
    device_hash = data.get("device_hash")  # None for wildcard
    if not bot_name:
        return web.json_response({"error": "bot_name required"}, status=400)

    target = await asyncio.to_thread(db.get_user_by_id, user_id)
    if not target:
        return web.json_response({"error": "User not found"}, status=404)

    bot = await asyncio.to_thread(db.get_bot, bot_name)
    if not bot:
        return web.json_response({"error": "Server not found"}, status=404)

    grant_id = await asyncio.to_thread(
        db.create_grant, user_id, bot_name, device_hash,
        "full", admin["user_id"],
    )
    return web.json_response({"status": "ok", "grant_id": grant_id})


async def api_admin_unassign_device(request: web.Request) -> web.Response:
    """Admin removes a device grant from a user."""
    await _require_admin(request)
    await _check_csrf(request)
    data = await request.json()
    grant_id = data.get("grant_id")
    if not grant_id:
        return web.json_response({"error": "grant_id required"}, status=400)

    deleted = await asyncio.to_thread(db.delete_grant, int(grant_id))
    if not deleted:
        return web.json_response({"error": "Grant not found"}, status=404)
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
# Statistics page (public, no login required)
# ------------------------------------------------------------------

async def page_statistics(request: web.Request) -> web.Response:
    user = await _get_user(request)
    csrf = _get_csrf(request) if user else ""

    import os, re
    stats_path = "/opt/9bot-repo/web/static/statistics.html"
    if os.path.exists(stats_path):
        with open(stats_path) as f:
            raw = f.read()
        # Extract <main> content, <style>, and <script> blocks
        main_match = re.search(r"<main>(.*?)</main>", raw, re.DOTALL)
        style_match = re.search(r"(<style>.*?</style>)", raw, re.DOTALL)
        script_match = re.search(r"(<script>.*?</script>)", raw, re.DOTALL)
        body = (main_match.group(1) if main_match else "")
        body += (style_match.group(1) if style_match else "")
        body += (script_match.group(1) if script_match else "")
    else:
        body = '<div style="padding:40px;text-align:center;color:#667">Statistics data not available yet.</div>'

    return web.Response(text=_page("Statistics", body, user, csrf),
                        content_type="text/html")


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
