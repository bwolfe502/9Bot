"""Basic combat actions: attack, phantom clash, reinforce, target, teleport.

Dependencies: _helpers (for _interruptible_sleep)

Key exports:
    attack              — basic attack sequence
    phantom_clash_attack — Phantom Clash mode attack
    reinforce_throne    — reinforce the throne
    target              — target menu sequence
    teleport            — teleport with guided + random fallback
    _detect_player_at_eg — player detection near EG positions
"""

import cv2
import numpy as np
import time
import random

import config
from config import Screen
from botlog import get_logger, timed_action
from vision import (tap_image, wait_for_image_and_tap, timed_wait,
                    load_screenshot, find_image, find_all_matches,
                    get_template, get_last_best, adb_tap, adb_swipe,
                    logged_tap, clear_click_trail, save_failure_screenshot)
from navigation import navigate, check_screen
from troops import troops_avail, all_troops_home, heal_all

from actions._helpers import _interruptible_sleep

_log = get_logger("actions")


@timed_action("attack")
def attack(device):
    """Heal all troops first (if auto heal enabled), then check troops and attack"""
    log = get_logger("actions", device)
    clear_click_trail()
    if config.get_device_config(device, "auto_heal"):
        heal_all(device)

    if check_screen(device) != Screen.MAP:
        log.debug("Not on map_screen, navigating...")
        if not navigate(Screen.MAP, device):
            log.warning("Failed to navigate to map screen")
            return

    troops = troops_avail(device)
    min_troops = config.get_device_config(device, "min_troops")

    if troops > min_troops:
        logged_tap(device, 560, 675, "attack_selection")
        wait_for_image_and_tap("attack_button.png", device, timeout=5)
        time.sleep(1)  # Wait for attack dialog
        if tap_image("depart.png", device):
            log.info("Attack departed with %d troops available", troops)
        else:
            log.warning("Depart button not found after attack sequence")
    else:
        log.warning("Not enough troops available (have %d, need more than %d)", troops, min_troops)

@timed_action("phantom_clash_attack")
def phantom_clash_attack(device, stop_check=None):
    """Heal all troops first (if auto heal enabled), then attack in Phantom Clash mode"""
    log = get_logger("actions", device)
    clear_click_trail()
    if config.get_device_config(device, "auto_heal"):
        heal_all(device)

    if stop_check and stop_check():
        return

    # Determine if we need to attack based on troop statuses
    screen = load_screenshot(device)
    total = config.DEVICE_TOTAL_TROOPS.get(device, 5)
    deployed = total - troops_avail(device)
    if deployed >= total:
        # All troops out — check if any can attack (stationing or home)
        has_stationing = find_image(screen, "statuses/stationing.png") is not None
        has_battling = find_image(screen, "statuses/battling.png") is not None
        has_marching = find_image(screen, "statuses/marching.png") is not None
        has_returning = find_image(screen, "statuses/returning.png") is not None
        if not has_stationing:
            log.info("All %d troops deployed and none stationing — skipping attack", total)
            return
        log.info("All troops deployed but stationing troop found — proceeding to attack")
    else:
        log.info("%d/%d troops deployed — proceeding to attack", deployed, total)

    # Check for returning troops and drag to recall (skip if attack window already open)
    if not find_image(screen, "esb_middle_attack_window.png"):
        match = find_image(screen, "statuses/returning.png")
        if match:
            _, (mx, my), h, w = match
            cx, cy = mx + w // 2, my + h // 2
            log.info("Found returning troops at (%d, %d), dragging to (560, 1200)", cx, cy)
            adb_swipe(device, cx, cy, 560, 1200, duration_ms=500)
            if _interruptible_sleep(1, stop_check):
                return

    logged_tap(device, 550, 450, "phantom_clash_attack_selection")

    # Phase 1: Wait for the attack menu to open (esb_middle_attack_window.png)
    # Retap king periodically until the menu appears, but stop retapping once confirmed.
    start = time.time()
    menu_open = False
    attack_tapped = False
    while time.time() - start < 15:
        if stop_check and stop_check():
            return
        screen = load_screenshot(device)
        if find_image(screen, "esb_middle_attack_window.png"):
            log.debug("Attack menu open, moving to attack button poll")
            menu_open = True
            break
        match = find_image(screen, "esb_attack.png")
        if match:
            _, (mx, my), h, w = match
            adb_tap(device, mx + w // 2, my + h // 2)
            log.debug("Tapped esb_attack.png during menu open phase (%.0f%%)",
                      match[0] * 100)
            attack_tapped = True
            break
        # Menu not open yet — retap king every ~3s
        log.debug("Attack menu not detected (best: %.0f%%), retapping king",
                  get_last_best() * 100)
        logged_tap(device, 550, 450, "phantom_clash_attack_selection")
        if _interruptible_sleep(3, stop_check):
            return

    if not menu_open and not attack_tapped:
        log.warning("Timed out waiting for attack menu after %.0fs", time.time() - start)
        return

    # Phase 2: Menu is open — poll for attack button (never retap king)
    if not attack_tapped:
        poll_start = time.time()
        while time.time() - poll_start < 30:
            if stop_check and stop_check():
                return
            screen = load_screenshot(device)
            match = find_image(screen, "esb_attack.png")
            if match:
                _, (mx, my), h, w = match
                adb_tap(device, mx + w // 2, my + h // 2)
                log.debug("Tapped esb_attack.png (%.0f%%) after %.0fs poll",
                          match[0] * 100, time.time() - poll_start)
                attack_tapped = True
                break
            log.debug("Waiting for esb_attack.png (best: %.0f%%)",
                      get_last_best() * 100)
            if _interruptible_sleep(0.5, stop_check):
                return
        if not attack_tapped:
            log.warning("Timed out waiting for esb_attack.png after %.0fs with menu open",
                        time.time() - poll_start)
            save_failure_screenshot(device, "esb_attack_timeout")
            return

    # Wait for depart button — may take 2-3s to appear after attack tap
    if wait_for_image_and_tap("depart.png", device, timeout=5, threshold=0.7):
        log.info("Phantom Clash attack departed")
    else:
        log.warning("Depart button not found after Phantom Clash attack (best: %.0f%%)",
                    get_last_best() * 100)
        save_failure_screenshot(device, "esb_depart_miss")

@timed_action("reinforce_throne")
def reinforce_throne(device):
    """Heal all troops first (if auto heal enabled), then check troops and reinforce throne"""
    log = get_logger("actions", device)
    clear_click_trail()
    if config.get_device_config(device, "auto_heal"):
        heal_all(device)

    if check_screen(device) != Screen.MAP:
        log.debug("Not on map_screen, navigating...")
        if not navigate(Screen.MAP, device):
            log.warning("Failed to navigate to map screen")
            return

    troops = troops_avail(device)
    min_troops = config.get_device_config(device, "min_troops")

    if troops > min_troops:
        logged_tap(device, 560, 675, "throne_selection")
        wait_for_image_and_tap("throne_reinforce.png", device, timeout=5)
        time.sleep(1)
        tap_image("depart.png", device)
    else:
        log.warning("Not enough troops available (have %d, need more than %d)", troops, min_troops)

@timed_action("reinforce_target")
def reinforce_target(device):
    """Tap the target (pass/tower) after navigation and reinforce or detect enemy ownership.

    Assumes the camera is already centered on the target (called after target()).
    Returns 'reinforce' if we owned and departed, 'attack' if enemy owns it, False on failure.

    Handles the stacked-entity picker: when multiple entities overlap the tap
    point, the game shows a vertical list instead of the action menu. If neither
    reinforce nor attack is found within 3s, we save a debug screenshot, dismiss
    the picker by tapping off-center, and re-tap the target.
    """
    log = get_logger("actions", device)

    if config.get_device_config(device, "auto_heal"):
        heal_all(device)
    troops = troops_avail(device)
    if troops <= config.get_device_config(device, "min_troops"):
        log.warning("Not enough troops for reinforce target")
        return False

    _MAX_RETAPS = 3
    for attempt in range(_MAX_RETAPS):
        adb_tap(device, 560, 675)
        time.sleep(1)

        start_time = time.time()
        timeout = 3 if attempt < _MAX_RETAPS - 1 else 10  # short poll, long on last try
        while time.time() - start_time < timeout:
            screen = load_screenshot(device)
            if screen is None:
                time.sleep(0.5)
                continue

            if find_image(screen, "reinforce_button.png", threshold=0.5):
                log.info("Found reinforce button — reinforcing target")
                tap_image("reinforce_button.png", device, threshold=0.5)
                time.sleep(1)
                tap_image("depart.png", device)
                return "reinforce"

            if find_image(screen, "attack_button.png", threshold=0.7):
                log.info("Found attack button — enemy owns target, closing menu")
                adb_tap(device, 560, 675)
                time.sleep(0.5)
                return "attack"

            time.sleep(0.5)

        if attempt < _MAX_RETAPS - 1:
            # Likely stacked-entity picker — save screenshot for diagnosis, dismiss and retry
            save_failure_screenshot(device, "reinforce_target_picker")
            log.info("No action button after %ds (attempt %d/%d) — dismissing picker and retrying",
                     timeout, attempt + 1, _MAX_RETAPS)
            adb_tap(device, 200, 400)  # tap off-center to dismiss picker
            time.sleep(1)

    log.warning("Neither reinforce nor attack button found after %d attempts, closing menu",
                _MAX_RETAPS)
    save_failure_screenshot(device, "reinforce_target_no_button")
    adb_tap(device, 560, 675)
    time.sleep(0.5)
    return False


@timed_action("target")
def target(device):
    """Open target menu, tap enemy tab, verify marker exists, then tap target.
    Returns True on success, False on general failure, 'no_marker' if target marker not found.
    """
    log = get_logger("actions", device)
    if config.get_device_config(device, "auto_heal"):
        heal_all(device)

    if check_screen(device) != Screen.MAP:
        log.debug("Not on map_screen, navigating...")
        if not navigate(Screen.MAP, device):
            log.warning("Failed to navigate to map screen")
            return False

    log.debug("Starting target sequence...")

    if not tap_image("target_menu.png", device):
        log.warning("Failed to find target_menu.png")
        return False
    time.sleep(1)

    # Tap the Enemy tab
    logged_tap(device, 738, 310, "target_enemy_tab")
    time.sleep(1)

    # Check how many target markers exist (retry up to 3 seconds)
    matches = []
    start_time = time.time()
    while time.time() - start_time < 3:
        screen = load_screenshot(device)
        if screen is not None:
            matches = find_all_matches(screen, "target_marker.png",
                                       threshold=0.7, device=device)
            if matches:
                break
        time.sleep(0.5)

    if not matches:
        log.warning("No target marker found!")
        return "no_marker"

    if len(matches) > 1:
        log.error("Multiple target markers found (%d) — remove duplicates!", len(matches))
        return "duplicate_markers"

    # Tap the coordinate link in the first entry row to navigate
    logged_tap(device, 350, 476, "target_coords")
    time.sleep(1)

    log.info("Target sequence complete!")
    return True

# ============================================================
# TELEPORT
# ============================================================

def _check_dead(screen, dead_img, device):
    """Check for dead.png on screen, click it if found. Returns True if dead was found."""
    log = get_logger("actions", device)
    if dead_img is None or screen is None:
        return False
    result = cv2.matchTemplate(screen, dead_img, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val > 0.95:
        log.warning("Found dead.png (confidence: %.1f%%), aborting teleport", max_val * 100)
        h, w = dead_img.shape[:2]
        logged_tap(device, max_loc[0] + w // 2, max_loc[1] + h // 2, "tp_dead_click")
        time.sleep(1)
        return True
    return False

def _find_green_pixel(screen, target_color, tolerance=20):
    """Check the center of the screen for the green teleport circle.

    Scans a large region (x:50-1000, y:100-800) to catch the circle regardless
    of camera position.  Samples every 5th pixel for speed and requires at least
    20 matching pixels to avoid false positives from small green UI elements.
    """
    region = screen[100:800:5, 50:1000:5].astype(np.int16)
    diff = np.abs(region - np.array(target_color))
    matches = np.all(diff < tolerance, axis=2)
    return int(np.sum(matches)) >= 20

def _check_green_at_current_position(device, dead_img, stop_check=None):
    """Long-press to open context menu, tap TELEPORT, check for green circle.

    Assumes the camera is already positioned where we want to test.
    Returns (result, screenshot_path, elapsed_s) where result is:
        True  — green circle found
        False — no green circle (normal miss)
        None  — dead.png detected (caller should abort)
    """
    log = get_logger("actions", device)
    target_color = (0, 255, 0)  # BGR green
    start = time.time()

    # Long press to open context menu
    adb_swipe(device, 540, 1400, 540, 1400, 1000)
    time.sleep(2)
    if stop_check and stop_check():
        return False, None, time.time() - start

    # Tap the TELEPORT button on context menu
    logged_tap(device, 780, 1400, "tp_search_btn")
    time.sleep(2)
    if stop_check and stop_check():
        return False, None, time.time() - start

    # Poll for green boundary circle (valid location)
    green_check_start = time.time()
    screen = None
    green_checks = 0

    while time.time() - green_check_start < 3:
        if stop_check and stop_check():
            return False, None, time.time() - start

        screen = load_screenshot(device)
        if screen is None:
            time.sleep(1)
            continue

        if _check_dead(screen, dead_img, device):
            return None, None, time.time() - start

        green_checks += 1
        if _find_green_pixel(screen, target_color):
            elapsed = time.time() - start
            ss_path = save_failure_screenshot(device, "teleport_green_found")
            log.debug("Green circle found after %d checks (%.1fs)", green_checks, elapsed)
            return True, ss_path, elapsed

        time.sleep(1)

    # No green found — cancel
    elapsed = time.time() - start
    log.debug("No green circle after %d checks (%.1fs). Canceling...", green_checks, elapsed)
    if screen is not None:
        match = find_image(screen, "cancel.png")
        if match:
            _, max_loc, h, w = match
            logged_tap(device, max_loc[0] + w // 2, max_loc[1] + h // 2, "tp_cancel")
        else:
            log.debug("Cancel button not found, waiting for UI to clear...")
    time.sleep(2)

    return False, None, elapsed


# Player name colors (BGR) for detecting other players at Evil Guards
_PLAYER_NAME_BLUE = (255, 150, 66)    # #4296FF
_PLAYER_TAG_GOLD  = (115, 215, 255)   # #FFD773

def _detect_player_at_eg(screen, x, y, box_size=200, tolerance=25):
    """Check for another player's name near a dead priest position.

    Player names appear in blue (#4296FF) with gold (#FFD773) alliance tags.
    Both colors must be present in the region to confirm a player is there.
    Returns True if another player is detected.
    """
    h, w = screen.shape[:2]
    x0 = max(x - box_size // 2, 0)
    x1 = min(x + box_size // 2, w)
    y0 = max(y - box_size // 2, 0)
    y1 = min(y + box_size // 2, h)

    region = screen[y0:y1:3, x0:x1:3].astype(np.int16)

    blue_diff = np.abs(region - np.array(_PLAYER_NAME_BLUE))
    blue_matches = np.sum(np.all(blue_diff < tolerance, axis=2))

    gold_diff = np.abs(region - np.array(_PLAYER_TAG_GOLD))
    gold_matches = np.sum(np.all(gold_diff < tolerance, axis=2))

    return blue_matches >= 5 and gold_matches >= 3

def _teleport_random(device, dry_run=False, stop_check=None):
    """Random camera panning teleport (original algorithm).

    Assumes troop/heal/screen checks already done by teleport().
    """
    log = get_logger("actions", device)

    # When called from auto-occupy, the camera is centered on the tower we
    # just attacked.  Tapping it opens its info dialog, which auto-pans the
    # camera so the tower moves up and the view centers on the empty area
    # below — a spot more likely to be valid for teleporting.
    logged_tap(device, 540, 960, "tp_start")
    time.sleep(2)

    dead_img = get_template("elements/dead.png")

    screen = load_screenshot(device)
    if _check_dead(screen, dead_img, device):
        return False

    # Dismiss any dialog opened by the tap above
    logged_tap(device, 540, 500, "tp_dismiss_dialog")
    time.sleep(2)

    log.debug("Starting teleport search loop (90 second timeout)...")
    start_time = time.time()
    attempt_count = 0
    max_attempts = 15

    while time.time() - start_time < 90 and attempt_count < max_attempts:
        if stop_check and stop_check():
            return False
        attempt_count += 1

        distance = random.randint(200, 400)
        dir_x = random.choice([-1, 1])
        dir_y = random.choice([-1, 0, 1])
        end_x = max(100, min(980, 540 + distance * dir_x))
        end_y = max(500, min(1400, 960 + distance * dir_y))
        log.debug("Attempt #%d/%d — pan to (%d, %d)",
                  attempt_count, max_attempts, end_x, end_y)

        adb_swipe(device, 540, 960, end_x, end_y, 300)
        time.sleep(1)

        result, ss_path, elapsed = _check_green_at_current_position(
            device, dead_img, stop_check=stop_check)

        if result is None:
            return False

        if result:
            total_elapsed = time.time() - start_time
            if dry_run:
                log.info("GREEN CIRCLE FOUND (dry run) on attempt #%d "
                         "(%.1fs). NOT confirming — canceling.",
                         attempt_count, total_elapsed)
                tap_image("cancel.png", device)
                return True

            log.info("Green circle found on attempt #%d (%.1fs). Confirming...",
                     attempt_count, total_elapsed)
            logged_tap(device, 760, 1700, "tp_confirm")
            time.sleep(2)
            log.info("Teleport confirmed after %d attempt(s), %.1fs total",
                     attempt_count, time.time() - start_time)
            return True

        log.debug("Time elapsed: %.1fs / 90s", time.time() - start_time)

    log.error("Teleport failed after %d attempts (%.1fs)",
              attempt_count, time.time() - start_time)
    save_failure_screenshot(device, "teleport_timeout")
    return False


def _teleport_guided(device, target_square, territory_grid, dry_run=False,
                     stop_check=None):
    """Grid-guided teleport — use territory grid to position camera deterministically.

    Tries known-good positions from memory first, then own-team squares
    sorted by Manhattan distance from the target.

    Returns True on success, False if all candidates exhausted (caller
    falls through to random).
    """
    log = get_logger("actions", device)
    from territory import _get_square_center
    from teleport_memory import get_candidates, record_result

    target_row, target_col = target_square
    candidates = get_candidates(target_row, target_col, grid=territory_grid)

    if not candidates:
        log.debug("No guided candidates for (%d, %d) — skipping to random",
                  target_row, target_col)
        return False

    dead_img = get_template("elements/dead.png")
    start_time = time.time()

    log.info("Guided teleport: %d candidates for target (%d, %d)",
             len(candidates), target_row, target_col)

    for i, (cand_row, cand_col) in enumerate(candidates):
        if time.time() - start_time > 75:
            log.debug("Guided teleport budget exceeded (75s), falling through")
            break
        if stop_check and stop_check():
            return False

        log.debug("Guided attempt %d/%d: square (%d, %d)",
                  i + 1, len(candidates), cand_row, cand_col)

        # Position camera via territory grid tap
        if not navigate(Screen.TERRITORY, device):
            log.warning("Failed to navigate to TERRITORY for guided teleport")
            continue
        cx, cy = _get_square_center(cand_row, cand_col)
        adb_tap(device, cx, cy)
        time.sleep(2)  # Camera settles on MAP after grid tap

        if stop_check and stop_check():
            return False

        # Check green at this position
        result, ss_path, elapsed = _check_green_at_current_position(
            device, dead_img, stop_check=stop_check)

        # Log entity observations (Phase 2 data collection)
        _log_nearby_entities(device, cand_row, cand_col, bool(result))

        if result is None:  # dead
            record_result(target_row, target_col, cand_row, cand_col, False)
            return False
        if result:
            record_result(target_row, target_col, cand_row, cand_col, True)
            if dry_run:
                log.info("Guided teleport GREEN (dry run) at (%d, %d)",
                         cand_row, cand_col)
                tap_image("cancel.png", device)
                return True
            log.info("Guided teleport GREEN at (%d, %d) — confirming",
                     cand_row, cand_col)
            logged_tap(device, 760, 1700, "tp_confirm")
            time.sleep(2)
            log.info("Guided teleport confirmed in %.1fs",
                     time.time() - start_time)
            return True
        else:
            record_result(target_row, target_col, cand_row, cand_col, False)
            log.debug("No green at (%d, %d)", cand_row, cand_col)

    log.info("All %d guided candidates exhausted — falling through to random",
             len(candidates))
    return False


def _log_nearby_entities(device, cand_row, cand_col, success):
    """Log entity observations from protocol GameState (Phase 2 data collection).

    No-op when protocol is not active for this device.
    """
    if device not in config.PROTOCOL_ACTIVE_DEVICES:
        return
    try:
        from startup import get_protocol_game_state
        gs = get_protocol_game_state(device)
        if gs is None:
            return
        entities = getattr(gs, "entity_history", {})
        if not entities:
            return
        from teleport_memory import log_observation
        for eid, info in list(entities.items()):
            x = info.get("X", 0)
            z = info.get("Z", 0)
            if not x or not z:
                continue
            etype = info.get("type", "unknown")
            level = info.get("level", 0)
            # Rough distance estimate using grid square size
            dist_r = abs(info.get("grid_row", cand_row) - cand_row)
            dist_c = abs(info.get("grid_col", cand_col) - cand_col)
            dist = (dist_r ** 2 + dist_c ** 2) ** 0.5
            log_observation(x, z, etype, level, dist, success)
    except Exception:
        pass  # Best-effort logging


@timed_action("teleport")
def teleport(device, dry_run=False, target_square=None, territory_grid=None,
             stop_check=None):
    """Teleport to a valid location on the map.

    If target_square and territory_grid are provided (from auto_occupy),
    tries grid-guided positioning first, then falls through to random panning.

    Args:
        device: ADB device ID string
        dry_run: If True, find green spot but cancel instead of confirming
        target_square: (row, col) tuple of the territory target being attacked
        territory_grid: {(row, col): team_string} dict from scan_targets
        stop_check: Callable returning True when task should stop
    """
    log = get_logger("actions", device)
    if not dry_run:
        log.debug("Checking if all troops are home before teleporting...")
        if not all_troops_home(device):
            log.warning("Troops are not home! Cannot teleport. Aborting.")
            return False
        if config.get_device_config(device, "auto_heal"):
            heal_all(device)
    else:
        log.info("Teleport DRY RUN — skipping troop check and heal")

    if check_screen(device) != Screen.MAP:
        log.warning("Not on map_screen, can't teleport")
        return False

    log.debug("Starting teleport sequence...")

    # Try guided teleport first when we have grid context
    if target_square and territory_grid:
        result = _teleport_guided(device, target_square, territory_grid,
                                  dry_run=dry_run, stop_check=stop_check)
        if result:
            return True
        if stop_check and stop_check():
            return False
        # Guided exhausted — ensure we're on MAP for random fallback
        if check_screen(device) != Screen.MAP:
            if not navigate(Screen.MAP, device):
                log.warning("Cannot reach MAP for random fallback")
                return False

    return _teleport_random(device, dry_run=dry_run, stop_check=stop_check)
