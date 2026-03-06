"""9Bot startup & shutdown — shared initialization for all entry points.

Used by both ``run_web.py`` (web-only) and ``main.py`` (legacy tkinter GUI).
"""

import io
import os
import sys
import json
import base64
import hashlib
import hmac
import logging
log = logging.getLogger("startup")
import platform
import subprocess
import threading
import time

import config
from config import (running_tasks, set_min_troops, set_auto_heal,
                    set_auto_restore_ap, set_ap_restore_options,
                    set_territory_config, set_eg_rally_own, set_titan_rally_own,
                    set_gather_options, set_tower_quest_enabled,
                    set_protocol_enabled)
from settings import load_settings, save_settings

# Relay server connection details (obfuscated, not plaintext in source)
_RELAY_URL_B64 = "d3NzOi8vMTQ1My5saWZlL3dzL3R1bm5lbA=="
_RELAY_SECRET_B64 = "MEpRR2l2bmJDMkNEUHlaS3dFVW5Qc1FrbGlWZ0phMXVZbmZ3MktOcHpYTQ=="


def get_relay_config(settings):
    """Compute relay configuration, auto-deriving from the license key.

    Returns ``(relay_url, relay_secret, bot_name)`` when relay should be
    active, or ``None`` when it should be disabled.
    """
    if not settings.get("remote_access", True):
        return None

    try:
        from license import get_license_key
        key = get_license_key()
    except Exception:
        key = None

    if not key:
        return None

    bot_name = hashlib.sha256(key.encode()).hexdigest()[:10]
    relay_url = base64.b64decode(_RELAY_URL_B64).decode()
    relay_secret = base64.b64decode(_RELAY_SECRET_B64).decode()
    return relay_url, relay_secret, bot_name


def device_hash(device_id):
    """Short URL-safe hash of a device ID (doesn't expose IP/port)."""
    return hashlib.sha256(device_id.encode()).hexdigest()[:8]


def _get_license_key():
    try:
        from license import get_license_key
        return get_license_key()
    except Exception:
        return None


def generate_device_token(device_id):
    """Deterministic per-device token derived from the license key.

    Returns a 16-char hex string, or ``None`` if no license key is available.
    """
    key = _get_license_key()
    if not key:
        return None
    return hashlib.sha256(f"{key}:{device_id}".encode()).hexdigest()[:16]


def generate_device_ro_token(device_id):
    """Deterministic read-only token for a device.

    Returns a 16-char hex string, or ``None`` if no license key is available.
    """
    key = _get_license_key()
    if not key:
        return None
    return hashlib.sha256(f"{key}:ro:{device_id}".encode()).hexdigest()[:16]


def validate_device_token(device_id, token):
    """Validate a device token using constant-time comparison.

    Returns ``"full"``, ``"readonly"``, or ``None`` (invalid).
    """
    full = generate_device_token(device_id)
    if full and hmac.compare_digest(token, full):
        return "full"
    ro = generate_device_ro_token(device_id)
    if ro and hmac.compare_digest(token, ro):
        return "readonly"
    return None


# ---------------------------------------------------------------------------
# Protocol interceptor lifecycle
# ---------------------------------------------------------------------------

# Per-device protocol instances:
#   {device_id: {"bus": EventBus, "state": GameState,
#                "thread": InterceptorThread, "port": int}}
_device_protocol = {}
_device_protocol_lock = threading.Lock()
_FRIDA_BASE_PORT = 27042
_stale_lineup_warned = {}  # {device: last_warn_time} — throttle "lineups stale (never)" logs


def _allocate_port():
    """Allocate the next available host port for Frida forwarding.

    Must be called while holding ``_device_protocol_lock``.
    """
    used = {info["port"] for info in _device_protocol.values()}
    port = _FRIDA_BASE_PORT
    while port in used:
        port += 1
    return port


def _setup_frida_forward_for_device(device_id, host_port):
    """Set ADB forward for a single device: host_port -> 27042 (gadget port)."""
    import subprocess
    from botlog import get_logger
    log = get_logger("startup")
    try:
        subprocess.run(
            [config.adb_path, "-s", device_id, "forward",
             f"tcp:{host_port}", "tcp:27042"],
            capture_output=True, timeout=5,
        )
        log.debug("ADB forward tcp:%d -> tcp:27042 for %s", host_port, device_id)
    except Exception:
        log.warning("Failed to set ADB forward for %s", device_id, exc_info=True)


def _ensure_lz4():
    """Auto-install lz4 if missing (needed for protocol CompressedMessage)."""
    try:
        import lz4.block  # noqa: F401
    except ImportError:
        from botlog import get_logger
        log = get_logger("startup")
        log.info("Installing lz4 package for protocol decompression...")
        import subprocess, sys
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "lz4", "--quiet"],
                timeout=60,
            )
            log.info("lz4 installed successfully")
        except Exception:
            log.warning("Failed to auto-install lz4 — CompressedMessage decoding "
                        "will be unavailable. Install manually: pip install lz4")


def start_protocol_for_device(device_id):
    """Start protocol interception for a single device.

    Creates a dedicated EventBus, GameState, and InterceptorThread for
    *device_id*.  Allocates a unique host port for ADB forward and adds
    the device to ``config.PROTOCOL_ACTIVE_DEVICES``.
    """
    from botlog import get_logger
    log = get_logger("startup")
    with _device_protocol_lock:
        if device_id in _device_protocol:
            return  # already running
    _ensure_lz4()
    try:
        from protocol.events import EventBus
        from protocol.interceptor import InterceptorThread
        from protocol.game_state import GameState
    except ImportError:
        log.warning("Protocol package not available — skipping")
        return
    with _device_protocol_lock:
        if device_id in _device_protocol:
            return  # race check
        host_port = _allocate_port()
        bus = EventBus()
        state = GameState(device_id, bus)
        thread = InterceptorThread(
            event_bus=bus,
            gadget_port=host_port,
            pre_connect=lambda _dev=device_id, _port=host_port: (
                _setup_frida_forward_for_device(_dev, _port)
            ),
        )
        _device_protocol[device_id] = {
            "bus": bus, "state": state, "thread": thread, "port": host_port,
        }
        config.PROTOCOL_ACTIVE_DEVICES.add(device_id)
    thread.start()
    log.info("Protocol interceptor started for %s (port %d)", device_id, host_port)


def stop_protocol_for_device(device_id):
    """Stop protocol interception for a single device."""
    from botlog import get_logger
    with _device_protocol_lock:
        info = _device_protocol.pop(device_id, None)
        config.PROTOCOL_ACTIVE_DEVICES.discard(device_id)
    if info is not None:
        info["thread"].stop()
        info["state"].shutdown()
        get_logger("startup").info("Protocol interceptor stopped for %s", device_id)


def _start_protocol():
    """Start protocol for all connected devices that have protocol_enabled."""
    try:
        from devices import get_devices
        for dev in get_devices():
            if config.get_device_config(dev, "protocol_enabled"):
                start_protocol_for_device(dev)
    except Exception:
        from botlog import get_logger
        get_logger("startup").warning("Protocol startup failed", exc_info=True)


def _stop_protocol():
    """Stop protocol for all devices."""
    with _device_protocol_lock:
        device_ids = list(_device_protocol.keys())
    for dev_id in device_ids:
        stop_protocol_for_device(dev_id)


def _get_device_state(device):
    """Return the GameState for *device*, or None."""
    if device is None:
        return None
    with _device_protocol_lock:
        info = _device_protocol.get(device)
    return info["state"] if info else None


def get_protocol_stats(device=None):
    """Return interceptor stats dict for *device*, or None."""
    if device is None:
        return None
    with _device_protocol_lock:
        info = _device_protocol.get(device)
    if info is None:
        return None
    return info["thread"].stats


def get_protocol_message_type_counts(device=None):
    """Return full protocol message-type counters for *device*, or None."""
    if device is None:
        return None
    with _device_protocol_lock:
        info = _device_protocol.get(device)
    if info is None:
        return None
    return info["thread"].message_type_counts


def get_protocol_message_type_counts_by_direction(device=None, direction: str = "both"):
    """Return protocol message-type counters by direction for *device*.

    *direction* can be ``"recv"``, ``"send"``, or ``"both"``.
    """
    if device is None:
        return None
    with _device_protocol_lock:
        info = _device_protocol.get(device)
    if info is None:
        return None
    thread = info["thread"]
    if direction == "recv":
        return thread.message_type_counts_recv
    if direction == "send":
        return thread.message_type_counts_send
    return thread.message_type_counts


def get_protocol_ap(device=None):
    """Return (current, max) AP from protocol, or None if unavailable/stale."""
    state = _get_device_state(device)
    if state is None:
        return None
    if not state.is_fresh("ap", max_age_s=10.0):
        return None
    return state.ap


# LineupState enum → bot TroopAction mapping
# 0=IDLE, 1=HOME, 2=MARCHING, 3=BATTLING, 4=GATHERING, 5=RETURNING, 6=DEFENDING, 7=RALLYING
_LINEUP_STATE_TO_ACTION = None  # lazy-built on first use


def _get_action_map():
    """Lazy-build LineupState→TroopAction mapping (avoids import at module level).

    Values extracted from game binary via Frida IL2CPP API (LineupState enum).
    """
    global _LINEUP_STATE_TO_ACTION
    if _LINEUP_STATE_TO_ACTION is not None:
        return _LINEUP_STATE_TO_ACTION
    from troops import TroopAction
    _LINEUP_STATE_TO_ACTION = {
        0:  TroopAction.HOME,        # ERR — no deployment / idle
        1:  TroopAction.HOME,        # DEFENDER — at home (available)
        2:  TroopAction.MARCHING,    # OUT_CITY — marching to target
        3:  TroopAction.STATIONING,  # CAMP — stationing at a camp
        4:  TroopAction.RALLYING,    # RALLY — waiting in a rally
        5:  TroopAction.DEFENDING,   # REINFORCE — reinforcing an ally
        6:  TroopAction.GATHERING,   # GATHERING — gathering resources
        7:  TroopAction.BATTLING,    # TROOP_FIGHT — in solo combat
        8:  TroopAction.BATTLING,    # RALLY_FIGHT — in rally combat
        9:  TroopAction.RETURNING,   # RETURN — marching home
        10: TroopAction.MARCHING,    # BUILDING_BUILD — construction march
        11: TroopAction.DEFENDING,   # BUILDING_OCCUPY — occupying a building
        12: TroopAction.DEFENDING,   # BUILDING_DEFEND — defending a building
        13: TroopAction.ADVENTURING, # MINE_EXPLORE — bizarre cave
        14: TroopAction.MARCHING,    # PICKUP — collecting item
        15: TroopAction.GATHERING,   # SCORE_GATHERING — event gathering
    }
    return _LINEUP_STATE_TO_ACTION


def get_protocol_rallies(device=None):
    """Return list of active rallies from protocol, or None if unavailable/stale.

    Returns [] if protocol confirms zero rallies (bail-out signal).
    Returns None if protocol unavailable or data stale (fall through to UI).
    """
    state = _get_device_state(device)
    if state is None:
        return None
    if not state.is_fresh("rallies", max_age_s=30.0):
        return None
    rallies = state.rallies  # thread-safe dict copy
    if not rallies:
        return []  # confirmed: no active rallies
    return list(rallies.values())


def get_protocol_troops_home(device=None):
    """Return count of HOME troops from protocol, or None if unavailable/stale."""
    state = _get_device_state(device)
    if state is None:
        return None
    if not state.is_fresh("lineups", max_age_s=30.0):
        return None
    lineups = state.lineups
    lineup_states = state.lineup_states
    if not lineups:
        return None
    home_count = 0
    for lid, lu in lineups.items():
        # _lineup_states only contains deployed troops (HOME entries are
        # removed on arrival), so missing entry → trust Lineup.state.
        ls = lineup_states.get(lid)
        effective_state = ls.state if ls is not None else lu.state
        if effective_state in (0, 1):  # ERR/idle or DEFENDER = home
            home_count += 1
    return home_count


def get_protocol_chat_messages(device=None):
    """Return recent chat messages for *device*, or empty list."""
    state = _get_device_state(device)
    if state is None:
        return []
    return state.chat_messages  # thread-safe list copy


def get_protocol_event_bus(device=None):
    """Return the EventBus for *device*, or None if protocol is not active."""
    if device is None:
        return None
    with _device_protocol_lock:
        info = _device_protocol.get(device)
    return info["bus"] if info else None


def set_protocol_ally_monitoring(device, enabled: bool) -> None:
    """Enable or disable ally city tracking on the GameState for *device*."""
    try:
        state = _get_device_state(device)
        if state is not None:
            state.set_ally_monitoring(enabled)
    except Exception:
        pass


def get_protocol_ally_cities(device=None):
    """Return list of verified ally PLAYER_CITY entity dicts, or None if unavailable/stale.

    Entities come from UnionEntitiesNtf (server-filtered to own alliance) and are
    additionally validated against own unionID. Returns None when protocol is off or
    entity data has not been received yet (no bail-out signal — unlike rallies, zero
    ally cities on screen is normal). Callers use None as the signal to skip.
    """
    try:
        state = _get_device_state(device)
        if state is None:
            return None
        if not state.is_fresh("entities", max_age_s=60.0):
            return None
        return state.ally_city_entities  # thread-safe list copy
    except Exception:
        return None


_FACTION_TO_TEAM = {1: "red", 2: "blue", 3: "green", 4: "yellow"}


def get_protocol_kvk_tower_troops(device=None):
    """Return KvkBuilding troop counts observed from entity packets, or None.

    Returns dict mapping (row, col) -> troop_count for towers seen in the
    player's viewport since login.  A count > 0 means troops are present.
    Returns None when protocol is off or no data has been collected yet.
    """
    try:
        state = _get_device_state(device)
        if state is None:
            return None
        troops = state.kvk_tower_troops
        return troops if troops is not None else None
    except Exception:
        return None


def get_protocol_territory_grid(device=None):
    """Return territory grid from protocol, or None.

    Returns None when protocol is off, data not yet received, or data is stale.

    Returns dict mapping (row, col) -> (owner_team, contester_team, has_defender):
        owner_team:     team string ("red"/"blue"/"green"/"yellow") or None if unowned
        contester_team: team currently attacking this tower, or None
        has_defender:   True if a troop is occupying/defending this tower
    Only towers with at least an owner or a contester are included.
    """
    try:
        state = _get_device_state(device)
        if state is None:
            return None
        if not state.is_fresh("territory", max_age_s=30.0):
            return None
        raw_grid = state.territory_grid
        if not raw_grid:
            return None
        result = {}
        for (row, col), val in raw_grid.items():
            faction_id, cur_faction_id, legion_id = val[0], val[1], val[2]
            cur_legion_id = val[3] if len(val) > 3 else 0
            owner_team = _FACTION_TO_TEAM.get(faction_id)
            contester_team = _FACTION_TO_TEAM.get(cur_faction_id) if cur_faction_id else None
            has_defender = bool(legion_id) or bool(cur_legion_id)
            if owner_team or contester_team:
                result[(row, col)] = (owner_team, contester_team, has_defender)
        return result
    except Exception:
        return None


def get_protocol_troop_snapshot(device):
    """Build a DeviceTroopSnapshot from protocol lineup data, or None."""
    state = _get_device_state(device)
    if state is None:
        log.debug("proto_snapshot[%s]: no device state", device)
        return None
    if not state.is_fresh("lineups", max_age_s=30.0):
        try:
            age = state.last_update("lineups")
            age_s = f"{time.time() - age:.1f}s ago" if age else "never"
        except Exception:
            age_s = "unknown"
        # Throttle "never" logs — only log once per 60s per device to avoid
        # spamming when interceptor restarts mid-session and hasn't received
        # LineupsNtf yet (which only arrives at login).
        if age_s == "never":
            now = time.time()
            last = _stale_lineup_warned.get(device, 0)
            if now - last < 60:
                return None
            _stale_lineup_warned[device] = now
        log.debug("proto_snapshot[%s]: lineups stale (%s)", device, age_s)
        return None
    lineups = state.lineups
    lineup_states = state.lineup_states
    if not lineups:
        log.debug("proto_snapshot[%s]: lineups empty", device)
        return None

    from troops import TroopAction, TroopStatus, DeviceTroopSnapshot
    action_map = _get_action_map()

    troops = []
    now = time.time()
    now_ms = int(now * 1000)
    server_ts = state.server_time  # epoch milliseconds from HeartBeatAck
    # Validate server_ts sanity — reject if more than 5 min from wall clock
    if server_ts and abs(server_ts - now_ms) > 300_000:
        server_ts = None

    for lid, lu in lineups.items():
        # _lineup_states only contains deployed troops (HOME entries removed),
        # so missing entry → trust Lineup.state directly.
        ls = lineup_states.get(lid)
        effective_state = ls.state if ls is not None else lu.state
        action = action_map.get(effective_state, TroopAction.MARCHING)

        seconds_remaining = None
        if ls is not None and ls.stateEndTs > 0:
            # stateEndTs is epoch milliseconds — compute remaining seconds
            ref_ms = server_ts if server_ts else now_ms
            seconds_remaining = max(0, (ls.stateEndTs - ref_ms) // 1000)

        if action == TroopAction.HOME:
            troops.append(TroopStatus(action=TroopAction.HOME, read_at=now))
        else:
            troops.append(TroopStatus(
                action=action,
                seconds_remaining=seconds_remaining,
                read_at=now,
            ))

    return DeviceTroopSnapshot(device=device, troops=troops, read_at=now, source="protocol")


def apply_settings(settings):
    """Push settings values into config globals.

    Called on startup and whenever settings are saved (from any UI).
    """
    set_auto_heal(settings.get("auto_heal", True))
    set_auto_restore_ap(settings.get("auto_restore_ap", False))
    set_ap_restore_options(
        settings.get("ap_use_free", True),
        settings.get("ap_use_potions", True),
        settings.get("ap_allow_large_potions", True),
        settings.get("ap_use_gems", False),
        settings.get("ap_gem_limit", 0),
    )
    set_min_troops(settings.get("min_troops", 0))
    set_eg_rally_own(settings.get("eg_rally_own", True))
    set_titan_rally_own(settings.get("titan_rally_own", True))
    enemy_teams = settings.get("enemy_teams", [])
    # Migrate legacy single enemy_team to list
    if not enemy_teams and settings.get("enemy_team"):
        enemy_teams = [settings["enemy_team"]]
    set_territory_config(settings.get("my_team", "yellow"), enemy_teams or None)
    config.MITHRIL_INTERVAL = settings.get("mithril_interval", 19)
    for dev_id, ts in settings.get("last_mithril_time", {}).items():
        try:
            ts_f = float(ts)
            interval = config.get_device_config(dev_id, "mithril_interval")
            if time.time() - ts_f < interval * 60:
                config.LAST_MITHRIL_TIME[dev_id] = ts_f
        except (ValueError, TypeError):
            pass
    from botlog import set_console_verbose
    set_console_verbose(settings.get("verbose_logging", False))
    import training
    training.configure(settings.get("collect_training_data", False))
    import chat_translate
    chat_translate.configure(
        settings.get("chat_translate_enabled", False),
        settings.get("chat_translate_api_key", ""),
    )
    set_gather_options(
        settings.get("gather_enabled", True),
        settings.get("gather_mine_level", 4),
        settings.get("gather_max_troops", 3),
    )
    set_tower_quest_enabled(settings.get("tower_quest_enabled", False))
    config.FRONTLINE_OCCUPY_ACTION = settings.get("frontline_occupy_action", "reinforce")
    config.FRONTLINE_ENEMY_TEAMS = settings.get("frontline_enemy_teams", [])
    config.VARIATION = settings.get("variation", 0)
    config.TITAN_INTERVAL = settings.get("titan_interval", 30)
    config.GROOT_INTERVAL = settings.get("groot_interval", 30)
    config.REINFORCE_INTERVAL = settings.get("reinforce_interval", 30)
    config.PASS_INTERVAL = settings.get("pass_interval", 30)
    config.PASS_MODE = settings.get("pass_mode", "Rally Joiner")
    set_protocol_enabled(settings.get("protocol_enabled", False))
    # Territory passes & safe zones
    config.TERRITORY_PASSES = settings.get("territory_passes", {})
    config.TERRITORY_MUTUAL_ZONES = settings.get("territory_mutual_zones", {})
    config.TERRITORY_SAFE_ZONES = settings.get("territory_safe_zones", {})
    config.TERRITORY_HOME_ZONES = settings.get("territory_home_zones", {})
    config.recompute_pass_blocked()
    # Per-device protocol reconciliation (after device_settings are applied below)
    # Deferred to end of function — see _reconcile_protocol() call.
    for dev_id, count in settings.get("device_troops", {}).items():
        try:
            config.DEVICE_TOTAL_TROOPS[dev_id] = int(count)
        except (ValueError, TypeError):
            config.DEVICE_TOTAL_TROOPS[dev_id] = 5

    # Per-device setting overrides
    config.clear_device_overrides()
    for dev_id, overrides in settings.get("device_settings", {}).items():
        config.set_device_overrides(dev_id, overrides)

    # Per-device protocol reconciliation — now that device_settings are applied,
    # start/stop interceptors to match the desired state.
    _reconcile_protocol()


# ---------------------------------------------------------------------------
# APK patching subprocess management
# ---------------------------------------------------------------------------

_patch_progress = {}   # {device_id: {phase, step, total, lines, error}}
_patch_threads = {}    # {device_id: Thread}
_patch_lock = threading.Lock()
_ANSI_RE = None  # lazy-compiled regex


def _strip_ansi(text):
    """Remove ANSI escape codes from text."""
    global _ANSI_RE
    if _ANSI_RE is None:
        import re
        _ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
    return _ANSI_RE.sub("", text)


def start_apk_patch(device_id):
    """Spawn a background thread to patch the APK for *device_id*.

    Runs ``python -m protocol.patch_apk --device <id> --install`` as a
    subprocess, streaming output line-by-line into ``_patch_progress``.
    On success, auto-enables protocol for the device.
    """
    import re
    step_re = re.compile(r"\[(\d+)/(\d+)\]")

    with _patch_lock:
        t = _patch_threads.get(device_id)
        if t is not None and t.is_alive():
            return False  # already running
        _patch_progress[device_id] = {
            "phase": "running", "step": 0, "total": 0,
            "lines": [], "error": None,
        }

    def _run():
        prog = _patch_progress[device_id]
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                [sys.executable, "-m", "protocol.patch_apk",
                 "--device", device_id, "--install"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
            )
            for raw_line in proc.stdout:
                line = _strip_ansi(raw_line.rstrip("\n"))
                if not line:
                    continue
                lines = prog["lines"]
                lines.append(line)
                # Rolling cap: keep last 200 lines
                if len(lines) > 200:
                    del lines[:len(lines) - 200]
                m = step_re.search(line)
                if m:
                    prog["step"] = int(m.group(1))
                    prog["total"] = int(m.group(2))
            proc.wait()
            if proc.returncode == 0:
                prog["phase"] = "done"
                # Auto-enable protocol for this device
                try:
                    settings = load_settings()
                    ds = settings.setdefault("device_settings", {})
                    dev_s = ds.setdefault(device_id, {})
                    dev_s["protocol_enabled"] = True
                    from startup import apply_settings as _apply
                    _apply(settings)
                    save_settings(settings)
                except Exception:
                    pass
            else:
                prog["phase"] = "error"
                # Include last few log lines so the user can see what failed
                tail = [ln for ln in prog["lines"][-5:] if ln.strip()]
                detail = "\n".join(tail) if tail else "(no output)"
                prog["error"] = (f"Process exited with code {proc.returncode}"
                                 f"\n{detail}")
        except Exception as e:
            prog = _patch_progress.get(device_id)
            if prog:
                prog["phase"] = "error"
                prog["error"] = str(e)

    t = threading.Thread(target=_run, daemon=True, name=f"patch-{device_id}")
    with _patch_lock:
        _patch_threads[device_id] = t
    t.start()
    return True


def get_patch_progress(device_id):
    """Return current patch progress dict for *device_id*."""
    return dict(_patch_progress.get(device_id, {"phase": "idle"}))


def is_patching(device_id):
    """Return True if a patch thread is alive for *device_id*."""
    with _patch_lock:
        t = _patch_threads.get(device_id)
    return t is not None and t.is_alive()


def _reconcile_protocol():
    """Start/stop per-device interceptors to match current settings."""
    from botlog import get_logger
    log = get_logger("startup")
    try:
        from devices import get_devices
        desired = set()
        for dev in get_devices():
            if config.get_device_config(dev, "protocol_enabled"):
                desired.add(dev)
        # Stop devices no longer wanted.
        with _device_protocol_lock:
            current = set(_device_protocol.keys())
        for dev in current - desired:
            stop_protocol_for_device(dev)
        # Start newly enabled devices.
        for dev in desired - current:
            start_protocol_for_device(dev)
    except Exception:
        log.debug("Protocol reconciliation skipped (no devices yet)", exc_info=True)


def initialize():
    """One-time app startup: logging, settings, devices, OCR warmup.

    Returns the loaded settings dict.
    """
    from botlog import setup_logging, get_logger
    setup_logging()
    config.log_adb_path()

    log = get_logger("startup")

    # Compatibility bridge: capture print() calls to legacy log file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    _log_path = os.path.join(script_dir, "9bot.log")
    _log_file = open(_log_path, "w", encoding="utf-8")

    class _Tee:
        """Write to both the original stream and a log file."""
        def __init__(self, stream, logf):
            self._stream = stream
            self._log = logf
        def write(self, data):
            self._stream.write(data)
            try:
                self._log.write(data)
                self._log.flush()
            except Exception:
                pass
        def flush(self):
            self._stream.flush()
            try:
                self._log.flush()
            except Exception:
                pass

    sys.stdout = _Tee(sys.stdout, _log_file)
    sys.stderr = _Tee(sys.stderr, _log_file)

    # License check
    cloud_mode = os.environ.get("CLOUD_MODE") == "1"
    if cloud_mode:
        # Cloud mode: validate from env var, no interactive prompt
        from license import validate_license
        validate_license()
        log.info("Cloud mode active (instance: %s)",
                 os.environ.get("NINEBOT_INSTANCE_ID", "unknown"))
    elif not os.path.isdir(os.path.join(script_dir, ".git")):
        from license import validate_license
        validate_license()
    else:
        log.info("Git repo detected — skipping license check (developer mode).")

    # Auto-update check (skipped in cloud mode — managed by VM agent)
    if not cloud_mode:
        from updater import check_and_update
        if check_and_update():
            log.info("Update installed — restarting...")
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except OSError as e:
                log.error("Failed to restart after update: %s", e)
    else:
        log.info("Cloud mode — auto-update disabled (managed by VM agent)")

    # Load and apply settings
    settings = load_settings()
    if cloud_mode:
        from server.cloud_config import apply_cloud_defaults
        settings = apply_cloud_defaults(settings)
    apply_settings(settings)

    # Connect emulators
    from devices import auto_connect_emulators
    auto_connect_emulators()

    # Reconcile protocol interceptors now that devices are connected.
    # The earlier call in apply_settings() may have found no devices yet.
    _reconcile_protocol()

    # Pre-initialize OCR engine in background thread
    from vision import warmup_ocr
    threading.Thread(target=warmup_ocr, daemon=True).start()

    log.info("9Bot initialized.")
    return settings


def shutdown():
    """Graceful shutdown: stop tasks, save stats, disconnect ADB, flush logs."""
    from botlog import get_logger

    log = get_logger("startup")
    log.info("Shutting down...")

    # Stop all running tasks
    try:
        config.MITHRIL_ENABLED_DEVICES.clear()
        config.MITHRIL_DEPLOY_TIME.clear()
        for key in list(running_tasks.keys()):
            from runners import stop_task
            stop_task(key)
        config.DEVICE_STATUS.clear()
        log.info("=== ALL TASKS STOPPED ===")
    except Exception as e:
        print(f"Failed to stop tasks: {e}")

    # Stop relay tunnel if running
    try:
        from tunnel import stop_tunnel
        stop_tunnel()
    except Exception:
        pass

    # Stop protocol interceptor
    try:
        _stop_protocol()
    except Exception:
        pass

    # Persist mithril timers so they survive restarts
    try:
        if config.LAST_MITHRIL_TIME:
            settings = load_settings()
            settings["last_mithril_time"] = dict(config.LAST_MITHRIL_TIME)
            save_settings(settings)
            log.info("Mithril timers saved (%d devices)", len(config.LAST_MITHRIL_TIME))
    except Exception as e:
        print(f"Failed to save mithril timers: {e}")

    # Save player name and power caches
    try:
        from protocol.game_state import save_player_names_if_dirty, save_player_powers_if_dirty
        save_player_names_if_dirty()
        save_player_powers_if_dirty()
        log.info("Player name/power cache saved")
    except Exception:
        pass

    # Stop chat translation worker
    try:
        import chat_translate
        chat_translate.shutdown()
    except Exception:
        pass

    # Close training data file
    try:
        import training
        training.shutdown()
    except Exception:
        pass

    # Save session stats
    try:
        from botlog import stats
        stats.save()
        log.info("Session stats saved")
        summary = stats.summary()
        if summary:
            log.info("Session stats:\n%s", summary)
    except Exception as e:
        print(f"Failed to save stats: {e}")

    # Flush all log handlers
    try:
        logging.shutdown()
    except Exception:
        pass

    # Disconnect ADB devices
    try:
        from devices import get_devices
        for d in get_devices():
            try:
                subprocess.run([config.adb_path, "disconnect", d],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2)
            except Exception:
                pass
    except Exception:
        pass


def create_bug_report_zip(clear_debug=True, notes=None):
    """Create a bug report zip file in memory and return the bytes.

    Args:
        clear_debug: If True (default), remove debug screenshots after zipping.
            Pass False for periodic auto-uploads to keep debug files intact.
        notes: Optional user notes string to include as ``notes.txt`` in the zip.

    Returns ``(zip_bytes, filename)`` tuple.
    """
    import io
    import zipfile
    from datetime import datetime
    from botlog import stats, SCRIPT_DIR, LOG_DIR, STATS_DIR, BOT_VERSION

    buf = io.BytesIO()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"9bot_bugreport_{timestamp}.zip"

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Logs (current + rotated backups)
        for suffix in ["", ".1", ".2", ".3"]:
            logfile = os.path.join(LOG_DIR, f"9bot.log{suffix}")
            if os.path.isfile(logfile):
                zf.write(logfile, f"logs/9bot.log{suffix}")

        # Debug screenshots (failures, click trails, general debug)
        debug_dir = os.path.join(SCRIPT_DIR, "debug")
        for subdir in ["failures", "clicks", ""]:
            scan_dir = os.path.join(debug_dir, subdir) if subdir else debug_dir
            zip_prefix = f"debug/{subdir}/" if subdir else "debug/"
            if os.path.isdir(scan_dir):
                for f in os.listdir(scan_dir):
                    fpath = os.path.join(scan_dir, f)
                    if os.path.isfile(fpath) and f.endswith(".png"):
                        zf.write(fpath, zip_prefix + f)

        # Training data (JSONL logs + selective images)
        training_dir = os.path.join(SCRIPT_DIR, "training_data")
        if os.path.isdir(training_dir):
            for f in os.listdir(training_dir):
                fpath = os.path.join(training_dir, f)
                if os.path.isfile(fpath) and f.endswith(".jsonl"):
                    zf.write(fpath, f"training_data/{f}")
            images_dir = os.path.join(training_dir, "images")
            if os.path.isdir(images_dir):
                for f in os.listdir(images_dir):
                    fpath = os.path.join(images_dir, f)
                    if os.path.isfile(fpath) and f.endswith(".jpg"):
                        zf.write(fpath, f"training_data/images/{f}")

        # Session stats
        if os.path.isdir(STATS_DIR):
            for f in os.listdir(STATS_DIR):
                if f.endswith(".json"):
                    zf.write(os.path.join(STATS_DIR, f), f"stats/{f}")

        # Settings (redact secrets)
        settings_path = os.path.join(SCRIPT_DIR, "settings.json")
        if os.path.isfile(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as sf:
                    safe_settings = json.load(sf)
                for key in ("relay_secret", "chat_translate_api_key"):
                    if key in safe_settings and safe_settings[key]:
                        safe_settings[key] = "***REDACTED***"
                zf.writestr("settings.json", json.dumps(safe_settings, indent=2))
            except Exception:
                zf.write(settings_path, "settings.json")

        # User notes (if provided)
        if notes and notes.strip():
            zf.writestr("notes.txt", notes.strip())

        # System info report
        try:
            from devices import get_devices
            device_list = get_devices()
        except Exception:
            device_list = ["(could not detect)"]

        cpu_cores = os.cpu_count() or "unknown"
        cpu_arch = platform.machine()
        ram_gb = _get_ram_gb()

        info_lines = [
            "9Bot Bug Report",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "=== System ===",
            f"Version: {BOT_VERSION}",
            f"Python: {sys.version}",
            f"OS: {platform.system()} {platform.release()} ({platform.version()})",
            f"CPU: {cpu_arch}, {cpu_cores} cores",
            f"RAM: {ram_gb}",
            f"ADB: {config.adb_path}",
            f"Devices: {', '.join(device_list) if device_list else '(none)'}",
            "",
            "=== Session Summary ===",
            stats.summary(),
        ]
        zf.writestr("report_info.txt", "\n".join(info_lines))

    buf.seek(0)
    zip_bytes = buf.getvalue()

    if clear_debug:
        _clear_debug_files(SCRIPT_DIR)

    return zip_bytes, filename


def _clear_debug_files(script_dir):
    """Remove debug screenshots, click trails, and training data after export."""
    for subdir in ["debug/failures", "debug/clicks", "debug"]:
        dirpath = os.path.join(script_dir, subdir)
        if not os.path.isdir(dirpath):
            continue
        for f in os.listdir(dirpath):
            fpath = os.path.join(dirpath, f)
            if os.path.isfile(fpath) and f.endswith(".png"):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
    # Clear training data (JSONL + images)
    for subdir, ext in [("training_data", ".jsonl"), ("training_data/images", ".jpg")]:
        dirpath = os.path.join(script_dir, subdir)
        if not os.path.isdir(dirpath):
            continue
        for f in os.listdir(dirpath):
            fpath = os.path.join(dirpath, f)
            if os.path.isfile(fpath) and f.endswith(ext):
                try:
                    os.remove(fpath)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Bug report auto-upload
# ---------------------------------------------------------------------------

_upload_thread = None
_upload_stop = threading.Event()
_last_upload_time = None      # datetime or None
_last_upload_error = None     # str or None
_upload_interval_hours = 24

# Manual upload progress tracking (for UI polling)
_upload_progress = {"phase": "idle", "percent": 0, "message": ""}
_manual_upload_thread = None


class _ProgressReader:
    """File-like wrapper that tracks read progress for requests uploads."""

    def __init__(self, data, callback):
        self._buf = io.BytesIO(data)
        self._total = len(data)
        self._callback = callback

    def read(self, size=-1):
        chunk = self._buf.read(size)
        if chunk:
            self._callback(self._buf.tell(), self._total)
        return chunk

    def __len__(self):
        return self._total


def upload_bug_report(settings=None, notes=None):
    """Upload a bug report ZIP to the relay server.

    Args:
        settings: Settings dict (loaded from file if None).
        notes: Optional user notes to include in the zip.

    Returns ``(success, message)`` tuple.
    """
    global _last_upload_time, _last_upload_error
    if settings is None:
        settings = load_settings()
    relay_cfg = get_relay_config(settings)
    if not relay_cfg:
        _upload_progress.update(phase="idle", percent=0, message="")
        return False, "Relay not configured (no license or remote access disabled)"

    relay_url, relay_secret, bot_name = relay_cfg
    host = relay_url.replace("wss://", "").replace("ws://", "").split("/")[0]
    upload_url = f"https://{host}/_upload?bot={bot_name}"

    _upload_progress.update(phase="zipping", percent=0, message="Generating report...")
    zip_bytes, filename = create_bug_report_zip(clear_debug=False, notes=notes)

    _upload_progress.update(phase="uploading", percent=0, message="Uploading...")

    def _on_progress(sent, total):
        pct = int(sent * 100 / total) if total else 0
        _upload_progress.update(percent=pct, message=f"Uploading... {pct}%")

    # Build multipart body manually so we can track upload progress
    boundary = "----9BotUploadBoundary"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/zip\r\n\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    body = prefix + zip_bytes + suffix

    import requests as _req
    _max_retries = 3
    resp = None
    for _attempt in range(_max_retries):
        try:
            resp = _req.post(
                upload_url,
                data=_ProgressReader(body, _on_progress),
                headers={
                    "Authorization": f"Bearer {relay_secret}",
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                },
                timeout=300,
            )
            break  # success — got a response
        except (_req.ConnectionError, _req.Timeout, IOError) as e:
            if _attempt < _max_retries - 1:
                _wait = 2 ** (_attempt + 1)
                logging.getLogger("startup").warning("Upload attempt %d failed: %s — retrying in %ds",
                             _attempt + 1, e, _wait)
                _upload_progress.update(
                    message=f"Retry {_attempt + 1}/{_max_retries} in {_wait}s...")
                time.sleep(_wait)
            else:
                _last_upload_error = str(e)
                _upload_progress.update(phase="error", percent=0, message=str(e))
                return False, f"Upload failed after {_max_retries} attempts: {e}"
        except Exception as e:
            _last_upload_error = str(e)
            _upload_progress.update(phase="error", percent=0, message=str(e))
            return False, f"Upload failed: {e}"

    if resp.status_code == 200:
        from datetime import datetime
        _last_upload_time = datetime.now()
        _last_upload_error = None
        _upload_progress.update(phase="done", percent=100, message="Uploaded!")
        return True, "Upload successful"

    _last_upload_error = f"HTTP {resp.status_code}"
    _upload_progress.update(phase="error", percent=0,
                            message=f"HTTP {resp.status_code}")
    return False, f"Upload failed: HTTP {resp.status_code}"


def start_manual_upload(notes=None):
    """Start a manual upload in a background thread. Returns immediately."""
    global _manual_upload_thread
    if _manual_upload_thread is not None and _manual_upload_thread.is_alive():
        return  # already running
    _upload_progress.update(phase="starting", percent=0, message="Starting...")

    def _run():
        try:
            upload_bug_report(notes=notes)
        except Exception as e:
            _upload_progress.update(phase="error", percent=0, message=str(e))

    _manual_upload_thread = threading.Thread(target=_run, daemon=True,
                                             name="manual-upload")
    _manual_upload_thread.start()


def get_upload_progress():
    """Return current manual upload progress dict."""
    return dict(_upload_progress)


def start_auto_upload(settings):
    """Start periodic bug report upload in a background thread."""
    global _upload_thread, _upload_interval_hours
    if _upload_thread is not None and _upload_thread.is_alive():
        return
    _upload_stop.clear()
    _upload_interval_hours = max(1, settings.get("upload_interval_hours", 24))

    def _loop():
        from botlog import get_logger
        log = get_logger("auto_upload")
        log.info("Auto-upload started (every %dh)", _upload_interval_hours)
        while not _upload_stop.is_set():
            _upload_stop.wait(_upload_interval_hours * 3600)
            if _upload_stop.is_set():
                break
            try:
                ok, msg = upload_bug_report(settings)
                if ok:
                    log.info("Auto-upload: %s", msg)
                else:
                    log.warning("Auto-upload: %s", msg)
            except Exception as e:
                log.warning("Auto-upload error: %s", e)
        log.info("Auto-upload stopped")

    _upload_thread = threading.Thread(target=_loop, daemon=True, name="auto-upload")
    _upload_thread.start()


def stop_auto_upload():
    """Stop the periodic upload thread."""
    global _upload_thread
    _upload_stop.set()
    if _upload_thread is not None:
        _upload_thread.join(timeout=2)
        _upload_thread = None


def upload_status():
    """Return dict describing auto-upload state."""
    from datetime import datetime
    enabled = (_upload_thread is not None and _upload_thread.is_alive())
    result = {
        "enabled": enabled,
        "interval_hours": _upload_interval_hours,
        "last_upload": _last_upload_time.isoformat() if _last_upload_time else None,
        "error": _last_upload_error,
    }
    if enabled and _last_upload_time:
        next_dt = _last_upload_time.timestamp() + _upload_interval_hours * 3600
        result["next_upload_in_s"] = max(0, int(next_dt - datetime.now().timestamp()))
    return result


def _get_ram_gb():
    """Get total system RAM in human-readable format. Cross-platform."""
    try:
        if platform.system() == "Windows":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            mem = MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return f"{mem.ullTotalPhys / (1024**3):.1f} GB"
        elif platform.system() == "Darwin":
            result = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                    capture_output=True, text=True, timeout=5)
            return f"{int(result.stdout.strip()) / (1024**3):.1f} GB"
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return f"{kb / (1024**2):.1f} GB"
    except Exception:
        pass
    return "unknown"
