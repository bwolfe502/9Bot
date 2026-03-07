"""Ally castle reinforcement via protocol entity data + map coordinate search.

Key exports:
    navigate_to_coord     — jump map camera to world coordinates via game search UI
    reinforce_ally_castle — full reinforce flow for an ally castle at given coordinates
    capture_home_coords   — navigate away/back to center camera on home castle, OCR coordinates
"""

import re
import time

import config
from config import Screen
from botlog import get_logger, timed_action
from vision import (
    tap_image, wait_for_image_and_tap, load_screenshot, find_image,
    find_all_matches, adb_tap, adb_keyevent, logged_tap,
    save_failure_screenshot, read_text,
)
from navigation import navigate, check_screen
from troops import troops_avail, heal_all

from actions._helpers import _interruptible_sleep, check_depart_anyway, tap_depart_anyway

_log = get_logger("actions")

# Time to wait for the map to pan to the new coordinate after confirming.
_MAP_PAN_WAIT_S = 2.0



def _get_shield_remaining(device):
    """Get shield remaining seconds from protocol countdown. Returns float or None."""
    try:
        from startup import get_protocol_shield_status
    except ImportError:
        return None
    return get_protocol_shield_status(device)


def _center_on_home_castle(device, stop_check=None):
    """Center camera on home castle by switching screens. Returns True on success."""
    log = get_logger("actions", device)
    if check_screen(device) != Screen.MAP:
        if not navigate(Screen.MAP, device):
            log.warning("ensure_shield: failed to reach MAP")
            return False
    # Quick screen switch centers on home castle.
    adb_tap(device, 452, 1841)
    _interruptible_sleep(0.5, stop_check)
    adb_tap(device, 987, 1841)
    _interruptible_sleep(0.8, stop_check)
    return True


def _query_shield_via_ui(device, stop_check=None):
    """Open shield menu to trigger GetShieldInfoAck, then close.

    The game sends GetShieldInfoAck when the shield menu opens.
    Protocol handler captures ShieldEndTs for the countdown.
    """
    log = get_logger("actions", device)

    # Tap castle at center.
    adb_tap(device, 540, 960)
    _interruptible_sleep(1.0, stop_check)

    # Tap shield button in castle menu.
    adb_tap(device, 530, 1050)
    _interruptible_sleep(1.0, stop_check)

    # GetShieldInfoAck should now be captured by protocol handler.
    # Single tap exits shield screen.
    adb_tap(device, 452, 1841)
    _interruptible_sleep(0.5, stop_check)

    log.debug("Shield menu queried — protocol should have ShieldEndTs now")


def _apply_shield_via_ui(device, stop_check=None):
    """Open shield menu and tap 8-hour shield button."""
    log = get_logger("actions", device)

    # Tap castle at center.
    adb_tap(device, 540, 960)
    _interruptible_sleep(1.0, stop_check)

    if stop_check and stop_check():
        return

    # Tap shield button in castle menu.
    adb_tap(device, 530, 1050)
    _interruptible_sleep(1.0, stop_check)

    if stop_check and stop_check():
        return

    # Tap 8-hour shield button.
    adb_tap(device, 200, 965)
    _interruptible_sleep(0.5, stop_check)

    # Check for confirmation popup — tap confirm if present.
    screen = load_screenshot(device)
    if screen is not None and find_image(screen, "checked.png", threshold=0.7) is not None:
        adb_tap(device, 540, 1200)
        _interruptible_sleep(0.5, stop_check)

    log.info("8-hour shield applied")

    # Single tap exits shield screen.
    adb_tap(device, 452, 1841)
    _interruptible_sleep(0.5, stop_check)


# Buffer: re-apply shield when less than 1 hour remains.
_SHIELD_BUFFER_S = 3600


def ensure_shield(device, stop_check=None) -> bool:
    """Ensure castle has an active shield. Uses protocol countdown.

    Flow:
    1. Check protocol countdown — if shield has >1hr left, skip.
    2. If no countdown data yet, open shield menu to trigger GetShieldInfoAck.
    3. If shield expired or expiring soon, apply 8-hour shield via UI.

    Returns True if shield is active, False on failure.
    """
    log = get_logger("actions", device)

    # Check existing countdown from protocol.
    remaining = _get_shield_remaining(device)

    if remaining is not None and remaining > _SHIELD_BUFFER_S:
        log.info("Shield active: %.0f min remaining — no action needed",
                 remaining / 60)
        return True

    # Need to navigate to castle for either querying or applying.
    if not _center_on_home_castle(device, stop_check):
        return False

    if stop_check and stop_check():
        return False

    # If we have no shield data yet, query it first.
    if remaining is None:
        _query_shield_via_ui(device, stop_check)
        _interruptible_sleep(0.5, stop_check)
        remaining = _get_shield_remaining(device)
        if remaining is not None and remaining > _SHIELD_BUFFER_S:
            log.info("Shield confirmed active: %.0f min remaining", remaining / 60)
            return True

    # Shield expired or expiring soon — apply.
    if remaining is not None:
        log.info("Shield expiring (%.0f min left) — applying 8hr shield", remaining / 60)
    else:
        log.info("Shield status unknown — applying 8hr shield")

    _apply_shield_via_ui(device, stop_check)

    # Verify: re-query to capture new ShieldEndTs.
    if not _center_on_home_castle(device, stop_check):
        return True  # applied but can't verify
    _query_shield_via_ui(device, stop_check)
    remaining = _get_shield_remaining(device)
    if remaining is not None and remaining > 0:
        log.info("Shield verified: %.0f min remaining", remaining / 60)
    return True


def _minimize_quest_dialog(device, stop_check=None) -> bool:
    """Minimize the quest panel if visible.

    The quest panel overlaps the map search icon on the MAP screen.
    Taps quest_minimize.png (new template required). Returns True if
    minimized or already hidden, False if minimize button not found.
    """
    log = get_logger("actions", device)
    screen = load_screenshot(device)
    if screen is None:
        return False
    if find_image(screen, "quest_minimize.png") is None:
        return True  # already hidden
    if tap_image("quest_minimize.png", device):
        _interruptible_sleep(0.4, stop_check)
        log.debug("Quest dialog minimized")
        return True
    log.warning("quest_minimize.png found but tap failed")
    return False


def capture_home_coords(device, stop_check=None):
    """Read home castle coordinates from the MAP screen coordinate banner.

    Reads the coordinate banner at the bottom of the MAP screen (region
    520,1640 → 734,1681). The game must already be on the MAP screen centered
    on the home castle before calling this.

    Returns (x, z) as display-unit integers (e.g. X:2276 Y:3394 → (2276, 3394)),
    or None on failure.
    """
    log = get_logger("actions", device)

    # Ensure on MAP before the screen switch.
    if check_screen(device) != Screen.MAP:
        if not navigate(Screen.MAP, device):
            log.warning("capture_home_coords: failed to reach MAP screen")
            return None

    # Quick screen switch to reset camera, centering on home castle.
    adb_tap(device, 452, 1841)
    _interruptible_sleep(0.5, stop_check)
    adb_tap(device, 987, 1841)
    _interruptible_sleep(0.5, stop_check)

    _minimize_quest_dialog(device, stop_check)
    _interruptible_sleep(0.3, stop_check)

    screen = load_screenshot(device)
    if screen is None:
        log.warning("capture_home_coords: screenshot failed")
        return None

    # Coordinate banner region: (x1, y1, x2, y2) — "X:2276 Y:3394" style text.
    coord_text = read_text(screen, (520, 1640, 734, 1681))
    if not coord_text:
        log.warning("capture_home_coords: no text found in coordinate region")
        return None

    # Parse "X:2276 Y:3394" — Y often misread as V/U by OCR, so match any letter.
    m = re.search(r'[Xx][:\s]*(\d+)[^\d]+[A-Za-z][:\s]*(\d+)', coord_text)
    if not m:
        log.warning("capture_home_coords: could not parse coords from %r", coord_text)
        return None

    x = int(m.group(1))
    z = int(m.group(2))
    log.info("Home castle coordinates captured: X=%d Y=%d", x, z)
    return x, z


def navigate_to_coord(device, x: int, z: int, stop_check=None) -> bool:
    """Jump the map camera to world coordinates (x, z) using the game search.

    Flow:
    1. Ensure on MAP screen.
    2. Minimize quest dialog (blocks search icon).
    3. Tap map search icon.
    4. Tap x_coordinate.png field, clear, type x // 1000.
    5. Tap y_coordinate.png field, clear, type z // 1000.
    6. Confirm (map_search_confirm.png or ENTER keyevent).
    7. Wait for map to pan.

    Returns True on success, False on any failure.
    """
    log = get_logger("actions", device)

    if check_screen(device) != Screen.MAP:
        if not navigate(Screen.MAP, device):
            log.warning("navigate_to_coord: failed to reach MAP screen")
            return False

    _minimize_quest_dialog(device, stop_check)

    if not tap_image("map_search.png", device):
        log.warning("navigate_to_coord: map_search.png not found")
        save_failure_screenshot(device, "map_search_not_found")
        return False
    _interruptible_sleep(0.4, stop_check)

    x_disp = x // 1000
    z_disp = z // 1000

    def _type_digits(val: int) -> None:
        """Type each digit individually via keyevents (KEYCODE_0=7 … KEYCODE_9=16)."""
        for ch in str(val):
            adb_keyevent(device, 7 + int(ch))
            time.sleep(0.15)  # per-keystroke delay

    # Tap X input field, clear, type value.
    adb_tap(device, 348, 902)
    time.sleep(0.1)
    for _ in range(4):
        adb_keyevent(device, 67)  # KEYCODE_DEL (backspace)
    time.sleep(0.1)
    _type_digits(x_disp)
    _interruptible_sleep(0.2, stop_check)

    # Tap Y input field, clear, type value.
    adb_tap(device, 772, 896)
    _interruptible_sleep(0.3, stop_check)
    for _ in range(4):
        adb_keyevent(device, 67)  # KEYCODE_DEL (backspace)
    time.sleep(0.1)
    _type_digits(z_disp)
    _interruptible_sleep(0.2, stop_check)

    log.debug("Entered coordinates: x=%d y=%d (raw: %d,%d)", x_disp, z_disp, x, z)

    # Confirm — try template first, fall back to ENTER keyevent (66).
    if not tap_image("map_search_confirm.png", device):
        adb_keyevent(device, 66)  # KEYCODE_ENTER

    _interruptible_sleep(_MAP_PAN_WAIT_S, stop_check)
    log.debug("navigate_to_coord (%d, %d) complete", x, z)
    return True


@timed_action("reinforce_ally_castle")
def reinforce_ally_castle(device, x: int, z: int, player_name: str = "",
                           stop_check=None) -> bool:
    """Navigate to an ally castle at (x, z) and reinforce it.

    Flow:
    1. navigate_to_coord to move map camera to the ally's location.
    2. Tap center of screen to select the castle.
    3. Wait for reinforce_button.png and tap it.
    4. Tap depart.png (with depart_anyway.png fallback).

    Returns True if a troop departed, False otherwise.
    """
    log = get_logger("actions", device)

    label = player_name or f"({x},{z})"
    log.info("Reinforcing ally %s at (%d, %d)", label, x, z)

    if config.get_device_config(device, "auto_heal"):
        heal_all(device)

    if stop_check and stop_check():
        return False

    troops = troops_avail(device)
    min_troops = config.get_device_config(device, "min_troops")
    if troops <= min_troops:
        log.warning("Not enough troops to reinforce ally %s (have %d, need >%d)",
                    label, troops, min_troops)
        return False

    if not navigate_to_coord(device, x, z, stop_check):
        log.warning("Failed to navigate to ally %s coordinates (%d, %d)", label, x, z)
        return False

    if stop_check and stop_check():
        return False

    # Tap a 3x3 grid across the castle area to find and open the castle detail panel.
    # Castle may not be perfectly centered after navigate_to_coord.
    # Start from center and spiral outward.
    _grid_points = [
        (551, 966),  # center first
        (476, 906), (551, 906), (626, 906),  # top row
        (626, 966),                           # middle right
        (626, 1025), (551, 1025), (476, 1025), # bottom row
        (476, 966),                           # middle left
    ]
    panel_opened = False
    for gx, gy in _grid_points:
        adb_tap(device, gx, gy)
        time.sleep(0.15)
        screen = load_screenshot(device)
        if screen is not None and find_image(screen, "detail_button.png", threshold=0.7) is not None:
            log.debug("Castle panel opened at grid tap (%d, %d)", gx, gy)
            panel_opened = True
            break
    if not panel_opened:
        log.warning("Castle panel did not open after grid tap for %s", label)

    _interruptible_sleep(0.3, stop_check)

    # The ally castle panel always shows the yellow REINFORCE button at a fixed position.
    logged_tap(device, 529, 1043, "ally_reinforce_button")
    _interruptible_sleep(0.4, stop_check)

    if stop_check and stop_check():
        return False

    # Wait for depart button — may take 2-3s to appear after reinforce tap.
    if wait_for_image_and_tap("depart.png", device, timeout=5, threshold=0.75):
        log.info("Ally reinforce departed for %s", label)
        navigate(Screen.MAP, device)
        return True

    # Fallback: depart_anyway.png (troops at low health).
    if check_depart_anyway(device):
        log.warning("Low health troops — 'Depart Anyway' visible for %s", label)
        if config.get_device_config(device, "auto_heal"):
            log.info("Healing troops before retry for %s", label)
            adb_keyevent(device, 4)  # KEYCODE_BACK — dismiss depart dialog
            _interruptible_sleep(0.5, stop_check)
            navigate(Screen.MAP, device)
            heal_all(device)
            return False  # retry on next cycle (heal_all navigates to MAP)
        log.info("Auto heal off — tapping Depart Anyway for %s", label)
        tap_depart_anyway(device)
        navigate(Screen.MAP, device)
        return True

    log.warning("Depart button not found after ally reinforce for %s", label)
    save_failure_screenshot(device, "ally_reinforce_depart_missing")
    navigate(Screen.MAP, device)
    return False


def recall_defending_troops(device, count=2, coords=None, stop_check=None) -> int:
    """Recall defending troops.

    If *coords* is given as (x, z) raw coordinates, navigates to that castle
    and recalls the troop there.  Returns 1 on success, 0 on failure.

    If *coords* is None, falls back to panel-based recall: finds defending.png
    icons on the MAP troop panel and recalls up to *count* troops.

    Returns number of troops successfully recalled.
    """
    log = get_logger("actions", device)

    if check_screen(device) != Screen.MAP:
        if not navigate(Screen.MAP, device):
            log.warning("recall_defending: failed to reach MAP")
            return 0

    # -- Coordinate-based recall (single troop) --
    if coords is not None:
        rx, rz = coords
        log.info("Recalling troop from coords (%d, %d)", rx // 1000, rz // 1000)
        if not navigate_to_coord(device, rx, rz, stop_check):
            log.warning("recall_defending: failed to navigate to (%d, %d)", rx, rz)
            return 0
        # Tap center to select the castle.
        adb_tap(device, 540, 960)
        _interruptible_sleep(1.0, stop_check)
        if stop_check and stop_check():
            return 0
        if wait_for_image_and_tap("recall_troop.png", device, timeout=3, threshold=0.75):
            _interruptible_sleep(0.5, stop_check)
            # Confirm the recall popup.
            adb_tap(device, 300, 1070)
            _interruptible_sleep(1.0, stop_check)
            # Return to map screen.
            adb_tap(device, 460, 1840)
            log.info("Recalled troop from (%d, %d)", rx // 1000, rz // 1000)
            _interruptible_sleep(2.0, stop_check)
            return 1
        log.warning("recall_defending: recall_troop.png not found at (%d, %d)", rx // 1000, rz // 1000)
        save_failure_screenshot(device, "recall_defending_coord_no_recall")
        adb_tap(device, 540, 960)
        _interruptible_sleep(0.5, stop_check)
        return 0

    # -- Panel-based recall (fallback) --
    recalled = 0
    for attempt in range(count):
        if stop_check and stop_check():
            break

        screen = load_screenshot(device)
        if screen is None:
            break

        # Find defending icons on the troop panel.
        matches = find_all_matches(screen, "defending.png", threshold=0.7, min_distance=30, device=device)
        if not matches:
            log.debug("recall_defending: no more defending.png icons found")
            break

        # Tap the first (topmost) defending icon.
        mx, my = matches[0]
        log.info("Recalling defending troop %d/%d at panel (%d, %d)", attempt + 1, count, mx, my)
        adb_tap(device, mx, my)
        _interruptible_sleep(1.5, stop_check)

        if stop_check and stop_check():
            break

        # Tap return button on the castle info popup.
        if wait_for_image_and_tap("return.png", device, timeout=3, threshold=0.75):
            recalled += 1
            log.info("Defending troop %d recalled", attempt + 1)
            _interruptible_sleep(1.0, stop_check)
        else:
            log.warning("recall_defending: return.png not found after tapping defending icon")
            save_failure_screenshot(device, "recall_defending_no_return")
            # Tap away to dismiss any popup.
            adb_tap(device, 540, 960)
            _interruptible_sleep(0.5, stop_check)

    if recalled:
        log.info("Recalled %d defending troop(s)", recalled)
        # Wait for troops to start returning.
        _interruptible_sleep(2.0, stop_check)
    return recalled
