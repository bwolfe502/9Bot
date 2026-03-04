"""Ally castle reinforcement via protocol entity data + map coordinate search.

Key exports:
    navigate_to_coord     — jump map camera to world coordinates via game search UI
    reinforce_ally_castle — full reinforce flow for an ally castle at given coordinates
"""

import time

import config
from config import Screen
from botlog import get_logger, timed_action
from vision import (
    tap_image, wait_for_image_and_tap, load_screenshot, find_image,
    adb_tap, adb_keyevent, logged_tap, save_failure_screenshot,
)
from navigation import navigate, check_screen
from troops import troops_avail, heal_all

from actions._helpers import _interruptible_sleep

_log = get_logger("actions")

# Time to wait for the map to pan to the new coordinate after confirming.
_MAP_PAN_WAIT_S = 2.0


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
            time.sleep(0.1)  # per-keystroke delay, too short to interrupt

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
    _interruptible_sleep(0.2, stop_check)
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

    # Tap center of screen to open the castle detail panel.
    logged_tap(device, 540, 960, "ally_castle_select")
    _interruptible_sleep(1, stop_check)

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
    da_screen = load_screenshot(device)
    if da_screen is not None and find_image(da_screen, "depart_anyway.png", threshold=0.65) is not None:
        log.warning("Low health troops — 'Depart Anyway' visible for %s", label)
        if config.get_device_config(device, "auto_heal"):
            log.info("Healing troops before retry for %s", label)
            # Dismiss the depart dialog first — BACK key closes it reliably
            adb_keyevent(device, 4)  # KEYCODE_BACK
            _interruptible_sleep(0.5, stop_check)
            navigate(Screen.MAP, device)
            heal_all(device)
            return False  # retry on next cycle (heal_all navigates to MAP)
        log.info("Auto heal off — tapping Depart Anyway for %s", label)
        tap_image("depart_anyway.png", device, threshold=0.65)
        navigate(Screen.MAP, device)
        return True

    log.warning("Depart button not found after ally reinforce for %s", label)
    save_failure_screenshot(device, "ally_reinforce_depart_missing")
    navigate(Screen.MAP, device)
    return False
