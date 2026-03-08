"""9Bot Web Dashboard — mobile-friendly remote control via Flask.

Runs alongside the tkinter GUI in a background thread.  Both share the same
process, so they see the same ``config.running_tasks``, ``config.DEVICE_STATUS``,
and all task functions.

Enable via settings.json::

    "web_dashboard": true

Then access at ``http://<your-ip>:8080`` from any browser.
"""

import os
import sys
import json
import time
import threading
import socket
import functools

from flask import Flask, render_template, request, redirect, url_for, jsonify, abort

# ---------------------------------------------------------------------------
# 9Bot imports (same as main.py)
# ---------------------------------------------------------------------------
import subprocess
import config
from config import (running_tasks, QuestType, RallyType, adb_path)
from devices import (get_devices, get_emulator_instances, auto_connect_emulators,
                     get_bluestacks_config, get_bluestacks_running,
                     start_bluestacks_instance, stop_bluestacks_instance,
                     get_instance_for_device, get_offline_instances)
from navigation import check_screen
from vision import load_screenshot, restart_game, adb_tap
from troops import troops_avail, heal_all, get_troop_status
from actions import (attack, phantom_clash_attack, reinforce_throne, target,
                     check_quests, teleport, teleport_benchmark,
                     rally_titan, rally_eg,
                     search_eg_reset, join_rally, join_war_rallies,
                     reset_quest_tracking, reset_rally_blacklist,
                     mine_mithril,
                     gather_gold,
                     get_quest_tracking_state, get_quest_last_checked, occupy_tower)
from territory import attack_territory, diagnose_grid, scan_test_squares
from botlog import get_logger

try:
    from tunnel import tunnel_status
except ImportError:
    def tunnel_status():
        return "disabled"

try:
    from startup import upload_status as _upload_status
    from startup import get_upload_progress as _get_upload_progress
except ImportError:
    def _upload_status():
        return {"enabled": False}
    def _get_upload_progress():
        return {"phase": "idle", "percent": 0, "message": ""}

_log = get_logger("web")

# ---------------------------------------------------------------------------
# Task functions (same map as main.py TASK_FUNCTIONS)
# ---------------------------------------------------------------------------

TASK_FUNCTIONS = {
    "Rally Titan": rally_titan,
    "Rally Evil Guard": rally_eg,
    "Join Titan Rally": lambda dev: join_rally(QuestType.TITAN, dev),
    "Join Evil Guard Rally": lambda dev: join_rally(QuestType.EVIL_GUARD, dev),
    "Join Groot Rally": lambda dev: join_rally(RallyType.GROOT, dev),
    "Heal All": heal_all,
    "Target": target,
    "Attack": attack,
    "Phantom Clash Attack": phantom_clash_attack,
    "Reinforce Throne": reinforce_throne,
    "UP UP UP!": join_war_rallies,
    "Teleport": teleport,
    "Attack Territory": attack_territory,
    "Check Quests": check_quests,
    "Check Troops": troops_avail,
    "Check Screen": check_screen,
    "Diagnose Grid": diagnose_grid,
    "Scan Corner Coords": scan_test_squares,
    "Test Teleport": lambda dev: teleport(dev, dry_run=True),
    "Teleport Benchmark": teleport_benchmark,
    "Mine Mithril": mine_mithril,
    "Gather Gold": gather_gold,
    "Reinforce Tower": occupy_tower,
}

# Auto-mode names grouped by category, per game mode
# Broken Lands: Combat first, then Farming
# Home Server: Events, Farming, Combat
AUTO_MODES_BL = [
    {"group": "Combat", "modes": [
        {"key": "auto_pass",           "label": "Pass Battle",
         "help": "Marker must be set on the target pass. Reinforces the pass if your team owns it, joins rallies if the enemy owns it. Mode can be switched between Rally Joiner and Rally Starter in Settings."},
        {"key": "auto_occupy",         "label": "Occupy Towers",
         "help": "Scans the territory grid for enemy towers adjacent to your territory, teleports to them, and attacks. Owned passes must be set on the Territory Grid page or unreachable zones will be skipped. Configure your team and enemies in Settings."},
        {"key": "auto_reinforce",      "label": "Reinforce Throne",
         "help": "Periodically sends a troop to reinforce your alliance throne in territory war. The throne must be centered on screen before starting."},
        {"key": "auto_reinforce_ally", "label": "Reinforce Ally",
         "help": "Automatically reinforces nearby alliance castles in order of power level. Requires protocol to be enabled. Max distance can be changed in Settings."},
        {"key": "auto_war_rallies",  "label": "War Rallies",
         "help": "Continuously joins castle, pass, and tower rallies on the war screen. Uses the same join logic as Auto Quest rally joining."},
    ]},
    {"group": "Farming", "modes": [
        {"key": "auto_quest",     "label": "Auto Quest",
         "help": "Automatically completes alliance quests for you. Begins mining gold after quests are complete. Markers must be set for tower and PVP quests to work. Check Settings for gold mine level, troop count, and AP usage."},
        {"key": "auto_titan",     "label": "Rally Titans",
         "help": "Searches for and rallies Titans on the map. Restores AP if needed. AP usage can be set to on or off in Settings."},
        {"key": "auto_gold",      "label": "Gather Gold",
         "help": "Sends all troops to gather gold from mines. Mine level and max troops can be configured in Settings."},
        {"key": "auto_mithril",   "label": "Mine Mithril",
         "help": "Sends troops to gather mithril on a configurable timer. Pulls them out before the 20-minute vulnerability window. Interval can be changed in Settings."},
    ]},
]

AUTO_MODES_HS = [
    {"group": "Events", "modes": [
        {"key": "auto_groot",     "label": "Join Groot",
         "help": "Joins Groot rally events when they appear."},
    ]},
    {"group": "Farming", "modes": [
        {"key": "auto_titan",     "label": "Rally Titans",
         "help": "Searches for and rallies Titans on the map. Restores AP if needed. AP usage can be set to on or off in Settings."},
        {"key": "auto_gold",      "label": "Gather Gold",
         "help": "Sends all troops to gather gold from mines. Mine level and max troops can be configured in Settings."},
        {"key": "auto_mithril",   "label": "Mine Mithril",
         "help": "Sends troops to gather mithril on a configurable timer. Pulls them out before the 20-minute vulnerability window. Interval can be changed in Settings."},
    ]},
    {"group": "Combat", "modes": [
        {"key": "auto_reinforce",      "label": "Reinforce Throne",
         "help": "Periodically sends a troop to reinforce your alliance throne in territory war. The throne must be centered on screen before starting."},
        {"key": "auto_reinforce_ally", "label": "Reinforce Ally",
         "help": "Automatically reinforces nearby alliance castles in order of power level. Requires protocol to be enabled. Max distance can be changed in Settings."},
    ]},
]

# One-shot action names (grouped for display)
ONESHOT_FARM = ["Rally Evil Guard", "Join Titan Rally", "Join Evil Guard Rally",
                "Join Groot Rally", "Heal All", "Gather Gold"]
ONESHOT_WAR = ["Target", "Attack", "Phantom Clash Attack", "Reinforce Throne",
               "UP UP UP!", "Teleport", "Attack Territory"]
ONESHOT_DEBUG = ["Check Screen", "Check Troops", "Diagnose Grid",
                 "Scan Corner Coords", "Test Teleport", "Teleport Benchmark"]

# ---------------------------------------------------------------------------
# Task runners (shared module — no more duplication)
# ---------------------------------------------------------------------------

from runners import (run_auto_quest, run_auto_titan, run_auto_groot,
                     run_auto_pass, run_auto_occupy, run_auto_reinforce,
                     run_auto_reinforce_ally, run_auto_war_rallies,
                     run_auto_mithril, run_auto_gold, run_auto_esb,
                     run_debug_occupy,
                     run_once, run_repeat,
                     launch_task, stop_task, stop_all_tasks_matching,
                     force_stop_all)



_task_start_lock = threading.Lock()  # prevent TOCTOU race on running_tasks

# Modes that can't run simultaneously on the same device
_EXCLUSIVE_MODES = {
    "auto_quest": ["auto_gold", "auto_titan"],
    "auto_titan": ["auto_gold", "auto_quest"],
    "auto_gold":  ["auto_quest", "auto_titan"],
}


# Map auto-mode keys to their runner functions
AUTO_RUNNERS = {
    "auto_quest":     lambda dev, se, s: run_auto_quest(dev, se),
    "auto_titan":     lambda dev, se, s: run_auto_titan(dev, se, s.get("titan_interval", 30), s.get("variation", 0)),
    "auto_groot":     lambda dev, se, s: run_auto_groot(dev, se, s.get("groot_interval", 30), s.get("variation", 0)),
    "auto_pass":      lambda dev, se, s: run_auto_pass(dev, se, s.get("pass_mode", "Rally Joiner"), s.get("reinforce_interval", 30), s.get("variation", 0)),
    "auto_occupy":    lambda dev, se, s: run_auto_occupy(dev, se),
    "auto_reinforce":      lambda dev, se, s: run_auto_reinforce(dev, se, s.get("reinforce_interval", 30), s.get("variation", 0)),
    "auto_reinforce_ally": lambda dev, se, s: run_auto_reinforce_ally(dev, se),
    "auto_mithril":        lambda dev, se, s: run_auto_mithril(dev, se),
    "auto_gold":      lambda dev, se, s: run_auto_gold(dev, se),
    "auto_war_rallies": lambda dev, se, s: run_auto_war_rallies(dev, se, s.get("war_rally_interval", 10), s.get("variation", 0)),
    "auto_esb":       lambda dev, se, s: run_auto_esb(dev, se, 5, s.get("variation", 0)),
    "debug_occupy":   lambda dev, se, s: run_debug_occupy(dev, se),
}


def _effective_settings(device_id, settings):
    """Merge global settings with per-device overrides into one flat dict."""
    effective = dict(settings)
    overrides = settings.get("device_settings", {}).get(device_id, {})
    effective.update(overrides)
    return effective


# ---------------------------------------------------------------------------
# Dashboard-specific task helpers
# ---------------------------------------------------------------------------

def stop_all():
    """Force-kill every running task immediately."""
    force_stop_all()
    config.SCHEDULED_TASKS.clear()
    config.SCHEDULE_SUPPRESSED.clear()

def cleanup_dead_tasks():
    """Remove finished threads from running_tasks."""
    for key in list(running_tasks.keys()):
        info = running_tasks.get(key)
        if not isinstance(info, dict):
            continue
        thread = info.get("thread")
        if thread and not thread.is_alive():
            del running_tasks[key]


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

from settings import SETTINGS_FILE, DEFAULTS, load_settings as _load_settings, save_settings as _save_settings, DEVICE_OVERRIDABLE_KEYS

from startup import apply_settings as _apply_settings

_settings_lock = threading.Lock()  # serialize all settings mutations


def _ajax_save_setting(key, value):
    """Validate and save a single global setting. Returns (ok, error_msg)."""
    from config import SETTINGS_RULES, validate_settings
    # Handle per-device troop count: device_troops_<device_id>
    if key.startswith("device_troops_"):
        dev_id = key[len("device_troops_"):]
        try:
            count = int(value)
        except (ValueError, TypeError):
            return False, "Expected integer for troop count"
        if count < 1 or count > 5:
            return False, "Troop count must be 1-5"
        with _settings_lock:
            settings = _load_settings()
            dt = settings.setdefault("device_troops", {})
            dt[dev_id] = count
            _apply_settings(settings)
            _save_settings(settings)
        return True, None
    rule = SETTINGS_RULES.get(key)
    if rule is None and key != "device_troops":
        return False, f"Unknown setting: {key}"
    # Coerce value to expected type
    if rule:
        expected = rule["type"]
        if expected is bool:
            value = bool(value)
        elif expected is int:
            try:
                value = int(value)
            except (ValueError, TypeError):
                return False, f"{key}: expected integer"
        elif expected is str:
            value = str(value)
    with _settings_lock:
        settings = _load_settings()
        settings[key] = value
        settings, warnings = validate_settings(settings, DEFAULTS)
        for w in warnings:
            if key in w:
                return False, w
        _apply_settings(settings)
        _save_settings(settings)
    return True, None


def _validate_schedules(value):
    """Validate a list of schedule dicts. Returns error string or None."""
    if not isinstance(value, list):
        return "schedules must be a list"
    valid_modes = set(AUTO_RUNNERS.keys())
    for i, sched in enumerate(value):
        if not isinstance(sched, dict):
            return f"schedules[{i}]: expected dict"
        if not sched.get("id"):
            return f"schedules[{i}]: missing id"
        if sched.get("mode") not in valid_modes:
            return f"schedules[{i}]: invalid mode '{sched.get('mode')}'"
        for time_key in ("start", "end"):
            t = sched.get(time_key, "")
            if not isinstance(t, str) or len(t) != 5 or t[2] != ":":
                return f"schedules[{i}]: invalid {time_key} time"
    return None


def _ajax_save_device_setting(device_id, key, value):
    """Validate and save a single per-device override. Returns (ok, error_msg)."""
    from config import SETTINGS_RULES, validate_settings
    # Schedules: stored per-device but not in DEVICE_OVERRIDABLE_KEYS
    if key == "schedules":
        err = _validate_schedules(value)
        if err:
            return False, err
        with _settings_lock:
            settings = _load_settings()
            ds = settings.setdefault("device_settings", {})
            dev = ds.setdefault(device_id, {})
            dev["schedules"] = value
            _apply_settings(settings)
            _save_settings(settings)
        return True, None
    if key not in DEVICE_OVERRIDABLE_KEYS:
        return False, f"Not overridable: {key}"
    rule = SETTINGS_RULES.get(key)
    if rule:
        expected = rule["type"]
        if expected is bool:
            value = bool(value)
        elif expected is int:
            try:
                value = int(value)
            except (ValueError, TypeError):
                return False, f"{key}: expected integer"
        elif expected is str:
            value = str(value)
    with _settings_lock:
        settings = _load_settings()
        ds = settings.setdefault("device_settings", {})
        dev = ds.setdefault(device_id, {})
        dev[key] = value
        settings, warnings = validate_settings(settings, DEFAULTS)
        for w in warnings:
            if key in w:
                return False, w
        _apply_settings(settings)
        _save_settings(settings)
    return True, None


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def get_local_ip():
    """Best-effort detection of the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def ensure_firewall_open(port=8080):
    """On Windows, add a firewall rule to allow inbound TCP on *port*.

    Returns True if the rule was added (or already exists), False if we
    couldn't add it (e.g. not on Windows, or not running as admin).
    """
    if sys.platform != "win32":
        return True  # no firewall management needed

    import subprocess as _sp

    rule_name = f"9Bot Web Dashboard (TCP {port})"

    # Check if rule already exists
    try:
        check = _sp.run(
            ["netsh", "advfirewall", "firewall", "show", "rule",
             f"name={rule_name}"],
            capture_output=True, text=True, timeout=10,
        )
        if check.returncode == 0 and rule_name in check.stdout:
            _log.info("Firewall rule '%s' already exists", rule_name)
            return True
    except Exception:
        pass

    # Try to add the rule
    try:
        result = _sp.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule_name}",
             "dir=in", "action=allow", "protocol=TCP",
             f"localport={port}", "profile=private"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            _log.info("Firewall rule added: %s", rule_name)
            return True
        else:
            _log.warning(
                "Could not add firewall rule (need admin?). "
                "Remote devices may not be able to connect.\n"
                "  Fix: run as Administrator, or manually allow TCP port %d:\n"
                '  netsh advfirewall firewall add rule name="%s" '
                "dir=in action=allow protocol=TCP localport=%d profile=private",
                port, rule_name, port,
            )
            return False
    except FileNotFoundError:
        _log.warning("netsh not found — cannot configure firewall automatically")
        return False
    except Exception as exc:
        _log.warning("Firewall rule creation failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

def create_app():
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.secret_key = os.urandom(24)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # no static file caching during dev
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.after_request
    def _no_cache(response):
        """Prevent browser from caching dynamic HTML pages."""
        if "text/html" in response.content_type:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # --- Page routes ---

    @app.route("/")
    def index():
        cleanup_dead_tasks()
        devs, instances = _cached_devices()
        device_info = []
        emu_running = _device_cache.get("emu_running", {})
        for d in devs:
            emu_inst = get_instance_for_device(d)
            device_info.append({
                "id": d,
                "name": instances.get(d, d),
                "status": config.DEVICE_STATUS.get(d, "Idle"),
                "troops": config.DEVICE_TOTAL_TROOPS.get(d, 5),
                "emu_instance": emu_inst,
            })
        active_tasks = []
        for key, info in list(running_tasks.items()):
            if isinstance(info, dict):
                thread = info.get("thread")
                if thread and thread.is_alive():
                    active_tasks.append(key)
        settings = _load_settings()
        mode = settings.get("mode", "bl")
        auto_groups = AUTO_MODES_BL if mode == "bl" else AUTO_MODES_HS
        # Build remote URL from auto-derived relay config
        relay_url = None
        from startup import get_relay_config
        relay_cfg = get_relay_config(settings)
        if relay_cfg:
            raw, _, bot_name = relay_cfg
            is_secure = raw.startswith("wss://")
            host = raw.replace("ws://", "").replace("wss://", "").split("/")[0]
            scheme = "https" if is_secure else "http"
            relay_url = f"{scheme}://{host}/{bot_name}"

        # Add offline BlueStacks instances
        offline_instances = get_offline_instances()
        for inst in offline_instances:
            device_info.append({
                "id": inst["device_id"],
                "name": inst["display_name"],
                "status": "Starting Emulator..." if inst["device_id"] in config.EMULATOR_STARTING else "Offline",
                "troops": 0,
                "offline": True,
                "instance": inst["instance"],
            })

        # Build per-device share data (device_hash + tokens) — all devices incl. offline
        from startup import device_hash, generate_device_token, generate_device_ro_token
        share_data = {}
        share_url_base = relay_url or f"http://{get_local_ip()}:8080"
        for di in device_info:
            d = di["id"]
            dh = device_hash(d)
            dt = generate_device_token(d)
            dt_ro = generate_device_ro_token(d)
            if dh and dt:
                share_data[d] = {
                    "hash": dh,
                    "token": dt,
                    "url": f"{share_url_base}/d/{dh}?token={dt}",
                    "ro_url": f"{share_url_base}/d/{dh}?token={dt_ro}" if dt_ro else None,
                }

        return render_template("index.html",
                               devices=device_info,
                               tasks=active_tasks,
                               task_count=len(active_tasks),
                               auto_groups=auto_groups,
                               mode=mode,
                               oneshot_farm=ONESHOT_FARM,
                               oneshot_war=ONESHOT_WAR,
                               active_tasks=active_tasks,
                               local_ip=get_local_ip(),
                               relay_url=relay_url,
                               share_data=share_data,
                               device_filter=False)

    @app.route("/tasks")
    def tasks_page():
        return redirect(url_for("index"))

    @app.route("/settings")
    def settings_page():
        settings = _load_settings()
        detected, instances = _cached_devices()
        # Build device list for tabs
        all_devices = [{"id": d, "name": instances.get(d, d.split(":")[-1])}
                       for d in detected]
        return render_template("settings.html", settings=settings,
                               all_devices=all_devices)

    @app.route("/guide")
    def guide_page():
        return render_template("guide.html")

    @app.route("/debug")
    def debug_page():
        detected, _ = _cached_devices()
        device_info = [{"id": d, "name": d.split(":")[-1]} for d in detected]
        active_tasks = []
        for key, info in list(running_tasks.items()):
            thread = info.get("thread")
            if thread and thread.is_alive():
                active_tasks.append(key)
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        lines = []
        log_file = os.path.join(log_dir, "9bot.log")
        if os.path.isfile(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                lines = [l for l in all_lines if " DEBUG " not in l][-150:]
            except Exception as e:
                _log.warning("Failed to read log file: %s", e)
                lines = ["(Could not read log file)"]
        import training
        training_stats = training.get_training_stats()
        settings = _load_settings()
        # Per-device protocol status
        protocol_devices = []
        for d in detected:
            dev_enabled = config.get_device_config(d, "protocol_enabled")
            dev_active = d in config.PROTOCOL_ACTIVE_DEVICES
            protocol_devices.append({
                "id": d, "name": d.split(":")[-1] if ":" in d else d,
                "enabled": dev_enabled, "active": dev_active,
            })
        return render_template("debug.html",
                               devices=device_info,
                               tasks=active_tasks,
                               debug_actions=ONESHOT_DEBUG,
                               log_lines=lines,
                               training_stats=training_stats,
                               protocol_enabled=settings.get("protocol_enabled", False),
                               protocol_devices=protocol_devices)

    @app.route("/logs")
    def logs_page():
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        lines = []
        log_file = os.path.join(log_dir, "9bot.log")
        if os.path.isfile(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                lines = [l for l in all_lines if " DEBUG " not in l][-150:]
            except Exception as e:
                _log.warning("Failed to read log file: %s", e)
                lines = ["(Could not read log file)"]
        return render_template("logs.html", lines=lines)

    # --- API routes ---

    # Cache device list to avoid spamming ADB on every poll
    _device_cache = {"devices": [], "instances": {}, "emu_running": {}, "ts": 0}
    _device_cache_lock = threading.Lock()
    _DEVICE_CACHE_TTL = 15  # seconds

    def _cached_devices():
        with _device_cache_lock:
            now = time.time()
            if now - _device_cache["ts"] > _DEVICE_CACHE_TTL:
                _device_cache["devices"] = get_devices()
                _device_cache["instances"] = get_emulator_instances()
                _device_cache["emu_running"] = get_bluestacks_running()
                _device_cache["ts"] = now
            return _device_cache["devices"], _device_cache["instances"]

    def _device_status_info(d, instances):
        """Build status dict for a single device."""
        snapshot = get_troop_status(d)
        troops_list = []
        snapshot_age = None
        if snapshot:
            snapshot_age = round(snapshot.age_seconds)
            for t in snapshot.troops:
                troops_list.append({
                    "action": t.action.value,
                    "time_left": t.time_left,
                })
        mithril_next = None
        mithril_anchor = (config.MITHRIL_DEPLOY_TIME.get(d)
                          or config.LAST_MITHRIL_TIME.get(d))
        if mithril_anchor:
            interval = config.get_device_config(d, "mithril_interval")
            elapsed = time.time() - mithril_anchor
            remaining = interval * 60 - elapsed
            mithril_next = max(0, int(remaining))
        emu_instance = get_instance_for_device(d)
        emu_running = (emu_instance is not None
                       and emu_instance in _device_cache.get("emu_running", {}))
        # Check if any running task on this device was started by the scheduler
        scheduled = False
        for key in list(config.SCHEDULED_TASKS.keys()):
            if key.startswith(d + "_") and key in running_tasks:
                info = running_tasks[key]
                if isinstance(info, dict) and info.get("thread") and info["thread"].is_alive():
                    scheduled = True
                    break
        return {
            "id": d,
            "name": instances.get(d, d),
            "status": config.DEVICE_STATUS.get(d, "Idle"),
            "troops": troops_list,
            "snapshot_age": snapshot_age,
            "troop_source": snapshot.source if snapshot else None,
            "quests": get_quest_tracking_state(d),
            "quest_age": get_quest_last_checked(d),
            "mithril_next": mithril_next,
            "emu_running": emu_running,
            "scheduled": scheduled,
        }

    @app.route("/api/status")
    def api_status():
        cleanup_dead_tasks()
        devs, instances = _cached_devices()
        device_info = [_device_status_info(d, instances) for d in devs]
        # Chat mirroring: if enabled and any device has protocol, all devices get chat
        settings = _load_settings()
        chat_mirror = settings.get("chat_mirror", True)
        any_protocol = bool(config.PROTOCOL_ACTIVE_DEVICES)
        for di in device_info:
            di["chat_available"] = di.get("protocol_active", False) or (chat_mirror and any_protocol)
        # Include offline BlueStacks instances
        offline_instances = get_offline_instances()
        for inst in offline_instances:
            did = inst["device_id"]
            starting = did in config.EMULATOR_STARTING
            device_info.append({
                "id": did,
                "name": inst["display_name"],
                "status": "Starting Emulator..." if starting else "Offline",
                "troops": [],
                "snapshot_age": None,
                "quests": [],
                "quest_age": None,
                "mithril_next": None,
                "emu_running": False,
                "offline": True,
                "emu_starting": starting,
                "protocol_active": False,
            })
        active = []
        for key, info in list(running_tasks.items()):
            if isinstance(info, dict):
                thread = info.get("thread")
                if thread and thread.is_alive():
                    active.append(key)
        return jsonify({"devices": device_info, "tasks": active,
                        "tunnel": tunnel_status(),
                        "upload": _upload_status()})

    @app.route("/api/devices/refresh", methods=["POST"])
    def api_refresh_devices():
        auto_connect_emulators()
        _device_cache["ts"] = 0  # bust cache
        return redirect(url_for("index"))

    @app.route("/tasks/start", methods=["POST"])
    def start_task():
        device_raw = request.form.get("device", "")
        task_name = request.form.get("task_name")
        task_type = request.form.get("task_type", "oneshot")  # "auto" or "oneshot"

        # Support comma-separated device list (multi-select checkboxes)
        devices_to_run = [d.strip() for d in device_raw.split(",") if d.strip()]
        if not devices_to_run:
            return redirect(url_for("tasks_page"))

        settings = _load_settings()

        # Validate device IDs against known devices
        known = set(_cached_devices()[0])
        devices_to_run = [d for d in devices_to_run if d in known]
        if not devices_to_run:
            return redirect(url_for("tasks_page"))

        for device in devices_to_run:
          with _task_start_lock:
            if task_type == "auto":
                # Start an auto-mode
                mode_key = task_name
                task_key = f"{device}_{mode_key}"
                if task_key in running_tasks:
                    info = running_tasks[task_key]
                    if isinstance(info, dict) and info.get("thread") and info["thread"].is_alive():
                        continue

                # Exclusivity: stop conflicting modes before starting
                for conflict in _EXCLUSIVE_MODES.get(mode_key, []):
                    ckey = f"{device}_{conflict}"
                    if ckey in running_tasks:
                        stop_task(ckey)

                runner = AUTO_RUNNERS.get(mode_key)
                if runner:
                    stop_event = threading.Event()
                    if mode_key == "auto_mithril":
                        config.MITHRIL_ENABLED_DEVICES.add(device)
                    effective = _effective_settings(device, settings)
                    launch_task(device, mode_key,
                                lambda d=device, se=stop_event, s=effective: runner(d, se, s),
                                stop_event)
            else:
                # One-shot action
                func = TASK_FUNCTIONS.get(task_name)
                if func:
                    stop_event = threading.Event()
                    launch_task(device, f"once:{task_name}",
                                run_once, stop_event,
                                args=(device, task_name, func))

        return redirect(url_for("tasks_page"))

    @app.route("/tasks/stop", methods=["POST"])
    def stop_task_route():
        task_key = request.form.get("task_key")
        if task_key:
            stop_task(task_key)
        return redirect(url_for("tasks_page"))

    @app.route("/tasks/stop-mode", methods=["POST"])
    def stop_mode_route():
        """Stop running tasks for a given auto-mode.

        If ``device`` is provided, only stop that device's task.
        Otherwise stop all devices running this mode.
        """
        mode_key = request.form.get("mode_key")
        device = request.form.get("device")
        if mode_key:
            # Reset loop-control flags for modes that use them
            if mode_key == "auto_mithril":
                if device:
                    config.MITHRIL_ENABLED_DEVICES.discard(device)
                    config.MITHRIL_DEPLOY_TIME.pop(device, None)
                else:
                    config.MITHRIL_ENABLED_DEVICES.clear()
                    config.MITHRIL_DEPLOY_TIME.clear()
            if device:
                task_key = f"{device}_{mode_key}"
                if task_key in running_tasks:
                    stop_task(task_key)
                # Suppress scheduler restart for this window
                if task_key in config.SCHEDULED_TASKS:
                    config.SCHEDULE_SUPPRESSED.add(task_key)
                    config.SCHEDULED_TASKS.pop(task_key, None)
            else:
                suffix = f"_{mode_key}"
                for key in list(running_tasks.keys()):
                    if key.endswith(suffix):
                        stop_task(key)
                        if key in config.SCHEDULED_TASKS:
                            config.SCHEDULE_SUPPRESSED.add(key)
                            config.SCHEDULED_TASKS.pop(key, None)
        return redirect(url_for("tasks_page"))

    @app.route("/tasks/stop-all", methods=["POST"])
    def stop_all_route():
        stop_all()
        return redirect(url_for("tasks_page"))

    @app.route("/settings", methods=["POST"])
    def save_settings_route():
        settings = _load_settings()
        # Global-only toggles
        for key in ["verbose_logging", "remote_access", "auto_upload_logs",
                     "collect_training_data", "chat_mirror", "chat_translate_enabled"]:
            val = request.form.get(key, "")
            settings[key] = bool(val and val != "")

        for key in ["upload_interval_hours"]:
            val = request.form.get(key, "")
            if val.isdigit():
                settings[key] = int(val)

        for key in ["my_team", "mode", "chat_translate_api_key"]:
            val = request.form.get(key)
            if val is not None:
                settings[key] = val

        from config import validate_settings
        settings, warnings = validate_settings(settings, DEFAULTS)
        for w in warnings:
            _log.warning("Settings (web save): %s", w)
        _apply_settings(settings)
        _save_settings(settings)
        return redirect(url_for("settings_page"))

    # --- AJAX settings API ---

    @app.route("/api/settings/save", methods=["POST"])
    def api_settings_save():
        """Save a single global setting via AJAX."""
        data = request.get_json(silent=True) or {}
        key = data.get("key")
        value = data.get("value")
        if not key:
            return jsonify(ok=False, error="Missing key"), 400
        ok, err = _ajax_save_setting(key, value)
        if not ok:
            return jsonify(ok=False, error=err), 400
        return jsonify(ok=True)

    @app.route("/api/settings/device/<device_id>/save", methods=["POST"])
    def api_device_settings_save(device_id):
        """Save a single per-device override via AJAX."""
        detected = _cached_devices()[0]
        if device_id not in detected:
            abort(404)
        data = request.get_json(silent=True) or {}
        key = data.get("key")
        value = data.get("value")
        if not key:
            return jsonify(ok=False, error="Missing key"), 400
        ok, err = _ajax_save_device_setting(device_id, key, value)
        if not ok:
            return jsonify(ok=False, error=err), 400
        return jsonify(ok=True)

    @app.route("/api/settings/device/<device_id>/reset-key", methods=["POST"])
    def api_device_settings_reset_key(device_id):
        """Reset a single per-device override to global default."""
        detected = _cached_devices()[0]
        if device_id not in detected:
            abort(404)
        data = request.get_json(silent=True) or {}
        key = data.get("key")
        if not key:
            return jsonify(ok=False, error="Missing key"), 400
        from config import validate_settings
        with _settings_lock:
            settings = _load_settings()
            ds = settings.get("device_settings", {})
            dev = ds.get(device_id, {})
            dev.pop(key, None)
            if not dev or all(k in ("shared_modes", "shared_actions") for k in dev):
                # Only access control keys left — keep them, remove config overrides
                pass
            ds[device_id] = dev
            settings["device_settings"] = ds
            settings, _ = validate_settings(settings, DEFAULTS)
            _apply_settings(settings)
            _save_settings(settings)
        return jsonify(ok=True)

    @app.route("/api/settings/device/<device_id>/reset-all", methods=["POST"])
    def api_device_settings_reset_all(device_id):
        """Reset all per-device overrides (preserving access control)."""
        detected = _cached_devices()[0]
        if device_id not in detected:
            abort(404)
        from config import validate_settings
        with _settings_lock:
            settings = _load_settings()
            ds = settings.get("device_settings", {})
            existing = ds.get(device_id, {})
            # Preserve shared_modes/shared_actions (owner-only access control)
            preserved = {}
            if "shared_modes" in existing:
                preserved["shared_modes"] = existing["shared_modes"]
            if "shared_actions" in existing:
                preserved["shared_actions"] = existing["shared_actions"]
            if preserved:
                ds[device_id] = preserved
            else:
                ds.pop(device_id, None)
            settings["device_settings"] = ds
            settings, _ = validate_settings(settings, DEFAULTS)
            _apply_settings(settings)
            _save_settings(settings)
        return jsonify(ok=True)

    @app.route("/api/device/<device_id>/capture-home", methods=["POST"])
    def api_capture_home(device_id):
        """Navigate away/back to center camera on home castle and OCR coordinates."""
        from actions import capture_home_coords
        from runners import _save_home_coords
        detected = _cached_devices()[0]
        if device_id not in detected:
            abort(404)
        coords = capture_home_coords(device_id)
        if not coords:
            return jsonify(ok=False, error="Could not read coordinates — ensure game is on MAP screen"), 500
        x, z = coords
        _save_home_coords(device_id, x, z)
        return jsonify(ok=True, home_x=x, home_z=z)

    @app.route("/settings/device/<device_id>")
    def device_settings_page(device_id):
        """Per-device settings page with override toggles."""
        detected, instances = _cached_devices()
        if device_id not in detected:
            abort(404)
        all_devices = [{"id": d, "name": instances.get(d, d.split(":")[-1])}
                       for d in detected]
        settings = _load_settings()
        dev_overrides = settings.get("device_settings", {}).get(device_id, {})
        mode = settings.get("mode", "bl")
        auto_groups = AUTO_MODES_BL if mode == "bl" else AUTO_MODES_HS
        # Build mode labels for schedule dropdown
        from runners import _MODE_LABELS
        schedule_modes = [{"key": k, "label": _MODE_LABELS.get(k, k)}
                          for k in AUTO_RUNNERS.keys()
                          if k != "debug_occupy"]
        # Device troop count
        saved_dt = settings.get("device_troops", {})
        device_troop_count = saved_dt.get(device_id, 5)
        return render_template("settings_device.html",
                               device_id=device_id,
                               device_name=instances.get(device_id, device_id.split(":")[-1]),
                               all_devices=all_devices,
                               overrides=dev_overrides,
                               globals=settings,
                               auto_groups=auto_groups,
                               all_actions=ONESHOT_FARM + ONESHOT_WAR,
                               schedule_modes=schedule_modes,
                               device_troop_count=device_troop_count)

    @app.route("/settings/device/<device_id>", methods=["POST"])
    def save_device_settings(device_id):
        """Save per-device setting overrides."""
        detected = _cached_devices()[0]
        if device_id not in detected:
            abort(404)
        from settings import DEVICE_OVERRIDABLE_KEYS
        settings = _load_settings()
        ds = settings.setdefault("device_settings", {})
        overrides = {}

        # Boolean overridable keys
        bool_keys = {"auto_heal", "auto_restore_ap", "ap_use_free", "ap_use_potions",
                      "ap_allow_large_potions", "ap_use_gems", "eg_rally_own",
                      "titan_rally_own", "gather_enabled", "tower_quest_enabled"}
        for key in bool_keys & DEVICE_OVERRIDABLE_KEYS:
            if f"override_{key}" in request.form:
                val = request.form.get(key, "")
                overrides[key] = bool(val and val != "")

        # Integer overridable keys
        int_keys = {"ap_gem_limit", "min_troops", "mithril_interval",
                     "gather_mine_level", "gather_max_troops"}
        for key in int_keys & DEVICE_OVERRIDABLE_KEYS:
            if f"override_{key}" in request.form:
                val = request.form.get(key, "")
                if val.isdigit():
                    overrides[key] = int(val)

        # String overridable keys
        str_keys = {"my_team"}
        for key in str_keys & DEVICE_OVERRIDABLE_KEYS:
            if f"override_{key}" in request.form:
                val = request.form.get(key)
                if val is not None:
                    overrides[key] = val

        # Shared permissions (not config overrides — access control)
        perm_val = request.form.get("permissions_enabled", "")
        if perm_val and perm_val != "":
            # Collect checked auto-mode keys
            shared_modes = []
            for key in ALL_AUTO_MODE_KEYS:
                if f"perm_mode_{key}" in request.form:
                    shared_modes.append(key)
            overrides["shared_modes"] = shared_modes

            # Collect checked one-shot actions
            shared_actions = []
            for name in ONESHOT_FARM + ONESHOT_WAR:
                safe = name.replace(" ", "_")
                if f"perm_action_{safe}" in request.form:
                    shared_actions.append(name)
            overrides["shared_actions"] = shared_actions
        # else: no shared_modes/shared_actions key = allow everything

        ds[device_id] = overrides
        from config import validate_settings
        settings, warnings = validate_settings(settings, DEFAULTS)
        for w in warnings:
            _log.warning("Settings (device save): %s", w)
        _apply_settings(settings)
        _save_settings(settings)
        return redirect(url_for("device_settings_page", device_id=device_id))

    @app.route("/settings/device/<device_id>/reset", methods=["POST"])
    def reset_device_settings(device_id):
        """Remove all per-device overrides for a device."""
        detected = _cached_devices()[0]
        if device_id not in detected:
            abort(404)
        settings = _load_settings()
        ds = settings.get("device_settings", {})
        ds.pop(device_id, None)
        settings["device_settings"] = ds
        _apply_settings(settings)
        _save_settings(settings)
        return redirect(url_for("device_settings_page", device_id=device_id))

    @app.route("/api/restart", methods=["POST"])
    def api_restart():
        """Save settings, stop all tasks, and restart the process."""
        _log.info("=== RESTART requested via web dashboard ===")
        _save_settings(_load_settings())
        stop_all()

        def _do_restart():
            time.sleep(0.5)  # let the HTTP response flush
            # Mark as restarting so _on_exit skips shutdown (which would
            # tear down ADB connections the new process needs).
            config._restarting = True
            # Spawn the new process BEFORE closing the window, because
            # closing pywebview triggers os._exit(0) which kills daemon
            # threads (including this one) before os.execv can run.
            env = os.environ.copy()
            env["NINEBOT_RESTART"] = "1"  # skip opening new window
            # Save window position/size so the new process can restore it.
            # pywebview reports physical pixels; we need logical pixels
            # for create_window, so divide by the DPI scale factor.
            win = getattr(config, '_webview_window', None)
            if win:
                try:
                    scale = 1.0
                    if sys.platform == "win32":
                        import ctypes
                        ctypes.windll.user32.SetProcessDPIAware()
                        dpi = ctypes.windll.user32.GetDpiForSystem()
                        scale = dpi / 96.0
                    env["NINEBOT_WIN_X"] = str(int(win.x / scale))
                    env["NINEBOT_WIN_Y"] = str(int(win.y / scale))
                    env["NINEBOT_WIN_W"] = str(int(win.width / scale))
                    env["NINEBOT_WIN_H"] = str(int(win.height / scale))
                except Exception:
                    pass
            import subprocess as _sp
            # Use the project directory as cwd so relative paths (e.g.
            # platform-tools/adb.exe) resolve correctly in the new process.
            project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _sp.Popen([sys.executable] + sys.argv, env=env, cwd=project_dir)
            # Now close pywebview window (triggers os._exit in main thread)
            cb = config._quit_callback
            if cb:
                try:
                    cb()
                except Exception:
                    pass
            # Fallback if no pywebview — force exit the old process
            time.sleep(1)
            os._exit(0)

        threading.Thread(target=_do_restart, daemon=True).start()
        return jsonify({"ok": True, "message": "Restarting..."})

    @app.route("/api/bug-report", methods=["POST"])
    def api_bug_report():
        from startup import create_bug_report_zip
        from flask import send_file
        import io
        notes = request.form.get("notes", "").strip() or None
        zip_bytes, filename = create_bug_report_zip(notes=notes)
        return send_file(
            io.BytesIO(zip_bytes),
            mimetype="application/zip",
            as_attachment=True,
            download_name=filename,
        )

    @app.route("/api/protocol-toggle", methods=["POST"])
    def api_protocol_toggle():
        """Toggle protocol_enabled for a specific device or globally."""
        device_id = request.form.get("device_id")
        settings = _load_settings()
        if device_id:
            # Per-device toggle
            ds = settings.setdefault("device_settings", {})
            dev_settings = ds.setdefault(device_id, {})
            current = dev_settings.get(
                "protocol_enabled", settings.get("protocol_enabled", False))
            dev_settings["protocol_enabled"] = not current
            _apply_settings(settings)
            _save_settings(settings)
            return jsonify({"ok": True, "enabled": dev_settings["protocol_enabled"],
                            "device_id": device_id})
        else:
            # Global toggle (legacy)
            settings["protocol_enabled"] = not settings.get("protocol_enabled", False)
            _apply_settings(settings)
            _save_settings(settings)
            return jsonify({"ok": True, "enabled": settings["protocol_enabled"]})

    @app.route("/api/chat")
    def api_chat():
        """Return recent chat messages for a device.

        If the device has no protocol but chat_mirror is enabled, returns
        messages from a protocol-active device (excluding PRIVATE channel).
        """
        device_id = request.args.get("device")
        channel = request.args.get("channel")  # optional filter
        if not device_id:
            return jsonify({"error": "device parameter required"}), 400
        from startup import get_protocol_chat_messages
        messages = get_protocol_chat_messages(device_id)
        # Chat mirroring: if this device has no protocol messages, pull from
        # a protocol-active device (exclude PRIVATE channel for privacy)
        mirror_source = None
        if not messages and device_id not in config.PROTOCOL_ACTIVE_DEVICES:
            settings = _load_settings()
            if settings.get("chat_mirror", True):
                for active_dev in config.PROTOCOL_ACTIVE_DEVICES:
                    mirrored = get_protocol_chat_messages(active_dev)
                    if mirrored:
                        messages = [m for m in mirrored
                                    if isinstance(m, dict)
                                    and m.get("channel", "").upper() != "PRIVATE"]
                        mirror_source = active_dev
                        break
        # Filter by channel if specified
        if channel:
            ch_upper = channel.upper()
            if ch_upper == "UNION":
                # Include UNION_R4 (R4+ officer chat) under Alliance tab
                messages = [m for m in messages
                            if isinstance(m, dict)
                            and m.get("channel", "").upper().startswith("UNION")]
            else:
                messages = [m for m in messages
                            if isinstance(m, dict)
                            and m.get("channel", "").upper() == ch_upper]
        # Filter out system clutter
        _CHAT_SPAM_PREFIXES = ("Shared location of ", "Bizarre Cave")
        messages = [m for m in messages
                    if not (isinstance(m, dict)
                            and any(str(m.get("content", "")).startswith(p)
                                    for p in _CHAT_SPAM_PREFIXES))]
        # Serialize for JSON (strip raw objects)
        serializable = []
        for m in messages:
            if isinstance(m, dict):
                serializable.append({
                    "content": m.get("content", ""),
                    "sender": m.get("sender", ""),
                    "channel": m.get("channel", ""),
                    "channel_type": m.get("channel_type", 0),
                    "timestamp": m.get("timestamp", 0),
                    "union_name": m.get("union_name", ""),
                    "payload_type": m.get("payload_type", 0),
                    "translated": m.get("translated"),
                    "source_language": m.get("source_language", ""),
                })
        serializable.sort(key=lambda m: m.get("timestamp", 0))
        result = {"messages": serializable, "count": len(serializable)}
        if mirror_source:
            result["mirrored"] = True
        return jsonify(result)

    @app.route("/api/protocol-status")
    def api_protocol_status():
        """Return per-device protocol status for the debug page."""
        detected, _ = _cached_devices()
        settings = _load_settings()
        devices_status = []
        for dev in detected:
            enabled = config.get_device_config(dev, "protocol_enabled")
            active = dev in config.PROTOCOL_ACTIVE_DEVICES
            from startup import get_protocol_stats
            stats = get_protocol_stats(dev)
            devices_status.append({
                "device_id": dev,
                "enabled": enabled,
                "active": active,
                "connected": stats is not None and stats.get("uptime_s", 0) > 0,
                "stats": stats,
            })
        return jsonify({"devices": devices_status})

    @app.route("/api/patch-apk", methods=["POST"])
    def api_patch_apk():
        """Start APK patching for a device."""
        device_id = request.form.get("device_id")
        if not device_id:
            return jsonify({"error": "device_id required"}), 400
        from startup import start_apk_patch, is_patching
        if is_patching(device_id):
            return jsonify({"error": "Already patching"}), 409
        ok = start_apk_patch(device_id)
        if not ok:
            return jsonify({"error": "Already patching"}), 409
        return jsonify({"ok": True})

    @app.route("/api/patch-progress")
    def api_patch_progress():
        """Return current APK patch progress for a device."""
        device_id = request.args.get("device_id")
        if not device_id:
            return jsonify({"error": "device_id required"}), 400
        from startup import get_patch_progress
        return jsonify(get_patch_progress(device_id))

    # --- Protocol Visualizer ---

    def _safe_val(v):
        """Recursively convert a value to JSON-serializable form."""
        if isinstance(v, (str, int, float, bool, type(None))):
            return v
        if isinstance(v, bytes):
            return f"<{len(v)} bytes>"
        if isinstance(v, dict):
            return {str(k): _safe_val(vv) for k, vv in v.items()}
        if isinstance(v, (list, tuple)):
            return [_safe_val(x) for x in v]
        if hasattr(v, "__dict__"):
            return {str(k): _safe_val(vv) for k, vv in v.__dict__.items()
                    if not k.startswith("_")}
        return repr(v)

    def _build_msg_type_summary(state):
        """Build a summary of all message types seen, with last payload."""
        last_seen = state.last_seen_messages
        result = []
        for name, info in sorted(last_seen.items(), key=lambda x: -x[1]["count"]):
            safe_fields = _safe_val(info["fields"])
            field_keys = list(safe_fields.keys()) if isinstance(safe_fields, dict) else []
            result.append({
                "name": name,
                "count": info["count"],
                "dir": info["dir"],
                "ts": info["ts"],
                "fields": safe_fields,
                "field_keys": field_keys,
            })
        return result

    @app.route("/viz")
    def viz_page():
        """Protocol visualizer — real-time map + sidebar of all protocol data."""
        detected, _ = _cached_devices()
        device_info = [{"id": d, "name": d.split(":")[-1] if ":" in d else d}
                       for d in detected]
        return render_template("viz.html", devices=device_info)

    # Localization DB (extracted from APK localization bundle)
    _loc_db_cache = [None]  # mutable container for closure

    def _load_loc_db():
        if _loc_db_cache[0] is not None:
            return
        db_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data", "localization_db.json",
        )
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                _loc_db_cache[0] = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _loc_db_cache[0] = {}

    @app.route("/api/item-names")
    def api_item_names():
        """Return item ID → name mapping (backward compat)."""
        _load_loc_db()
        db = _loc_db_cache[0] or {}
        items = db.get("items", {})
        return jsonify({k: {"name": v} for k, v in items.items()})

    @app.route("/api/loc-db")
    def api_loc_db():
        """Return full localization DB (items, heroes, quests, skills, buffs, etc.)."""
        _load_loc_db()
        return jsonify(_loc_db_cache[0] or {})

    @app.route("/api/protocol-viz")
    def api_protocol_viz():
        """Return all protocol data for a device in one JSON blob."""
        device_id = request.args.get("device")
        if not device_id:
            return jsonify({"ok": False, "error": "device required"}), 400

        from startup import _get_device_state
        state = _get_device_state(device_id)
        if state is None:
            return jsonify({
                "ok": False,
                "connected": False,
                "error": "Protocol not active",
            })

        from protocol.messages import LineupState, MapUnitType, MarchAct, RallyState
        from protocol.game_state import GameState, CATEGORIES

        # -- Freshness --
        freshness = {}
        for cat in CATEGORIES:
            age = None
            ts = state.last_update(cat)
            if ts is not None:
                age = round(time.monotonic() - ts, 1)
            freshness[cat] = {
                "fresh": state.is_fresh(cat, max_age_s=30.0),
                "age_s": age,
            }

        # -- Entities (use history — accumulates all ever seen, not just current viewport) --
        raw_entities = state.entity_history
        map_entities = []
        entity_counts = {}
        all_x, all_z = [], []
        for eid, ent in raw_entities.items():
            x, z = GameState._entity_coords(ent)
            if not x and not z:
                continue
            etype = ent.get("field_2", ent.get("type", 0))
            owner = ent.get("field_3") or ent.get("owner") or {}
            name = owner.get("name", "") if isinstance(owner, dict) else ""
            union_id = owner.get("unionID", 0) if isinstance(owner, dict) else 0
            level = owner.get("cityLevel", 0) if isinstance(owner, dict) else 0
            map_entities.append({
                "id": eid, "type": etype, "x": x, "z": z,
                "name": name, "union_id": union_id, "level": level,
            })
            all_x.append(x)
            all_z.append(z)
            # Count by type name
            try:
                type_name = MapUnitType(etype).name
            except (ValueError, KeyError):
                type_name = f"UNKNOWN_{etype}"
            entity_counts[type_name] = entity_counts.get(type_name, 0) + 1

        # -- Rallies --
        map_rallies = []
        for rid, rally in state.rallies.items():
            rx, rz = 0, 0
            if rally.rallyCoord:
                rx, rz = rally.rallyCoord.X, rally.rallyCoord.Z
            if rx or rz:
                all_x.append(rx)
                all_z.append(rz)
            try:
                state_name = RallyState(rally.rallyState).name
            except (ValueError, KeyError):
                state_name = str(rally.rallyState)
            map_rallies.append({
                "id": rally.rallyTroopID, "x": rx, "z": rz,
                "owner": rally.unionNickName,
                "state": rally.rallyState, "state_name": state_name,
                "state_end_ts": rally.rallyStateEndTS,
                "troop_count": len(rally.troops),
                "max": rally.rallyMaxNum,
            })

        # -- Troop positions --
        troop_positions = []
        lineups_data = state.lineups
        for lid, ls in state.lineup_states.items():
            if ls.pos and (ls.pos.X or ls.pos.Z):
                all_x.append(ls.pos.X)
                all_z.append(ls.pos.Z)
                try:
                    sname = LineupState(ls.state).name
                except (ValueError, KeyError):
                    sname = str(ls.state)
                lineup = lineups_data.get(lid)
                troop_positions.append({
                    "lineup_id": lid, "state": ls.state,
                    "state_name": sname,
                    "x": ls.pos.X, "z": ls.pos.Z,
                    "end_ts": ls.stateEndTs,
                    "power": lineup.power if lineup else 0,
                })

        # -- Incoming attacks / marches --
        map_attacks = []
        for intel in state.incoming_attacks:
            fx, fz, tx, tz = 0, 0, 0, 0
            if intel.from_coord:
                fx, fz = intel.from_coord.X, intel.from_coord.Z
            if intel.to_coord:
                tx, tz = intel.to_coord.X, intel.to_coord.Z
            if fx or fz:
                all_x.append(fx)
                all_z.append(fz)
            if tx or tz:
                all_x.append(tx)
                all_z.append(tz)
            try:
                act_name = MarchAct(intel.act).name
            except (ValueError, KeyError):
                act_name = str(intel.act)
            map_attacks.append({
                "from_x": fx, "from_z": fz,
                "to_x": tx, "to_z": tz,
                "name": intel.name, "act": intel.act,
                "act_name": act_name,
                "start_ts": intel.startTime,
                "arrive_ts": intel.arriveTime,
                "union": intel.unionNickName,
                "level": intel.cityLevel,
            })

        # -- Ally cities --
        ally_cities = []
        for ac in state.ally_city_entities:
            ax = ac.get("X", 0)
            az = ac.get("Z", 0)
            if ax or az:
                all_x.append(ax)
                all_z.append(az)
            owner = ac.get("field_3") or ac.get("owner") or {}
            ally_cities.append({
                "id": GameState._entity_id(ac),
                "x": ax, "z": az,
                "name": owner.get("name", "") if isinstance(owner, dict) else "",
                "level": owner.get("cityLevel", 0) if isinstance(owner, dict) else 0,
            })

        # -- Territory grid (protocol-only: KvkTerritoryInfoAck) --
        tgrid = state.territory_grid
        territory = {"grid": {}, "available": bool(tgrid)}
        for (row, col), (fid, cfid, lid) in tgrid.items():
            territory["grid"][f"{row},{col}"] = [fid, cfid, lid]

        # -- Bounds --
        bounds = {"min_x": 0, "max_x": 1, "min_z": 0, "max_z": 1}
        if all_x and all_z:
            bounds = {
                "min_x": min(all_x), "max_x": max(all_x),
                "min_z": min(all_z), "max_z": max(all_z),
            }

        # -- Sidebar: lineups --
        sidebar_lineups = []
        for lid, lu in lineups_data.items():
            try:
                sname = LineupState(lu.state).name
            except (ValueError, KeyError):
                sname = str(lu.state)
            ls = state.lineup_states.get(lid)
            sidebar_lineups.append({
                "id": lid, "state": lu.state, "state_name": sname,
                "power": lu.power, "combat_power": lu.combatPower,
                "end_ts": ls.stateEndTs if ls else 0,
            })

        # -- Sidebar: quests --
        sidebar_quests = [
            {"cfg_id": k, "cur_cnt": v.get("curCnt", 0), "state": v.get("state", 0)}
            for k, v in state.quests.items()
        ]

        # -- Sidebar: resources --
        sidebar_resources = []
        for rid, asset in state.resources.items():
            sidebar_resources.append({
                "id": asset.ID, "type": asset.typ, "val": asset.val,
                "cap": asset.cap,
            })

        # -- Sidebar: buffs --
        sidebar_buffs = list(state.buffs)[:20]

        # -- Sidebar: battle results --
        battles = []
        for br in list(state.battle_results)[-10:]:
            if hasattr(br, "atkResult"):
                battles.append({
                    "atk_result": br.atkResult,
                    "def_result": br.defResult,
                    "timestamp": getattr(br, "timestamp", 0),
                })
            elif isinstance(br, dict):
                battles.append(br)

        # -- Sidebar: powers --
        powers = {str(k): list(v) for k, v in state.powers.items()}

        ap = state.ap

        # -- Data from _last_seen (messages without dedicated GameState stores) --
        last_seen = state.last_seen_messages

        # PVP battles (from PvpInfoAck)
        pvp_battles = []
        pvp_info = last_seen.get("PvpInfoAck")
        if pvp_info:
            pi_list = pvp_info.get("fields", {}).get("pi", [])
            if isinstance(pi_list, list):
                for b in pi_list[-50:]:  # last 50
                    if not isinstance(b, dict):
                        continue
                    pvp_battles.append(_safe_val(b))

        # March intelligence (from InformationNtf — richer than IntelligencesNtf)
        marches = []
        info_ntf = last_seen.get("InformationNtf")
        if info_ntf:
            infos = info_ntf.get("fields", {}).get("infos", [])
            if isinstance(infos, list):
                for inf in infos:
                    if isinstance(inf, dict):
                        marches.append(_safe_val(inf))

        # Alliance gifts (from UnionGiftInfoAck)
        gifts = []
        gift_info = last_seen.get("UnionGiftInfoAck")
        if gift_info:
            gf = gift_info.get("fields", {})
            gift_list = gf.get("unionGifts", [])
            if isinstance(gift_list, list):
                for g in gift_list[-30:]:  # last 30
                    if isinstance(g, dict):
                        gifts.append(_safe_val(g))

        # Heroes (from HeroInfoNtf)
        heroes = []
        hero_info = last_seen.get("HeroInfoNtf")
        if hero_info:
            hero_list = hero_info.get("fields", {}).get("heroes", [])
            if isinstance(hero_list, list):
                for h in hero_list:
                    if isinstance(h, dict):
                        heroes.append(_safe_val(h))

        # Mail headers (from Mail2NdHeadListNtf)
        mail_heads = []
        mail_info = last_seen.get("Mail2NdHeadListNtf")
        if mail_info:
            head_list = mail_info.get("fields", {}).get("headList", [])
            if isinstance(head_list, list):
                for m in head_list:
                    if isinstance(m, dict):
                        mail_heads.append(_safe_val(m))

        # Alliance altar (from UnionAltarAck)
        altar = None
        altar_info = last_seen.get("UnionAltarAck")
        if altar_info:
            altar = _safe_val(altar_info.get("fields", {}))

        # Explore atlas (from ExploreAtlasRefreshNtf)
        explore = None
        explore_info = last_seen.get("ExploreAtlasRefreshNtf")
        if explore_info:
            explore = _safe_val(explore_info.get("fields", {}))

        return jsonify({
            "ok": True,
            "device": device_id,
            "server_time": state.server_time,
            "connected": state.protocol_connected,
            "freshness": freshness,
            "map": {
                "entities": map_entities,
                "rallies": map_rallies,
                "troop_positions": troop_positions,
                "attacks": map_attacks,
                "ally_cities": ally_cities,
                "territory": territory,
                "bounds": bounds,
            },
            "sidebar": {
                "ap": {"current": ap[0], "max": ap[1]} if ap else None,
                "lineups": sidebar_lineups,
                "quests": sidebar_quests,
                "resources": sidebar_resources,
                "buffs": sidebar_buffs,
                "battle_results": battles,
                "city_burning": state.city_burning,
                "powers": powers,
                "entity_counts": entity_counts,
                "pvp_battles": pvp_battles,
                "marches": marches,
                "gifts": gifts,
                "heroes": heroes,
                "mail_heads": mail_heads,
                "altar": altar,
                "explore": explore,
            },
            "msg_types": _build_msg_type_summary(state),
        })

    @app.route("/api/protocol-viz-messages")
    def api_protocol_viz_messages():
        """Return protocol message log for the live feed.

        Query params:
          device — device ID (required)
          since  — sequence number (optional, for incremental polling)
        """
        device_id = request.args.get("device")
        if not device_id:
            return jsonify({"ok": False, "error": "device required"}), 400

        from startup import _get_device_state
        state = _get_device_state(device_id)
        if state is None:
            return jsonify({"ok": False, "error": "Protocol not active"})

        since = int(request.args.get("since", 0))
        entries, latest_seq = state.message_log(since)

        safe_entries = []
        for e in entries:
            safe_entries.append({
                "seq": e["seq"],
                "ts": e["ts"],
                "name": e["name"],
                "dir": e["dir"],
                "fields": _safe_val(e["fields"]),
            })

        return jsonify({
            "ok": True,
            "seq": latest_seq,
            "messages": safe_entries,
        })

    @app.route("/chat")
    def chat_page():
        """Chat viewer page — shows live game chat from protocol-enabled devices."""
        detected, _ = _cached_devices()
        device_info = [{"id": d, "name": d.split(":")[-1] if ":" in d else d}
                       for d in detected]
        return render_template("chat.html", devices=device_info)

    @app.route("/api/upload-logs", methods=["POST"])
    def api_upload_logs():
        """Start a bug report upload in the background."""
        from startup import start_manual_upload
        notes = request.form.get("notes", "").strip() or None
        start_manual_upload(notes=notes)
        return jsonify({"ok": True, "message": "Upload started"})

    @app.route("/api/upload-progress")
    def api_upload_progress():
        """Poll upload progress (phase, percent, message)."""
        return jsonify(_get_upload_progress())

    @app.route("/api/quit", methods=["POST"])
    def api_quit():
        """Stop all tasks and terminate the process."""
        _log.info("=== QUIT requested via web dashboard ===")
        stop_all()

        def _do_quit():
            time.sleep(0.5)  # let the HTTP response flush
            # Close pywebview window if available — triggers clean exit flow
            cb = config._quit_callback
            if cb:
                try:
                    cb()
                    time.sleep(2)  # give main thread time to exit normally
                except Exception:
                    pass
            # Hard exit fallback (browser mode, or if window.destroy didn't quit)
            os._exit(0)

        threading.Thread(target=_do_quit, daemon=True).start()
        return jsonify({"ok": True, "message": "Shutting down..."})

    @app.route("/api/logs")
    def api_logs():
        log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
        log_file = os.path.join(log_dir, "9bot.log")
        lines = []
        if os.path.isfile(log_file):
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                # Filter out DEBUG lines for the human-facing viewer
                lines = [l for l in all_lines if " DEBUG " not in l][-150:]
            except Exception as e:
                _log.warning("Failed to read log file: %s", e)
        return jsonify({"lines": [l.rstrip() for l in lines]})

    # --- Territory grid manager ---

    @app.route("/territory")
    def territory_page():
        devs = _cached_devices()[0]
        return render_template("territory.html", devices=devs)

    @app.route("/api/territory/grid")
    def api_territory_grid():
        return jsonify({
            "attack": [list(s) for s in config.MANUAL_ATTACK_SQUARES],
            "ignore": [list(s) for s in config.MANUAL_IGNORE_SQUARES],
            "throne": [[11, 11], [11, 12], [12, 11], [12, 12]],
        })

    @app.route("/api/territory/grid", methods=["POST"])
    def api_territory_grid_save():
        data = request.get_json()
        config.MANUAL_ATTACK_SQUARES = {tuple(s) for s in data.get("attack", [])}
        config.MANUAL_IGNORE_SQUARES = {tuple(s) for s in data.get("ignore", [])}
        return jsonify({"ok": True})

    @app.route("/api/territory/zones")
    def api_territory_zones():
        return jsonify({
            "passes": config.TERRITORY_PASSES,
            "mutual_zones": config.TERRITORY_MUTUAL_ZONES,
            "safe_zones": config.TERRITORY_SAFE_ZONES,
            "home_zones": config.TERRITORY_HOME_ZONES,
        })

    @app.route("/api/territory/zones", methods=["POST"])
    def api_territory_zones_save():
        data = request.get_json()
        passes = data.get("passes", {})
        mutual_zones = data.get("mutual_zones", {})
        safe_zones = data.get("safe_zones", {})
        home_zones = data.get("home_zones", {})
        config.TERRITORY_PASSES = passes
        config.TERRITORY_MUTUAL_ZONES = mutual_zones
        config.TERRITORY_SAFE_ZONES = safe_zones
        config.TERRITORY_HOME_ZONES = home_zones
        config.recompute_pass_blocked()
        # Persist to settings.json
        from settings import load_settings, save_settings
        settings = load_settings()
        settings["territory_passes"] = passes
        settings["territory_mutual_zones"] = mutual_zones
        settings["territory_safe_zones"] = safe_zones
        settings["territory_home_zones"] = home_zones
        save_settings(settings)
        return jsonify({"ok": True, "blocked": len(config.PASS_BLOCKED_SQUARES)})

    @app.route("/api/territory/passes/toggle", methods=["POST"])
    def api_territory_pass_toggle():
        data = request.get_json()
        pass_id = str(data.get("id", ""))
        if pass_id not in config.TERRITORY_PASSES:
            return jsonify({"error": "Unknown pass ID"}), 400
        owned = bool(data.get("owned", False))
        config.TERRITORY_PASSES[pass_id]["owned"] = owned
        config.recompute_pass_blocked()
        # Persist
        from settings import load_settings, save_settings
        settings = load_settings()
        if "territory_passes" not in settings:
            settings["territory_passes"] = {}
        settings["territory_passes"] = config.TERRITORY_PASSES
        save_settings(settings)
        return jsonify({"ok": True, "blocked": len(config.PASS_BLOCKED_SQUARES)})

    @app.route("/api/territory/screenshot")
    def api_territory_screenshot():
        """JPEG crop of the territory grid area for background overlay."""
        device = request.args.get("device", "")
        if not device:
            return "Missing device parameter", 400
        known = set(_cached_devices()[0])
        if device not in known:
            return "Unknown device", 404
        import io
        import cv2
        from flask import send_file
        from config import GRID_OFFSET_X, GRID_OFFSET_Y, GRID_WIDTH, GRID_HEIGHT, SQUARE_SIZE
        screen = load_screenshot(device)
        if screen is None:
            return "Screenshot failed", 500
        # Crop to grid area
        gw = int(GRID_WIDTH * SQUARE_SIZE)
        gh = int(GRID_HEIGHT * SQUARE_SIZE)
        crop = screen[GRID_OFFSET_Y:GRID_OFFSET_Y + gh,
                       GRID_OFFSET_X:GRID_OFFSET_X + gw]
        _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 40])
        return send_file(io.BytesIO(buf.tobytes()), mimetype="image/jpeg")

    @app.route("/api/territory/screenshot/save", methods=["POST"])
    def api_territory_screenshot_save():
        """Save territory grid screenshot as permanent background."""
        device = request.json.get("device", "") if request.is_json else ""
        if not device:
            return jsonify({"error": "Missing device"}), 400
        known = set(_cached_devices()[0])
        if device not in known:
            return jsonify({"error": "Unknown device"}), 404
        import cv2
        from config import GRID_OFFSET_X, GRID_OFFSET_Y, GRID_WIDTH, GRID_HEIGHT, SQUARE_SIZE
        screen = load_screenshot(device)
        if screen is None:
            return jsonify({"error": "Screenshot failed"}), 500
        gw = int(GRID_WIDTH * SQUARE_SIZE)
        gh = int(GRID_HEIGHT * SQUARE_SIZE)
        crop = screen[GRID_OFFSET_Y:GRID_OFFSET_Y + gh,
                       GRID_OFFSET_X:GRID_OFFSET_X + gw]
        bg_path = os.path.join(app.static_folder, "territory_bg.jpg")
        cv2.imwrite(bg_path, crop, [cv2.IMWRITE_JPEG_QUALITY, 50])
        return jsonify({"ok": True})

    @app.route("/api/territory/screenshot/saved")
    def api_territory_screenshot_saved():
        """Serve saved territory background if it exists."""
        from flask import send_file
        bg_path = os.path.join(app.static_folder, "territory_bg.jpg")
        if not os.path.exists(bg_path):
            return "", 404
        return send_file(bg_path, mimetype="image/jpeg")

    # --- QR code generator ---

    @app.route("/api/screenshot")
    def api_screenshot():
        """Take a live screenshot from a device. Returns JPEG if quality param set, else PNG."""
        device = request.args.get("device", "")
        if not device:
            return "Missing device parameter", 400
        known = set(_cached_devices()[0])
        if device not in known:
            return "Unknown device", 404
        import io
        import cv2
        from flask import send_file
        screen = load_screenshot(device)
        if screen is None:
            return "Screenshot failed (ADB error)", 500
        quality = request.args.get("quality")
        as_attachment = bool(request.args.get("download"))
        if quality and not as_attachment:
            try:
                q = max(10, min(95, int(quality)))
            except (ValueError, TypeError):
                q = 30
            _, buf = cv2.imencode(".jpg", screen, [cv2.IMWRITE_JPEG_QUALITY, q])
            return send_file(io.BytesIO(buf.tobytes()), mimetype="image/jpeg")
        _, buf = cv2.imencode(".png", screen)
        return send_file(io.BytesIO(buf.tobytes()), mimetype="image/png",
                         as_attachment=as_attachment,
                         download_name=f"screenshot_{device.replace(':', '_')}.png")

    _last_tap_time = {}  # device → monotonic timestamp for rate limiting

    @app.route("/api/tap", methods=["POST"])
    def api_tap():
        """Forward a screen tap to a device via ADB."""
        data = request.get_json(silent=True) or {}
        device = data.get("device", "")
        if not device:
            return jsonify(ok=False, error="Missing device"), 400
        known = set(_cached_devices()[0])
        if device not in known:
            return jsonify(ok=False, error="Unknown device"), 404
        try:
            x = int(data.get("x", -1))
            y = int(data.get("y", -1))
        except (ValueError, TypeError):
            return jsonify(ok=False, error="Invalid coordinates"), 400
        if not (0 <= x <= 1080 and 0 <= y <= 1920):
            return jsonify(ok=False, error="Coordinates out of range"), 400
        now = time.monotonic()
        if now - _last_tap_time.get(device, 0) < 0.1:
            return jsonify(ok=False, error="Too fast"), 429
        _last_tap_time[device] = now
        adb_tap(device, x, y)
        return jsonify(ok=True)

    @app.route("/api/stop-device", methods=["POST"])
    def api_stop_device():
        """Stop all running tasks for a single device."""
        data = request.get_json(silent=True) or {}
        device = data.get("device", "")
        if not device:
            return jsonify(ok=False, error="Missing device"), 400
        stopped = []
        for key in list(running_tasks.keys()):
            if key.startswith(device + "_"):
                stop_task(key)
                stopped.append(key)
        config.MITHRIL_ENABLED_DEVICES.discard(device)
        config.MITHRIL_DEPLOY_TIME.pop(device, None)
        return jsonify(ok=True, stopped=stopped)

    @app.route("/api/stream")
    def api_stream():
        """MJPEG stream from a device. Query params: device, fps (1-10), quality (10-95)."""
        device = request.args.get("device", "")
        if not device:
            return "Missing device parameter", 400
        known = set(_cached_devices()[0])
        if device not in known:
            return "Unknown device", 404
        import cv2
        from flask import Response
        try:
            fps = max(1, min(10, int(request.args.get("fps", "5"))))
        except (ValueError, TypeError):
            fps = 5
        try:
            quality = max(10, min(95, int(request.args.get("quality", "30"))))
        except (ValueError, TypeError):
            quality = 30
        interval = 1.0 / fps

        def generate():
            while True:
                screen = load_screenshot(device)
                if screen is not None:
                    _, buf = cv2.imencode(".jpg", screen,
                                          [cv2.IMWRITE_JPEG_QUALITY, quality])
                    frame = buf.tobytes()
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                           b"\r\n" + frame + b"\r\n")
                time.sleep(interval)

        return Response(generate(),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/api/qr")
    def api_qr():
        url = request.args.get("url", "")
        if not url:
            return "Missing url parameter", 400
        import io
        import qrcode
        from flask import Response
        qr = qrcode.QRCode(box_size=12, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), mimetype="image/png")

    @app.route("/api/restart-game", methods=["POST"])
    def api_restart_game():
        """Force-stop and relaunch the game on a device."""
        device = request.form.get("device", "")
        if not device:
            return jsonify(ok=False, error="Missing device"), 400
        known = set(_cached_devices()[0])
        if device not in known:
            return jsonify(ok=False, error="Unknown device"), 404
        # Stop all bot tasks on this device first
        for key in list(running_tasks.keys()):
            if key.startswith(device):
                stop_task(key)
        config.DEVICE_STATUS.pop(device, None)
        restart_game(device)
        return jsonify(ok=True)

    # --- Emulator control ---

    def _do_emulator_start(device_id="", instance_name=""):
        """Core logic for starting a BlueStacks emulator instance."""

        # Resolve instance name from device ID if needed
        if device_id and not instance_name:
            conf = get_bluestacks_config()
            for iname, info in conf.items():
                try:
                    if int(info.get("adb_port", "0")) == int(device_id.split(":")[-1]):
                        instance_name = iname
                        break
                except (ValueError, IndexError):
                    pass

        if not instance_name:
            return jsonify(ok=False, error="Could not resolve instance"), 400

        # Build device_id from instance config if not provided
        if not device_id:
            conf = get_bluestacks_config()
            info = conf.get(instance_name, {})
            port = info.get("adb_port")
            if port:
                device_id = f"127.0.0.1:{port}"
            else:
                return jsonify(ok=False, error="No ADB port for instance"), 400

        # Check not already running or starting
        running = get_bluestacks_running()
        if instance_name in running:
            return jsonify(ok=False, error="Instance already running"), 409
        if device_id in config.EMULATOR_STARTING:
            return jsonify(ok=False, error="Instance already starting"), 409

        config.EMULATOR_STARTING[device_id] = {
            "instance": instance_name,
            "started_at": time.time(),
        }
        config.set_device_status(device_id, "Starting Emulator...")

        def _boot_and_wait():
            try:
                proc = start_bluestacks_instance(instance_name)
                if proc is None:
                    config.EMULATOR_STARTING.pop(device_id, None)
                    config.set_device_status(device_id, "Start Failed")
                    return

                config.set_device_status(device_id, "Waiting for ADB...")
                # Poll for ADB connection
                deadline = time.time() + 120
                connected = False
                while time.time() < deadline:
                    time.sleep(3)
                    try:
                        result = subprocess.run(
                            [adb_path, "connect", device_id],
                            capture_output=True, text=True, timeout=5,
                        )
                        if "connected" in result.stdout.lower():
                            # Verify device actually responds
                            check = subprocess.run(
                                [adb_path, "-s", device_id, "shell", "echo", "ok"],
                                capture_output=True, text=True, timeout=5,
                            )
                            if check.returncode == 0:
                                connected = True
                                break
                    except (subprocess.TimeoutExpired, Exception):
                        pass

                config.EMULATOR_STARTING.pop(device_id, None)
                _device_cache["ts"] = 0  # bust cache
                if connected:
                    _log.info("Emulator '%s' is now connected as %s",
                              instance_name, device_id)
                    config.set_device_status(device_id, "Launching Game...")
                    try:
                        restart_game(device_id)
                        _log.info("Game launched on %s", device_id)
                    except Exception as ge:
                        _log.warning("Failed to launch game on %s: %s",
                                     device_id, ge)
                    config.clear_device_status(device_id)
                    # Scheduler will pick up this device on next tick
                else:
                    _log.warning("Emulator '%s' started but ADB not connected "
                                 "after 120s", instance_name)
                    config.set_device_status(device_id, "ADB Timeout")
            except Exception as e:
                _log.error("Emulator boot thread error: %s", e)
                config.EMULATOR_STARTING.pop(device_id, None)
                config.set_device_status(device_id, "Start Failed")

        threading.Thread(target=_boot_and_wait, daemon=True).start()
        return jsonify(ok=True, device=device_id, instance=instance_name)

    @app.route("/api/emulator/start", methods=["POST"])
    def api_emulator_start():
        """Start a BlueStacks emulator instance (owner route)."""
        return _do_emulator_start(
            device_id=request.form.get("device", ""),
            instance_name=request.form.get("instance", ""),
        )

    def _do_emulator_stop(device_id):
        """Core logic for stopping a BlueStacks emulator instance."""
        if not device_id:
            return jsonify(ok=False, error="Missing device"), 400

        instance_name = get_instance_for_device(device_id)
        if not instance_name:
            return jsonify(ok=False, error="Not a BlueStacks device"), 400

        # Stop all bot tasks on this device first
        for key in list(running_tasks.keys()):
            if key.startswith(device_id):
                stop_task(key)
        config.DEVICE_STATUS.pop(device_id, None)

        killed = stop_bluestacks_instance(instance_name)
        _device_cache["ts"] = 0  # bust cache
        if killed:
            _log.info("Emulator '%s' stopped", instance_name)
            return jsonify(ok=True)
        else:
            return jsonify(ok=False, error="Failed to stop instance"), 500

    @app.route("/api/emulator/stop", methods=["POST"])
    def api_emulator_stop():
        """Stop a BlueStacks emulator instance (owner route)."""
        return _do_emulator_stop(device_id=request.form.get("device", ""))

    # --- Device-scoped routes (friend view) ---

    def _resolve_device(dhash):
        """Resolve a device hash to a device ID, or None."""
        devs, _ = _cached_devices()
        from startup import device_hash as _dh
        for d in devs:
            if _dh(d) == dhash:
                return d
        # Also check offline BlueStacks instances
        for inst in get_offline_instances():
            did = inst["device_id"]
            if _dh(did) == dhash:
                return did
        return None

    def require_device_token(f):
        """Decorator: validate token query param or X-Portal-Access header
        and inject device/token/readonly."""
        @functools.wraps(f)
        def wrapper(dhash, *args, **kwargs):
            device = _resolve_device(dhash)
            if device is None:
                abort(404)
            # Check portal header first (injected by relay proxy)
            portal_access = request.headers.get("X-Portal-Access")
            if portal_access in ("full", "readonly"):
                kwargs["device"] = device
                kwargs["token"] = ""
                kwargs["readonly"] = (portal_access == "readonly")
                return f(dhash, *args, **kwargs)
            # Fall back to legacy token auth
            token = request.args.get("token", "")
            from startup import validate_device_token
            access = validate_device_token(device, token)
            if access is None:
                abort(403)
            kwargs["device"] = device
            kwargs["token"] = token
            kwargs["readonly"] = (access == "readonly")
            return f(dhash, *args, **kwargs)
        return wrapper

    def require_full_access(f):
        """Decorator: reject read-only tokens for write operations."""
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if kwargs.get("readonly"):
                abort(403)
            return f(*args, **kwargs)
        return wrapper

    # All auto-mode keys (for shared permissions defaults)
    ALL_AUTO_MODE_KEYS = []
    for _grp in AUTO_MODES_BL + AUTO_MODES_HS:
        for _m in _grp["modes"]:
            if _m["key"] not in ALL_AUTO_MODE_KEYS:
                ALL_AUTO_MODE_KEYS.append(_m["key"])

    @app.route("/d/<dhash>")
    @require_device_token
    def device_index(dhash, device=None, token=None, readonly=False):
        """Friend's filtered dashboard — single device only."""
        cleanup_dead_tasks()
        devs, instances = _cached_devices()
        is_offline = device not in devs
        if is_offline:
            # Offline device — find display name from BlueStacks config
            display_name = device
            instance_name = None
            for inst in get_offline_instances():
                if inst["device_id"] == device:
                    display_name = inst["display_name"]
                    instance_name = inst["instance"]
                    break
            device_info = [{
                "id": device,
                "name": display_name,
                "status": "Starting Emulator..." if device in config.EMULATOR_STARTING else "Offline",
                "troops": 0,
                "offline": True,
                "instance": instance_name,
            }]
        else:
            emu_inst = get_instance_for_device(device)
            device_info = [{
                "id": device,
                "name": instances.get(device, device),
                "status": config.DEVICE_STATUS.get(device, "Idle"),
                "troops": config.DEVICE_TOTAL_TROOPS.get(device, 5),
                "emu_instance": emu_inst,
            }]
        active_tasks = []
        for key, info in list(running_tasks.items()):
            if isinstance(info, dict):
                thread = info.get("thread")
                if thread and thread.is_alive() and key.startswith(device):
                    active_tasks.append(key)
        settings = _load_settings()
        mode = settings.get("mode", "bl")
        auto_groups = AUTO_MODES_BL if mode == "bl" else AUTO_MODES_HS

        # Filter by shared permissions
        dev_settings = settings.get("device_settings", {}).get(device, {})
        allowed_modes = dev_settings.get("shared_modes")  # None = all
        allowed_actions = dev_settings.get("shared_actions")  # None = all

        if allowed_modes is not None:
            allowed_set = set(allowed_modes)
            auto_groups = []
            for grp in (AUTO_MODES_BL if mode == "bl" else AUTO_MODES_HS):
                filtered = [m for m in grp["modes"] if m["key"] in allowed_set]
                if filtered:
                    auto_groups.append({"group": grp["group"], "modes": filtered})

        farm = list(ONESHOT_FARM)
        war = list(ONESHOT_WAR)
        if allowed_actions is not None:
            allowed_act = set(allowed_actions)
            farm = [a for a in farm if a in allowed_act]
            war = [a for a in war if a in allowed_act]

        return render_template("index.html",
                               devices=device_info,
                               tasks=active_tasks,
                               task_count=len(active_tasks),
                               auto_groups=auto_groups,
                               mode=mode,
                               oneshot_farm=farm,
                               oneshot_war=war,
                               active_tasks=active_tasks,
                               local_ip=get_local_ip(),
                               relay_url=None,
                               share_data={},
                               device_filter=True,
                               device_readonly=readonly,
                               device_hash=dhash,
                               device_token=token)

    @app.route("/d/<dhash>/api/status")
    @require_device_token
    def device_api_status(dhash, device=None, token=None, readonly=False):
        """Status for one device only."""
        cleanup_dead_tasks()
        devs, instances = _cached_devices()
        if device not in devs:
            # Offline device
            starting = device in config.EMULATOR_STARTING
            info = {
                "id": device,
                "name": device,
                "status": "Starting Emulator..." if starting else "Offline",
                "troops": [],
                "snapshot_age": None,
                "quests": [],
                "quest_age": None,
                "mithril_next": None,
                "emu_running": False,
                "offline": True,
                "emu_starting": starting,
            }
            # Try to get display name from BlueStacks config
            for inst in get_offline_instances():
                if inst["device_id"] == device:
                    info["name"] = inst["display_name"]
                    break
        else:
            info = _device_status_info(device, instances)
        active = []
        for key, val in list(running_tasks.items()):
            if isinstance(val, dict):
                thread = val.get("thread")
                if thread and thread.is_alive() and key.startswith(device):
                    active.append(key)
        # Chat mirroring: if chat_mirror enabled and any device has protocol, this device gets chat
        settings = _load_settings()
        chat_mirror = settings.get("chat_mirror", True)
        any_protocol = bool(config.PROTOCOL_ACTIVE_DEVICES)
        info["chat_available"] = (device in config.PROTOCOL_ACTIVE_DEVICES) or (chat_mirror and any_protocol)

        return jsonify({"devices": [info], "tasks": active,
                        "tunnel": tunnel_status(),
                        "upload": _upload_status()})

    @app.route("/d/<dhash>/tasks/start", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_start_task(dhash, device=None, token=None, readonly=False):
        """Start task on this device only."""
        task_name = request.form.get("task_name")
        task_type = request.form.get("task_type", "oneshot")
        settings = _load_settings()

        # Validate device is known
        known = set(_cached_devices()[0])
        if device not in known:
            abort(404)

        # Enforce shared permissions
        dev_settings = settings.get("device_settings", {}).get(device, {})
        if task_type == "auto":
            allowed_modes = dev_settings.get("shared_modes")
            if allowed_modes is not None and task_name not in allowed_modes:
                abort(403)
        else:
            allowed_actions = dev_settings.get("shared_actions")
            if allowed_actions is not None and task_name not in allowed_actions:
                abort(403)

        if task_type == "auto":
            mode_key = task_name
            task_key = f"{device}_{mode_key}"
            if task_key in running_tasks:
                info = running_tasks[task_key]
                if isinstance(info, dict) and info.get("thread") and info["thread"].is_alive():
                    return redirect(f"/d/{dhash}?token={token}")

            for conflict in _EXCLUSIVE_MODES.get(mode_key, []):
                ckey = f"{device}_{conflict}"
                if ckey in running_tasks:
                    stop_task(ckey)

            runner = AUTO_RUNNERS.get(mode_key)
            if runner:
                stop_event = threading.Event()
                if mode_key == "auto_mithril":
                    config.MITHRIL_ENABLED_DEVICES.add(device)
                launch_task(device, mode_key,
                            lambda d=device, se=stop_event, s=settings: runner(d, se, s),
                            stop_event)
        else:
            func = TASK_FUNCTIONS.get(task_name)
            if func:
                stop_event = threading.Event()
                launch_task(device, f"once:{task_name}",
                            run_once, stop_event,
                            args=(device, task_name, func))

        return redirect(f"/d/{dhash}?token={token}")

    @app.route("/d/<dhash>/tasks/stop", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_stop_task(dhash, device=None, token=None, readonly=False):
        """Stop a specific task on this device."""
        task_key = request.form.get("task_key", "")
        # Only allow stopping tasks belonging to this device
        if task_key and task_key.startswith(device):
            stop_task(task_key)
        return redirect(f"/d/{dhash}?token={token}")

    @app.route("/d/<dhash>/tasks/stop-mode", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_stop_mode(dhash, device=None, token=None, readonly=False):
        """Stop an auto-mode on this device."""
        mode_key = request.form.get("mode_key")
        if mode_key:
            task_key = f"{device}_{mode_key}"
            if mode_key == "auto_mithril":
                config.MITHRIL_ENABLED_DEVICES.discard(device)
                config.MITHRIL_DEPLOY_TIME.pop(device, None)
            if task_key in running_tasks:
                stop_task(task_key)
        return redirect(f"/d/{dhash}?token={token}")

    @app.route("/d/<dhash>/tasks/stop-all", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_stop_all(dhash, device=None, token=None, readonly=False):
        """Stop all tasks on this device only."""
        for key in list(running_tasks.keys()):
            if key.startswith(device):
                stop_task(key)
        config.DEVICE_STATUS.pop(device, None)
        return redirect(f"/d/{dhash}?token={token}")

    @app.route("/d/<dhash>/api/restart-game", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_restart_game(dhash, device=None, token=None, readonly=False):
        """Force-stop and relaunch the game on this device."""
        for key in list(running_tasks.keys()):
            if key.startswith(device):
                stop_task(key)
        config.DEVICE_STATUS.pop(device, None)
        restart_game(device)
        return jsonify(ok=True)

    @app.route("/d/<dhash>/api/emulator/start", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_emulator_start(dhash, device=None, token=None, readonly=False):
        """Start emulator for this device."""
        return _do_emulator_start(device_id=device)

    @app.route("/d/<dhash>/api/emulator/stop", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_emulator_stop(dhash, device=None, token=None, readonly=False):
        """Stop emulator for this device."""
        return _do_emulator_stop(device_id=device)

    @app.route("/d/<dhash>/api/chat")
    @require_device_token
    def device_api_chat(dhash, device=None, token=None, readonly=False):
        """Chat messages for this device (with mirroring from protocol-active devices)."""
        channel = request.args.get("channel")
        from startup import get_protocol_chat_messages
        messages = get_protocol_chat_messages(device)
        mirror_source = None
        if not messages and device not in config.PROTOCOL_ACTIVE_DEVICES:
            settings = _load_settings()
            if settings.get("chat_mirror", True):
                for active_dev in config.PROTOCOL_ACTIVE_DEVICES:
                    mirrored = get_protocol_chat_messages(active_dev)
                    if mirrored:
                        messages = [m for m in mirrored
                                    if isinstance(m, dict)
                                    and m.get("channel", "").upper() != "PRIVATE"]
                        mirror_source = active_dev
                        break
        if channel:
            ch_upper = channel.upper()
            if ch_upper == "UNION":
                messages = [m for m in messages
                            if isinstance(m, dict)
                            and m.get("channel", "").upper().startswith("UNION")]
            else:
                messages = [m for m in messages
                            if isinstance(m, dict)
                            and m.get("channel", "").upper() == ch_upper]
        # Filter out system clutter
        _CHAT_SPAM_PREFIXES = ("Shared location of ", "Bizarre Cave")
        messages = [m for m in messages
                    if not (isinstance(m, dict)
                            and any(str(m.get("content", "")).startswith(p)
                                    for p in _CHAT_SPAM_PREFIXES))]
        serializable = []
        for m in messages:
            if isinstance(m, dict):
                serializable.append({
                    "content": m.get("content", ""),
                    "sender": m.get("sender", ""),
                    "channel": m.get("channel", ""),
                    "channel_type": m.get("channel_type", 0),
                    "timestamp": m.get("timestamp", 0),
                    "union_name": m.get("union_name", ""),
                    "payload_type": m.get("payload_type", 0),
                    "translated": m.get("translated"),
                    "source_language": m.get("source_language", ""),
                })
        serializable.sort(key=lambda m: m.get("timestamp", 0))
        result = {"messages": serializable, "count": len(serializable)}
        if mirror_source:
            result["mirrored"] = True
        return jsonify(result)

    @app.route("/d/<dhash>/api/screenshot")
    @require_device_token
    def device_screenshot(dhash, device=None, token=None, readonly=False):
        """Screenshot of this device. Returns JPEG if quality param set, else PNG."""
        import io
        import cv2
        from flask import send_file
        screen = load_screenshot(device)
        if screen is None:
            return "Screenshot failed (ADB error)", 500
        quality = request.args.get("quality")
        as_attachment = bool(request.args.get("download"))
        if quality and not as_attachment:
            try:
                q = max(10, min(95, int(quality)))
            except (ValueError, TypeError):
                q = 30
            _, buf = cv2.imencode(".jpg", screen, [cv2.IMWRITE_JPEG_QUALITY, q])
            return send_file(io.BytesIO(buf.tobytes()), mimetype="image/jpeg")
        _, buf = cv2.imencode(".png", screen)
        return send_file(io.BytesIO(buf.tobytes()), mimetype="image/png",
                         as_attachment=as_attachment,
                         download_name=f"screenshot_{device.replace(':', '_')}.png")

    @app.route("/d/<dhash>/api/tap", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_tap(dhash, device=None, token=None, readonly=False):
        """Forward a screen tap to this device via ADB (full access only)."""
        data = request.get_json(silent=True) or {}
        try:
            x = int(data.get("x", -1))
            y = int(data.get("y", -1))
        except (ValueError, TypeError):
            return jsonify(ok=False, error="Invalid coordinates"), 400
        if not (0 <= x <= 1080 and 0 <= y <= 1920):
            return jsonify(ok=False, error="Coordinates out of range"), 400
        now = time.monotonic()
        if now - _last_tap_time.get(device, 0) < 0.1:
            return jsonify(ok=False, error="Too fast"), 429
        _last_tap_time[device] = now
        adb_tap(device, x, y)
        return jsonify(ok=True)

    @app.route("/d/<dhash>/api/stream")
    @require_device_token
    def device_stream(dhash, device=None, token=None, readonly=False):
        """MJPEG stream for this device (friend view)."""
        import cv2
        from flask import Response
        try:
            fps = max(1, min(10, int(request.args.get("fps", "5"))))
        except (ValueError, TypeError):
            fps = 5
        try:
            quality = max(10, min(95, int(request.args.get("quality", "30"))))
        except (ValueError, TypeError):
            quality = 30
        interval = 1.0 / fps

        def generate():
            while True:
                screen = load_screenshot(device)
                if screen is not None:
                    _, buf = cv2.imencode(".jpg", screen,
                                          [cv2.IMWRITE_JPEG_QUALITY, quality])
                    frame = buf.tobytes()
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(frame)).encode() + b"\r\n"
                           b"\r\n" + frame + b"\r\n")
                time.sleep(interval)

        return Response(generate(),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

    # --- Device-scoped settings routes (friend view) ---

    @app.route("/d/<dhash>/settings")
    @require_device_token
    @require_full_access
    def device_settings(dhash, device=None, token=None, readonly=False):
        """Per-device settings page accessible via shared token."""
        detected, instances = _cached_devices()
        settings = _load_settings()
        dev_overrides = settings.get("device_settings", {}).get(device, {})
        mode = settings.get("mode", "bl")
        auto_groups = AUTO_MODES_BL if mode == "bl" else AUTO_MODES_HS
        from runners import _MODE_LABELS
        schedule_modes = [{"key": k, "label": _MODE_LABELS.get(k, k)}
                          for k in AUTO_RUNNERS.keys()
                          if k != "debug_occupy"]
        saved_dt = settings.get("device_troops", {})
        device_troop_count = saved_dt.get(device, 5)
        return render_template("settings_device.html",
                               device_id=device,
                               device_name=instances.get(device, device.split(":")[-1]),
                               all_devices=[],
                               overrides=dev_overrides,
                               globals=settings,
                               auto_groups=auto_groups,
                               all_actions=ONESHOT_FARM + ONESHOT_WAR,
                               form_action=f"/d/{dhash}/settings?token={token}",
                               reset_action=f"/d/{dhash}/settings/reset?token={token}",
                               device_filter=True,
                               device_hash=dhash,
                               device_token=token,
                               schedule_modes=schedule_modes,
                               device_troop_count=device_troop_count)

    @app.route("/d/<dhash>/settings", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_settings_save(dhash, device=None, token=None, readonly=False):
        """Save per-device settings from shared token view."""
        detected = _cached_devices()[0]
        if device not in detected:
            abort(404)
        from settings import DEVICE_OVERRIDABLE_KEYS
        settings = _load_settings()
        ds = settings.setdefault("device_settings", {})
        overrides = {}

        # Boolean overridable keys
        bool_keys = {"auto_heal", "auto_restore_ap", "ap_use_free", "ap_use_potions",
                      "ap_allow_large_potions", "ap_use_gems", "eg_rally_own",
                      "titan_rally_own", "gather_enabled", "tower_quest_enabled"}
        for key in bool_keys & DEVICE_OVERRIDABLE_KEYS:
            if f"override_{key}" in request.form:
                val = request.form.get(key, "")
                overrides[key] = bool(val and val != "")

        # Integer overridable keys
        int_keys = {"ap_gem_limit", "min_troops", "mithril_interval",
                     "gather_mine_level", "gather_max_troops"}
        for key in int_keys & DEVICE_OVERRIDABLE_KEYS:
            if f"override_{key}" in request.form:
                val = request.form.get(key, "")
                if val.isdigit():
                    overrides[key] = int(val)

        # String overridable keys
        str_keys = {"my_team"}
        for key in str_keys & DEVICE_OVERRIDABLE_KEYS:
            if f"override_{key}" in request.form:
                val = request.form.get(key)
                if val is not None:
                    overrides[key] = val

        # Preserve shared_modes/shared_actions from existing overrides
        # (shared users cannot edit their own permissions)
        existing = ds.get(device, {})
        if "shared_modes" in existing:
            overrides["shared_modes"] = existing["shared_modes"]
        if "shared_actions" in existing:
            overrides["shared_actions"] = existing["shared_actions"]

        ds[device] = overrides
        from config import validate_settings
        settings, warnings = validate_settings(settings, DEFAULTS)
        for w in warnings:
            _log.warning("Settings (device save): %s", w)
        _apply_settings(settings)
        _save_settings(settings)
        return redirect(f"/d/{dhash}/settings?token={token}")

    @app.route("/d/<dhash>/settings/reset", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_settings_reset(dhash, device=None, token=None, readonly=False):
        """Reset per-device overrides from shared token view."""
        detected = _cached_devices()[0]
        if device not in detected:
            abort(404)
        settings = _load_settings()
        ds = settings.get("device_settings", {})
        # Preserve shared_modes/shared_actions (owner-only access control)
        existing = ds.get(device, {})
        preserved = {}
        if "shared_modes" in existing:
            preserved["shared_modes"] = existing["shared_modes"]
        if "shared_actions" in existing:
            preserved["shared_actions"] = existing["shared_actions"]
        if preserved:
            ds[device] = preserved
        else:
            ds.pop(device, None)
        settings["device_settings"] = ds
        _apply_settings(settings)
        _save_settings(settings)
        return redirect(f"/d/{dhash}/settings?token={token}")

    # --- Device-scoped AJAX settings API (friend view) ---

    @app.route("/d/<dhash>/api/settings/save", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_api_settings_save(dhash, device=None, token=None, readonly=False):
        """Save a single per-device override via AJAX (friend view)."""
        data = request.get_json(silent=True) or {}
        key = data.get("key")
        value = data.get("value")
        if not key:
            return jsonify(ok=False, error="Missing key"), 400
        ok, err = _ajax_save_device_setting(device, key, value)
        if not ok:
            return jsonify(ok=False, error=err), 400
        return jsonify(ok=True)

    @app.route("/d/<dhash>/api/settings/reset-key", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_api_settings_reset_key(dhash, device=None, token=None, readonly=False):
        """Reset a single per-device override (friend view)."""
        data = request.get_json(silent=True) or {}
        key = data.get("key")
        if not key:
            return jsonify(ok=False, error="Missing key"), 400
        from config import validate_settings
        with _settings_lock:
            settings = _load_settings()
            ds = settings.get("device_settings", {})
            dev = ds.get(device, {})
            dev.pop(key, None)
            ds[device] = dev
            settings["device_settings"] = ds
            settings, _ = validate_settings(settings, DEFAULTS)
            _apply_settings(settings)
            _save_settings(settings)
        return jsonify(ok=True)

    @app.route("/d/<dhash>/api/settings/reset-all", methods=["POST"])
    @require_device_token
    @require_full_access
    def device_api_settings_reset_all(dhash, device=None, token=None, readonly=False):
        """Reset all per-device overrides (friend view)."""
        from config import validate_settings
        with _settings_lock:
            settings = _load_settings()
            ds = settings.get("device_settings", {})
            existing = ds.get(device, {})
            preserved = {}
            if "shared_modes" in existing:
                preserved["shared_modes"] = existing["shared_modes"]
            if "shared_actions" in existing:
                preserved["shared_actions"] = existing["shared_actions"]
            if preserved:
                ds[device] = preserved
            else:
                ds.pop(device, None)
            settings["device_settings"] = ds
            settings, _ = validate_settings(settings, DEFAULTS)
            _apply_settings(settings)
            _save_settings(settings)
        return jsonify(ok=True)

    # --- Calibrate routes ---

    @app.route("/calibrate")
    def calibrate_page():
        detected, instances = _cached_devices()
        device_info = [{"id": d, "name": instances.get(d, d)} for d in detected]
        return render_template("calibrate.html", devices=device_info)

    @app.route("/api/calibrate/tap", methods=["POST"])
    def calibrate_tap():
        """Forward a tap to the device, wait briefly, return fresh screenshot."""
        data = request.get_json(silent=True) or {}
        device = data.get("device", "")
        try:
            x, y = int(data["x"]), int(data["y"])
        except (KeyError, ValueError, TypeError):
            return jsonify(error="Missing or invalid x/y"), 400
        known = set(_cached_devices()[0])
        if device not in known:
            return jsonify(error="Unknown device"), 404
        adb_tap(device, x, y)
        time.sleep(0.3)
        import io, cv2
        from flask import send_file
        screen = load_screenshot(device)
        if screen is None:
            return jsonify(error="Screenshot failed"), 500
        _, buf = cv2.imencode(".png", screen)
        return send_file(io.BytesIO(buf.tobytes()), mimetype="image/png")

    @app.route("/api/calibrate/crop", methods=["POST"])
    def calibrate_crop():
        """Crop a region from a fresh screenshot and save as template PNG."""
        data = request.get_json(silent=True) or {}
        device = data.get("device", "")
        filename = data.get("filename", "").strip()
        overwrite = data.get("overwrite", False)
        try:
            x1, y1, x2, y2 = int(data["x1"]), int(data["y1"]), int(data["x2"]), int(data["y2"])
        except (KeyError, ValueError, TypeError):
            return jsonify(error="Missing or invalid region coordinates"), 400
        if not filename:
            return jsonify(error="Missing filename"), 400
        if not filename.endswith(".png"):
            filename += ".png"
        # Path traversal protection
        if "/" in filename or "\\" in filename or ".." in filename:
            return jsonify(error="Invalid filename"), 400
        known = set(_cached_devices()[0])
        if device not in known:
            return jsonify(error="Unknown device"), 404
        # Validate region bounds
        if x1 < 0 or y1 < 0 or x2 > 1080 or y2 > 1920 or x1 >= x2 or y1 >= y2:
            return jsonify(error="Invalid region bounds"), 400
        elements_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "elements")
        out_path = os.path.join(elements_dir, filename)
        if os.path.exists(out_path) and not overwrite:
            return jsonify(error=f"{filename} already exists", exists=True), 409
        import cv2
        screen = load_screenshot(device)
        if screen is None:
            return jsonify(error="Screenshot failed"), 500
        crop = screen[y1:y2, x1:x2]
        os.makedirs(elements_dir, exist_ok=True)
        cv2.imwrite(out_path, crop)
        h, w = crop.shape[:2]
        return jsonify(ok=True, filename=filename, width=w, height=h)

    @app.route("/api/calibrate/export", methods=["POST"])
    def calibrate_export():
        """Save a recorded calibration sequence as JSON."""
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        steps = data.get("steps")
        if not name:
            return jsonify(error="Missing name"), 400
        if not steps or not isinstance(steps, list):
            return jsonify(error="Missing or invalid steps"), 400
        # Sanitize name
        if "/" in name or "\\" in name or ".." in name:
            return jsonify(error="Invalid name"), 400
        if not name.endswith(".json"):
            name += ".json"
        cal_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               "data", "calibrations")
        os.makedirs(cal_dir, exist_ok=True)
        out_path = os.path.join(cal_dir, name)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"name": name, "steps": steps}, f, indent=2)
        return jsonify(ok=True, path=f"data/calibrations/{name}")

    # --- General Task Scheduler ---

    def _in_time_window(start_str, end_str):
        """Check if current time is within HH:MM window. Supports overnight wrap."""
        from datetime import datetime
        try:
            now = datetime.now()
            current = now.hour * 60 + now.minute
            ps = start_str.split(":")
            pe = end_str.split(":")
            start = int(ps[0]) * 60 + int(ps[1])
            end = int(pe[0]) * 60 + int(pe[1])
            if start <= end:
                return start <= current < end
            else:
                return current >= start or current < end
        except (ValueError, IndexError):
            return False

    def _was_in_window_recently(start_str, end_str):
        """Check if we were in this window 2 minutes ago (for suppression cleanup)."""
        from datetime import datetime, timedelta
        try:
            past = datetime.now() - timedelta(minutes=2)
            current = past.hour * 60 + past.minute
            ps = start_str.split(":")
            pe = end_str.split(":")
            start = int(ps[0]) * 60 + int(ps[1])
            end = int(pe[0]) * 60 + int(pe[1])
            if start <= end:
                return start <= current < end
            else:
                return current >= start or current < end
        except (ValueError, IndexError):
            return False

    def _scheduler_start_task(device_id, mode_key, schedule_id, settings):
        """Start a scheduled task on a device."""
        with _task_start_lock:
            task_key = f"{device_id}_{mode_key}"
            if task_key in running_tasks:
                info = running_tasks[task_key]
                if isinstance(info, dict) and info.get("thread") and info["thread"].is_alive():
                    return  # Already running
            # Exclusivity
            for conflict in _EXCLUSIVE_MODES.get(mode_key, []):
                ckey = f"{device_id}_{conflict}"
                if ckey in running_tasks:
                    stop_task(ckey)
                    config.SCHEDULED_TASKS.pop(ckey, None)
            runner = AUTO_RUNNERS.get(mode_key)
            if runner:
                stop_event = threading.Event()
                if mode_key == "auto_mithril":
                    config.MITHRIL_ENABLED_DEVICES.add(device_id)
                launch_task(device_id, mode_key,
                            lambda d=device_id, se=stop_event, s=settings: runner(d, se, s),
                            stop_event)
                config.SCHEDULED_TASKS[task_key] = schedule_id
                _log.info("Scheduler started '%s' on %s (schedule %s)",
                          mode_key, device_id, schedule_id)

    def _scheduler_tick():
        """One tick of the scheduler — called every 60s."""
        schedules = config.SCHEDULES
        if not schedules:
            return

        settings = _load_settings()
        devs, _ = _cached_devices()
        cleanup_dead_tasks()

        # Determine active and inactive schedule IDs
        active_schedules = []
        active_ids = set()
        inactive_ids = set()
        for sched in schedules:
            if not sched.get("enabled", True):
                inactive_ids.add(sched["id"])
                continue
            if _in_time_window(sched.get("start", "00:00"), sched.get("end", "00:00")):
                active_schedules.append(sched)
                active_ids.add(sched["id"])
            else:
                inactive_ids.add(sched["id"])

        # Clean up SCHEDULE_SUPPRESSED for windows that went inactive
        for task_key in list(config.SCHEDULE_SUPPRESSED):
            # Find the schedule that originally owned this task
            sched_id = config.SCHEDULED_TASKS.get(task_key)
            if sched_id and sched_id in inactive_ids:
                config.SCHEDULE_SUPPRESSED.discard(task_key)
        # Also clean suppressed entries whose schedule is no longer present
        present_ids = active_ids | inactive_ids
        for task_key in list(config.SCHEDULE_SUPPRESSED):
            sid = config.SCHEDULED_TASKS.get(task_key)
            if sid and sid not in present_ids:
                config.SCHEDULE_SUPPRESSED.discard(task_key)

        # For each device: stop expired, start new
        for device in devs:
            # Stop tasks from now-inactive schedules
            for task_key in list(config.SCHEDULED_TASKS.keys()):
                if not task_key.startswith(device + "_"):
                    continue
                sched_id = config.SCHEDULED_TASKS[task_key]
                if sched_id in inactive_ids:
                    if task_key in running_tasks:
                        info = running_tasks[task_key]
                        if isinstance(info, dict) and info.get("thread") and info["thread"].is_alive():
                            _log.info("Scheduler stopping expired task %s", task_key)
                            stop_task(task_key)
                    config.SCHEDULED_TASKS.pop(task_key, None)
                    config.SCHEDULE_SUPPRESSED.discard(task_key)

            # Check if device is busy (any running task)
            device_busy = False
            for key in list(running_tasks.keys()):
                if key.startswith(device + "_"):
                    info = running_tasks[key]
                    if isinstance(info, dict) and info.get("thread") and info["thread"].is_alive():
                        device_busy = True
                        break

            if device_busy:
                continue  # Don't interrupt running tasks

            # Start tasks from active schedules
            for sched in active_schedules:
                mode_key = sched.get("mode", "")
                if mode_key not in AUTO_RUNNERS:
                    continue
                task_key = f"{device}_{mode_key}"
                if task_key in config.SCHEDULE_SUPPRESSED:
                    continue  # Manually stopped — don't restart until window resets
                _scheduler_start_task(device, mode_key, sched["id"], settings)
                break  # One task per device per tick

        # Boot offline emulators for active schedules with boot=true
        for sched in active_schedules:
            if not sched.get("boot", False):
                continue
            for inst in get_offline_instances():
                did = inst["device_id"]
                if did in config.EMULATOR_STARTING:
                    continue
                # Don't boot if already online
                if did in devs:
                    continue
                _log.info("Scheduler booting emulator for %s (schedule %s)",
                          did, sched["id"])
                _do_emulator_start(device_id=did)
                # Only boot one at a time to avoid overwhelming the system
                break

    def _scheduler_loop():
        """Background thread running the scheduler every 60s."""
        _log.info("Scheduler thread started")
        while True:
            try:
                _scheduler_tick()
            except Exception as e:
                _log.error("Scheduler tick error: %s", e, exc_info=True)
            time.sleep(60)

    # Start scheduler thread
    _sched_thread = threading.Thread(target=_scheduler_loop, daemon=True,
                                     name="scheduler")
    _sched_thread.start()

    return app
