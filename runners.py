"""Shared task runners for 9Bot.

All auto-mode loop functions live here — used by both the tkinter GUI (main.py)
and the Flask web dashboard (web/dashboard.py). This eliminates the previous
duplication where both files had their own copies of every runner function.

Key exports:
    sleep_interval        — Interruptible sleep with ± variation
    run_auto_quest        — Main farming loop (quests + troops + mithril)
    run_auto_titan        — Rally Titan loop with EG reset
    run_auto_groot        — Join Groot Rally loop
    run_auto_pass         — Pass battle (rally/reinforce/join war)
    run_auto_occupy       — Territory auto-occupy wrapper
    run_auto_reinforce    — Reinforce Throne loop
    run_auto_reinforce_ally — Reinforce ally castles from protocol viewport data
    run_auto_mithril      — Standalone mithril mining loop
    run_auto_gold         — Gold gathering loop
    run_repeat            — Generic repeating task wrapper
    run_once              — Generic one-shot task wrapper
    launch_task           — Spawn a daemon thread for a task
    stop_task             — Signal a task to stop + set "Stopping ..." status
    force_stop_all        — Force-kill all task threads immediately
    stop_all_tasks_matching — Stop all tasks with a given suffix
"""

import ctypes
import math
import os
import threading
import time
import random

import config
from config import running_tasks, Screen, RallyType
from botlog import get_logger
from navigation import check_screen, navigate
from vision import (adb_tap, load_screenshot, find_image, tap_image,
                    wait_for_image_and_tap)
from troops import troops_avail, heal_all, read_panel_statuses, get_troop_status, TroopAction
from actions import (attack, phantom_clash_attack, reinforce_throne, target,
                     check_quests, rally_titan, search_eg_reset, join_rally,
                     join_war_rallies, reset_quest_tracking, reset_rally_blacklist,
                     mine_mithril_if_due, gather_gold_loop,
                     reinforce_ally_castle, capture_home_coords)
from territory import auto_occupy_loop


# ============================================================
# UTILITIES
# ============================================================

def sleep_interval(base, variation, stop_check):
    """Sleep for base ± variation seconds, checking stop_check each second."""
    actual = base + random.randint(-variation, variation) if variation > 0 else base
    actual = max(1, actual)
    if variation > 0:
        get_logger("runner").debug("Waiting %ss (base %s +/-%s)", actual, base, variation)
    for _ in range(actual):
        if stop_check():
            break
        time.sleep(1)


def _deployed_status(device):
    """Build a status string from deployed troop actions (e.g. 'Gathering/Defending...')."""
    snapshot = get_troop_status(device)
    if not snapshot:
        return "Waiting for Troops..."
    actions = set()
    for t in snapshot.troops:
        if t.action != TroopAction.HOME:
            actions.add(t.action.value)
    if not actions:
        return "Waiting for Troops..."
    # Title Case, joined by /
    return "/".join(sorted(actions)) + "..."


# Track last check_quests time per device for periodic re-checks
_last_quest_check = {}   # {device: timestamp}
_QUEST_CHECK_INTERVAL = 300  # seconds (5 minutes when all troops deployed)


def _smart_wait_for_troops(device, stop_check, dlog, max_wait=120):
    """Check troop statuses and wait if one is close to finishing (< max_wait seconds).
    Returns True if a troop became available, False if timed out or stopped."""
    snapshot = read_panel_statuses(device)
    if snapshot is None:
        return False
    soonest = snapshot.soonest_free()
    if soonest is None or soonest.time_left is None:
        return False
    wait_secs = soonest.time_left
    if wait_secs > max_wait:
        dlog.debug("Soonest troop free in %ds — too long, skipping wait", wait_secs)
        return False
    dlog.info("Troop %s finishes in %ds — waiting", soonest.action.value, wait_secs)
    for _ in range(wait_secs + 5):  # Small buffer
        if stop_check():
            return False
        time.sleep(1)
    return True


# ============================================================
# AUTO-MODE RUNNERS
# ============================================================

def run_auto_quest(device, stop_event):
    dlog = get_logger("runner", device)
    dlog.info("Auto Quest started")
    reset_quest_tracking(device)
    reset_rally_blacklist(device)
    _last_quest_check.pop(device, None)  # Force quest check on first iteration
    stop_check = stop_event.is_set
    lock = config.get_device_lock(device)
    try:
        while not stop_check():
            with lock:
                mine_mithril_if_due(device, stop_check=stop_check)
                if stop_check():
                    break
                # Ensure we're on map_screen before checking troops
                # (troop pixel detection only works on map_screen)
                if not navigate(Screen.MAP, device):
                    dlog.warning("Cannot reach map screen — retrying in 10s")
                    config.set_device_status(device, "Navigating...")
                    for _ in range(10):
                        if stop_check():
                            break
                        time.sleep(1)
                    continue
                read_panel_statuses(device)
                troops = troops_avail(device)
                if troops > config.get_device_config(device, "min_troops"):
                    config.set_device_status(device, "Checking Quests...")
                    check_quests(device, stop_check=stop_check)
                    _last_quest_check[device] = time.time()
                else:
                    # Still run check_quests periodically to keep
                    # dashboard quest tracking up to date
                    since_check = time.time() - _last_quest_check.get(device, 0)
                    if since_check >= _QUEST_CHECK_INTERVAL:
                        config.set_device_status(device, "Checking Quests...")
                        check_quests(device, stop_check=stop_check)
                        _last_quest_check[device] = time.time()
                    else:
                        config.set_device_status(device, _deployed_status(device))
                    if _smart_wait_for_troops(device, stop_check, dlog):
                        continue  # Troop freed up — retry immediately
            if stop_check():
                break
            # Show deployed status if troops are low, otherwise "Idle"
            troops = troops_avail(device) if check_screen(device) == Screen.MAP else 0
            if troops <= config.get_device_config(device, "min_troops"):
                config.set_device_status(device, _deployed_status(device))
            else:
                config.set_device_status(device, "Idle")
            for _ in range(10):
                if stop_check():
                    break
                time.sleep(1)
    except Exception as e:
        dlog.error("ERROR in Auto Quest: %s", e, exc_info=True)
    config.clear_device_status(device)
    dlog.info("Auto Quest stopped")


def run_auto_titan(device, stop_event, interval, variation):
    """Loop rally_titan on a configurable interval.
    Every 5 rallies, searches for an Evil Guard to reset titan distances."""
    dlog = get_logger("runner", device)
    dlog.info("Rally Titan started (interval: %ss +/-%ss)", interval, variation)
    stop_check = stop_event.is_set
    lock = config.get_device_lock(device)
    rally_count = 0
    try:
        while not stop_check():
            with lock:
                mine_mithril_if_due(device, stop_check=stop_check)
                if stop_check():
                    break
                if config.get_device_config(device, "auto_heal"):
                    heal_all(device)
                if not navigate(Screen.MAP, device):
                    dlog.warning("Cannot reach map screen — retrying")
                    config.set_device_status(device, "Navigating...")
                    for _ in range(10):
                        if stop_check():
                            break
                        time.sleep(1)
                    continue
                troops = troops_avail(device)
                if troops > config.get_device_config(device, "min_troops"):
                    # Reset titan distance every 5 rallies by searching for EG
                    if rally_count > 0 and rally_count % 5 == 0:
                        search_eg_reset(device)
                        if stop_check():
                            break
                    config.set_device_status(device, "Rallying Titan...")
                    rally_titan(device)
                    rally_count += 1
                else:
                    dlog.warning("Not enough troops for Rally Titan")
                    config.set_device_status(device, "Waiting for Troops...")
                    if _smart_wait_for_troops(device, stop_check, dlog):
                        continue  # Troop freed up — retry immediately
            if stop_check():
                break
            config.set_device_status(device, "Idle")
            sleep_interval(interval, variation, stop_check)
    except Exception as e:
        dlog.error("ERROR in Rally Titan: %s", e, exc_info=True)
    config.clear_device_status(device)
    dlog.info("Rally Titan stopped")


def run_auto_groot(device, stop_event, interval, variation):
    """Loop join_rally('groot') on a configurable interval."""
    dlog = get_logger("runner", device)
    dlog.info("Rally Groot started (interval: %ss +/-%ss)", interval, variation)
    stop_check = stop_event.is_set
    lock = config.get_device_lock(device)
    try:
        while not stop_check():
            with lock:
                mine_mithril_if_due(device, stop_check=stop_check)
                if stop_check():
                    break
                if config.get_device_config(device, "auto_heal"):
                    heal_all(device)
                if not navigate(Screen.MAP, device):
                    dlog.warning("Cannot reach map screen — retrying")
                    config.set_device_status(device, "Navigating...")
                    for _ in range(10):
                        if stop_check():
                            break
                        time.sleep(1)
                    continue
                troops = troops_avail(device)
                if troops > config.get_device_config(device, "min_troops"):
                    config.set_device_status(device, "Joining Groot Rally...")
                    join_rally(RallyType.GROOT, device)
                else:
                    dlog.warning("Not enough troops for Rally Groot")
                    config.set_device_status(device, "Waiting for Troops...")
                    if _smart_wait_for_troops(device, stop_check, dlog):
                        continue  # Troop freed up — retry immediately
            if stop_check():
                break
            config.set_device_status(device, "Idle")
            sleep_interval(interval, variation, stop_check)
    except Exception as e:
        dlog.error("ERROR in Rally Groot: %s", e, exc_info=True)
    config.clear_device_status(device)
    dlog.info("Rally Groot stopped")


def run_auto_esb(device, stop_event, interval, variation):
    """Loop phantom_clash_attack on a configurable interval."""
    dlog = get_logger("runner", device)
    dlog.info("Phantom Clash started (interval: %ss +/-%ss)", interval, variation)
    stop_check = stop_event.is_set
    lock = config.get_device_lock(device)
    try:
        while not stop_check():
            with lock:
                config.set_device_status(device, "Phantom Clash...")
                phantom_clash_attack(device, stop_check=stop_check)
            if stop_check():
                break
            config.set_device_status(device, "Idle")
            sleep_interval(interval, variation, stop_check)
    except Exception as e:
        dlog.error("ERROR in Phantom Clash: %s", e, exc_info=True)
    config.clear_device_status(device)
    dlog.info("Phantom Clash stopped")


def run_auto_pass(device, stop_event, pass_mode, pass_interval, variation):
    dlog = get_logger("runner", device)
    stop_check = stop_event.is_set

    def _pass_attack(device):
        if config.get_device_config(device, "auto_heal"):
            heal_all(device)
        troops = troops_avail(device)
        if troops <= config.get_device_config(device, "min_troops"):
            dlog.warning("Not enough troops for pass battle")
            return False

        adb_tap(device, 560, 675)
        time.sleep(1)

        start_time = time.time()
        while time.time() - start_time < 10:
            screen = load_screenshot(device)
            if screen is None:
                time.sleep(0.5)
                continue

            if find_image(screen, "reinforce_button.png", threshold=0.5):
                dlog.info("Found reinforce button - reinforcing")
                tap_image("reinforce_button.png", device, threshold=0.5)
                time.sleep(1)
                tap_image("depart.png", device)
                return "reinforce"

            if find_image(screen, "attack_button.png", threshold=0.7):
                if pass_mode == "Rally Starter":
                    dlog.info("Found attack button - starting rally")
                    tap_image("rally_button.png", device, threshold=0.7)
                    time.sleep(1)
                    if not tap_image("depart.png", device):
                        wait_for_image_and_tap("depart.png", device, timeout=5)
                    return "rally_started"
                else:
                    dlog.info("Found attack button - enemy owns it, closing menu")
                    adb_tap(device, 560, 675)
                    time.sleep(0.5)
                    return "attack"

            time.sleep(0.5)

        dlog.warning("Neither reinforce nor attack button found, closing menu")
        adb_tap(device, 560, 675)
        time.sleep(0.5)
        return False

    dlog.info("Auto Pass Battle started (mode: %s)", pass_mode)
    lock = config.get_device_lock(device)
    try:
        while not stop_check():
            with lock:
                mine_mithril_if_due(device, stop_check=stop_check)
                if stop_check():
                    break
                config.set_device_status(device, "Pass Battle...")
                result = target(device)
                if result == "no_marker":
                    dlog.warning("*** TARGET NOT SET! ***")
                    dlog.warning("Please mark the pass or tower with a Personal 'Enemy' marker.")
                    dlog.warning("Auto Pass Battle stopping.")
                    config.alert_queue.put("no_marker")
                    break
                if result == "duplicate_markers":
                    dlog.warning("*** MULTIPLE ENEMY MARKERS SET! ***")
                    dlog.warning("Remove duplicate markers and keep only one.")
                    dlog.warning("Auto Pass Battle stopping.")
                    config.alert_queue.put("duplicate_markers")
                    break
                if stop_check():
                    break
                if not result:
                    break

                action = _pass_attack(device)
            if stop_check():
                break

            if action == "rally_started":
                dlog.info("Rally started - looping back")
                time.sleep(2)
            elif action == "attack":
                dlog.info("Enemy owns pass - joining war rallies continuously")
                config.set_device_status(device, "Joining War Rallies...")
                while not stop_check():
                    with lock:
                        troops = troops_avail(device)
                        if troops <= config.get_device_config(device, "min_troops"):
                            dlog.warning("Not enough troops, waiting...")
                            time.sleep(5)
                            continue
                        join_war_rallies(device)
                    if stop_check():
                        break
                    time.sleep(2)
            elif action == "reinforce":
                sleep_interval(pass_interval, variation, stop_check)
            else:
                sleep_interval(10, variation, stop_check)
    except Exception as e:
        dlog.error("ERROR in Auto Pass Battle: %s", e, exc_info=True)
    config.clear_device_status(device)
    dlog.info("Auto Pass Battle stopped")


def run_auto_occupy(device, stop_event):
    config.set_device_status(device, "Occupying Towers...")
    auto_occupy_loop(device, stop_check=stop_event.is_set)
    config.clear_device_status(device)
    get_logger("runner", device).info("Auto Occupy stopped")


def run_debug_occupy(device, stop_event):
    config.set_device_status(device, "Debug Occupy...")
    auto_occupy_loop(device, stop_check=stop_event.is_set, skip_troop_gate=True)
    config.clear_device_status(device)
    get_logger("runner", device).info("Debug Occupy stopped")


def run_auto_reinforce(device, stop_event, interval, variation):
    """Loop reinforce_throne on a configurable interval."""
    dlog = get_logger("runner", device)
    dlog.info("Auto Reinforce Throne started (interval: %ss +/-%ss)", interval, variation)
    stop_check = stop_event.is_set
    lock = config.get_device_lock(device)
    try:
        while not stop_check():
            with lock:
                mine_mithril_if_due(device, stop_check=stop_check)
                if stop_check():
                    break
                config.set_device_status(device, "Reinforcing Throne...")
                reinforce_throne(device)
            if stop_check():
                break
            config.set_device_status(device, "Idle")
            sleep_interval(interval, variation, stop_check)
    except Exception as e:
        dlog.error("ERROR in Auto Reinforce Throne: %s", e, exc_info=True)
    config.clear_device_status(device)
    dlog.info("Auto Reinforce Throne stopped")


_ALLY_REINFORCE_COOLDOWN_S = 1800  # 30 minutes per entity ID + position


_REINFORCE_STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "reinforce_stats.json")


def _log_reinforce_stat(device, name, power, dist, success):
    """Append a reinforce attempt record to data/reinforce_stats.json."""
    import json as _json
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "device": device,
        "name": name,
        "power": power,
        "dist": round(dist, 1) if dist is not None else None,
        "success": success,
    }
    try:
        os.makedirs(os.path.dirname(_REINFORCE_STATS_FILE), exist_ok=True)
        try:
            with open(_REINFORCE_STATS_FILE, "r") as f:
                stats = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            stats = []
        stats.append(record)
        with open(_REINFORCE_STATS_FILE, "w") as f:
            _json.dump(stats, f, indent=2)
    except Exception as e:
        get_logger("runner", device).debug("Failed to save reinforce stat: %s", e)


def _save_home_coords(device, x, z):
    """Persist captured home coords to settings as device overrides."""
    from settings import load_settings, save_settings
    from config import validate_settings, set_device_overrides
    from settings import DEFAULTS
    settings = load_settings()
    ds = settings.setdefault("device_settings", {})
    dev = ds.setdefault(device, {})
    dev["home_x"] = x
    dev["home_z"] = z
    settings["device_settings"] = ds
    settings, _ = validate_settings(settings, DEFAULTS)
    save_settings(settings)
    # Update in-memory device config immediately.
    existing = config._DEVICE_CONFIG.get(device, {})
    existing["home_x"] = x
    existing["home_z"] = z
    set_device_overrides(device, existing)


def run_auto_reinforce_ally(device, stop_event):
    """Reinforce ally PLAYER_CITY castles the moment they appear in the viewport.

    Subscribes to EVT_ALLY_CITY_SPOTTED on the device's EventBus. Each new
    ally city entity is queued immediately by the event handler (protocol thread)
    and processed by this runner thread — zero polling delay.

    Re-reinforcement is suppressed for _ALLY_REINFORCE_COOLDOWN_S per entity ID.
    When an entity leaves the viewport (UnionDelEntitiesNtf removes it from
    GameState), it can be re-queued and reinforced again on next appearance.

    Home coordinates are captured once at startup (if not already set) by
    navigating away and back — centering the camera on the home castle — then
    OCR-reading the coordinate banner. Re-capture via the dashboard after a
    castle teleport.

    Requires protocol to be enabled on this device.
    """
    import queue as _queue
    dlog = get_logger("runner", device)
    dlog.info("Auto Reinforce Ally started")
    stop_check = stop_event.is_set
    lock = config.get_device_lock(device)
    reinforced = {}  # entity_id -> (timestamp, x, z) of last successful reinforce
    active_coords = set()  # (x_disp, z_disp) tuples where we currently have a troop
    _TROOP_RESERVE = 1  # keep 1 troop free for other tasks
    pending = _queue.PriorityQueue()  # (-power, arrival_time, entity)

    try:
        from startup import get_protocol_event_bus, set_protocol_ally_monitoring
        from protocol.events import EVT_ALLY_CITY_SPOTTED
    except ImportError:
        dlog.error("Protocol not available — Auto Reinforce Ally requires protocol enabled")
        config.clear_device_status(device)
        return

    bus = get_protocol_event_bus(device)
    if bus is None:
        dlog.error("No EventBus for %s — is protocol enabled?", device)
        config.set_device_status(device, "Error: Enable protocol first")
        return

    # Always capture home coordinates on start.
    config.set_device_status(device, "Capturing Home Coordinates...")
    with lock:
        coords = capture_home_coords(device, stop_check)
    if coords:
        home_x, home_z = coords
        _save_home_coords(device, home_x, home_z)
        dlog.info("Home coordinates set: X=%d Y=%d", home_x, home_z)
    else:
        dlog.warning("Failed to capture home coordinates — using last saved or disabling filter")
        home_x = config.get_device_config(device, "home_x")
        home_z = config.get_device_config(device, "home_z")

    if stop_check():
        config.clear_device_status(device)
        return

    def _on_spotted(entity):
        power = entity.get("_power", 0)
        pending.put((-power, time.monotonic(), entity))

    set_protocol_ally_monitoring(device, True)
    bus.on(EVT_ALLY_CITY_SPOTTED, _on_spotted)
    dlog.info("Ally monitoring enabled (home X=%d Y=%d)", home_x, home_z)
    config.set_device_status(device, "Watching for Allies...")
    _HEAL_INTERVAL = 10  # seconds between idle heal checks
    _last_idle_heal = 0.0

    try:
        while not stop_check():
            try:
                _neg_power, _arrival, entity = pending.get(timeout=1.0)
            except _queue.Empty:
                now = time.monotonic()
                if now - _last_idle_heal >= _HEAL_INTERVAL:
                    _last_idle_heal = now
                    with lock:
                        heal_all(device)
                continue

            if stop_check():
                break

            # EntityInfo: field_1=ID, field_3=owner (OwnerInfo with named keys)
            eid = entity.get("field_1") or entity.get("id") or entity.get("ID")
            if eid is None:
                continue
            now = time.monotonic()
            last_t, last_x, last_z = reinforced.get(eid, (0, None, None))
            x = entity.get("X", 0)
            z = entity.get("Z", 0)
            if not x and not z:
                dlog.debug("Ally %s has no coordinates (0,0) — skipping", eid)
                continue
            if x // 1000 == 0 and z // 1000 == 0:
                dlog.debug("Ally %s coords (%s,%s) too small for display — skipping", eid, x, z)
                continue
            same_pos = (last_x == x and last_z == z)
            if same_pos and now - last_t < _ALLY_REINFORCE_COOLDOWN_S:
                dlog.debug("Ally %s on cooldown at same position, skipping", eid)
                continue

            owner = entity.get("field_3") or entity.get("owner") or {}
            name = owner.get("name", "") if isinstance(owner, dict) else getattr(owner, "name", "")
            pid = owner.get("ID", 0) if isinstance(owner, dict) else 0
            from protocol.game_state import lookup_player_power
            power = lookup_player_power(pid)

            # Distance filter — entity coords are raw (1000x display units).
            max_dist = config.get_device_config(device, "max_reinforce_distance")
            dist = None
            if home_x and home_z and x and z:
                dist = math.sqrt((x / 1000 - home_x) ** 2 + (z / 1000 - home_z) ** 2)
                # Skip own castle (distance ≈ 0 from home).
                if dist < 2:
                    dlog.debug("Skipping own castle %s (dist=%.1f)", name or eid, dist)
                    continue
                if max_dist and dist > max_dist:
                    dlog.info("Ally %s at dist %.1f > max %d — skipping", name or eid, dist, max_dist)
                    continue

            # Troop reserve: keep 1 free for other tasks.
            home_troops = troops_avail(device)
            if home_troops <= _TROOP_RESERVE:
                dlog.debug("Ally %s: only %d troops home (reserve %d) — skipping",
                           name or eid, home_troops, _TROOP_RESERVE)
                continue

            # Coordinate dedup: don't send another troop to the same location.
            display_coord = (x // 1000, z // 1000)
            if display_coord in active_coords:
                dlog.debug("Already have troop at (%d, %d) — skipping %s",
                           display_coord[0], display_coord[1], name or eid)
                continue

            dist_str = f" dist={dist:.1f}" if dist is not None else ""
            dlog.info("Ally city spotted: %s (power=%s) at (%s, %s)%s — reinforcing",
                      name or eid, power, x, z, dist_str)
            # Always heal before sending troops out.
            with lock:
                heal_all(device)
            if stop_check():
                break
            config.set_device_status(device, f"Reinforcing {name}..." if name else "Reinforcing Ally...")
            with lock:
                success = reinforce_ally_castle(device, x, z, name, stop_check)
            if success:
                reinforced[eid] = (time.monotonic(), x, z)
                active_coords.add(display_coord)
            _log_reinforce_stat(device, name, power, dist, success)
            config.set_device_status(device, "Watching for Allies...")
    except Exception as e:
        dlog.error("ERROR in Auto Reinforce Ally: %s", e, exc_info=True)
    finally:
        bus.off(EVT_ALLY_CITY_SPOTTED, _on_spotted)
        set_protocol_ally_monitoring(device, False)

    config.clear_device_status(device)
    dlog.info("Auto Reinforce Ally stopped")


def run_auto_mithril(device, stop_event):
    """Standalone mithril mining loop — checks every 60s if mining is due.
    Also useful as fallback when no other auto tasks are running."""
    dlog = get_logger("runner", device)
    dlog.info("Auto Mithril started (interval: %d min)", config.get_device_config(device, "mithril_interval"))
    stop_check = stop_event.is_set
    lock = config.get_device_lock(device)
    try:
        while not stop_check():
            with lock:
                config.set_device_status(device, "Mining Mithril...")
                mine_mithril_if_due(device, stop_check=stop_check)
            if stop_check():
                break
            config.set_device_status(device, "Idle")
            sleep_interval(60, 0, stop_check)  # Check every 60s
    except Exception as e:
        dlog.error("ERROR in Auto Mithril: %s", e, exc_info=True)
    config.clear_device_status(device)
    dlog.info("Auto Mithril stopped")


def run_auto_gold(device, stop_event):
    """Standalone gold gathering loop — deploys troops to gold mines every 60s."""
    dlog = get_logger("runner", device)
    dlog.info("Auto Gold started")
    stop_check = stop_event.is_set
    lock = config.get_device_lock(device)
    try:
        while not stop_check():
            with lock:
                mine_mithril_if_due(device, stop_check=stop_check)
                if stop_check():
                    break
                config.set_device_status(device, "Gathering Gold...")
                if navigate(Screen.MAP, device):
                    gather_gold_loop(device, stop_check=stop_check)
            if stop_check():
                break
            config.set_device_status(device, "Idle")
            sleep_interval(60, 0, stop_check)
    except Exception as e:
        dlog.error("ERROR in Auto Gold: %s", e, exc_info=True)
    config.clear_device_status(device)
    dlog.info("Auto Gold stopped")


# ============================================================
# GENERIC TASK WRAPPERS
# ============================================================

def run_repeat(device, task_name, function, interval, variation, stop_event):
    dlog = get_logger("runner", device)
    stop_check = stop_event.is_set
    lock = config.get_device_lock(device)
    dlog.info("Starting repeating task: %s", task_name)
    try:
        while not stop_check():
            dlog.info("Running %s...", task_name)
            config.set_device_status(device, f"{task_name}...")
            with lock:
                function(device)
            dlog.debug("%s completed, waiting %ss...", task_name, interval)
            config.set_device_status(device, "Idle")
            sleep_interval(interval, variation, stop_check)
    except Exception as e:
        dlog.error("ERROR in %s: %s", task_name, e, exc_info=True)
    config.clear_device_status(device)
    dlog.info("%s stopped", task_name)


def run_once(device, task_name, function):
    dlog = get_logger("runner", device)
    lock = config.get_device_lock(device)
    dlog.info("Running %s...", task_name)
    config.set_device_status(device, f"{task_name}...")
    try:
        with lock:
            function(device)
        dlog.info("%s completed", task_name)
    except Exception as e:
        dlog.error("ERROR in %s: %s", task_name, e, exc_info=True)
    config.clear_device_status(device)


# ============================================================
# TASK LAUNCHER
# ============================================================

def launch_task(device, task_name, target_func, stop_event, args=()):
    """Launch a task as a daemon thread."""
    thread = threading.Thread(target=target_func, args=args, daemon=True)
    thread.start()

    task_key = f"{device}_{task_name}"
    running_tasks[task_key] = {"thread": thread, "stop_event": stop_event}
    get_logger("runner", device).info("Started %s", task_name)


# Human-readable labels for auto-mode keys (used in "Stopping ..." status)
_MODE_LABELS = {
    "auto_quest":     "Auto Quest",
    "auto_titan":     "Rally Titans",
    "auto_groot":     "Join Groot",
    "auto_pass":      "Pass Battle",
    "auto_occupy":    "Occupy Towers",
    "auto_reinforce":      "Reinforce Throne",
    "auto_reinforce_ally": "Reinforce Ally",
    "auto_mithril":        "Mine Mithril",
    "auto_gold":      "Gather Gold",
    "auto_esb":       "Phantom Clash",
    "debug_occupy":   "Debug Occupy",
}


def stop_task(task_key):
    """Signal a task to stop via its threading.Event and set Stopping status."""
    if task_key in running_tasks:
        info = running_tasks[task_key]
        if isinstance(info, dict) and "stop_event" in info:
            info["stop_event"].set()
            get_logger("runner").debug("Stop signal sent for %s", task_key)
        # Show "Stopping ..." in the device status
        parts = task_key.split("_", 1)
        if len(parts) == 2:
            device, mode_key = parts
            label = _MODE_LABELS.get(mode_key, mode_key)
            config.set_device_status(device, f"Stopping {label}...")


def _force_kill_thread(thread):
    """Force-kill a thread by injecting SystemExit at the next bytecode."""
    if not thread.is_alive():
        return
    tid = thread.ident
    if tid is None:
        return
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(tid), ctypes.py_object(SystemExit))
    if res > 1:
        # Revert — something went wrong
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(tid), None)


def force_stop_all():
    """Force-kill every running task thread immediately."""
    _log = get_logger("runner")
    config.MITHRIL_ENABLED_DEVICES.clear()
    config.MITHRIL_DEPLOY_TIME.clear()
    for key in list(running_tasks.keys()):
        info = running_tasks.get(key)
        if not isinstance(info, dict):
            continue
        # Set stop event first (cooperative)
        stop_ev = info.get("stop_event")
        if stop_ev:
            stop_ev.set()
        # Force-kill the thread
        thread = info.get("thread")
        if thread:
            _force_kill_thread(thread)
    # Give threads a moment to actually die, then clean up
    time.sleep(0.1)
    running_tasks.clear()
    config.DEVICE_STATUS.clear()
    _log.info("=== ALL TASKS FORCE-KILLED ===")


def stop_all_tasks_matching(suffix):
    """Stop all tasks whose task_key ends with the given suffix."""
    for key in list(running_tasks.keys()):
        if key.endswith(suffix):
            stop_task(key)
