import cv2
import json
import numpy as np
import os
import time
import random
from collections import Counter

import config
from config import (SQUARE_SIZE, GRID_OFFSET_X, GRID_OFFSET_Y,
                    GRID_WIDTH, GRID_HEIGHT, THRONE_SQUARES, BORDER_COLORS, ALL_TEAMS, Screen)
from vision import (load_screenshot, tap_image, wait_for_image_and_tap,
                    find_image, adb_tap, adb_keyevent, get_template,
                    save_failure_screenshot)
from navigation import navigate
from troops import troops_avail, all_troops_home, heal_all
from actions import teleport, teleport_to_tower, navigate_to_coord
from botlog import get_logger, timed_action

_log = get_logger("territory")

# ============================================================
# TERRITORY GRID HELPERS (extracted for testability)
# ============================================================

def _get_square_center(row, col):
    """Get pixel coordinates of square center."""
    x = int(GRID_OFFSET_X + col * SQUARE_SIZE + SQUARE_SIZE / 2)
    y = int(GRID_OFFSET_Y + row * SQUARE_SIZE + SQUARE_SIZE / 2)
    return x, y


def _get_border_color(image, row, col):
    """Sample the BORDER pixels of a square — avoids clock obstruction for top rows.

    Returns average BGR color as a tuple of three floats.
    """
    x = int(GRID_OFFSET_X + col * SQUARE_SIZE)
    y = int(GRID_OFFSET_Y + row * SQUARE_SIZE)

    border_pixels = []

    # For row 0 specifically (heavily obscured by clock)
    if row == 0:
        for y_offset in range(2, int(SQUARE_SIZE / 4), 3):
            sample_y = y + y_offset
            if sample_y < image.shape[0]:
                for x_offset in [5, 10, 15, 20, 25, 30, 35]:
                    sample_x = x + x_offset
                    if sample_x < image.shape[1]:
                        border_pixels.append(image[sample_y, sample_x])

    # For row 1 (partially obscured by clock)
    elif row == 1:
        for offset in [5, 10, 15, 20, 25, 30, 35]:
            if y + offset < image.shape[0] and x < image.shape[1]:
                border_pixels.append(image[y + offset, x])
        bottom_y = int(y + SQUARE_SIZE - 1)
        for offset in [5, 10, 15, 20, 25, 30]:
            if bottom_y < image.shape[0] and x + offset < image.shape[1]:
                border_pixels.append(image[bottom_y, x + offset])

    # For all other rows (not obscured)
    else:
        for offset in [8, 15, 22, 30]:
            if x + offset < image.shape[1] and y < image.shape[0]:
                border_pixels.append(image[y, x + offset])
        for offset in [8, 15, 22, 30]:
            if y + offset < image.shape[0] and x < image.shape[1]:
                border_pixels.append(image[y + offset, x])

    if border_pixels:
        avg = np.mean(border_pixels, axis=0)
        return tuple(avg)
    return (0, 0, 0)


def _classify_square_team(bgr, device=None, candidate_teams=None):
    """Determine team based on border color — find closest Euclidean match.

    candidate_teams: optional frozenset of team colors expected in this zone.
    When provided, only those teams are considered.  If none passes the threshold,
    falls back to matching against all teams (handles third-team infiltration).

    Thresholds:
    - Green: <= 70 (neutral, always recognized)
    - Enemy teams: <= 70
    - Own team (MY_TEAM_COLOR): <= 90 (lenient — we want to find our own borders)
    - Any team: <= 55 (tight fallback)
    - Fallback own team: <= 95 (last resort)
    """
    b, g, r = bgr

    my_team = config.get_device_config(device, "my_team") if device else config.MY_TEAM_COLOR
    enemy_teams = config.get_device_enemy_teams(device) if device else config.ENEMY_TEAMS

    check_colors = BORDER_COLORS
    if candidate_teams is not None:
        check_colors = {t: c for t, c in BORDER_COLORS.items() if t in candidate_teams}

    min_distance = float('inf')
    best_team = "unknown"

    distances = {}
    for team, (target_b, target_g, target_r) in check_colors.items():
        distance = ((b - target_b)**2 + (g - target_g)**2 + (r - target_r)**2)**0.5
        distances[team] = distance

        if distance < min_distance:
            min_distance = distance
            best_team = team

    if best_team == "green" and min_distance <= 70:
        return "green"
    elif best_team in enemy_teams and min_distance <= 70:
        return best_team
    elif best_team == my_team and min_distance <= 90:
        return best_team
    elif min_distance <= 55:
        return best_team

    if best_team == "unknown" and my_team in distances and distances[my_team] <= 95:
        return my_team

    # Restricted search found no match — fall back to all teams
    if candidate_teams is not None:
        return _classify_square_team(bgr, device, candidate_teams=None)

    return "unknown"


def _has_flag(image, row, col):
    """Check if a square has a flag using red flag color #FF5D5A.

    Returns True if more than 15 red pixels are found in the square.
    """
    x = int(GRID_OFFSET_X + col * SQUARE_SIZE)
    y = int(GRID_OFFSET_Y + row * SQUARE_SIZE)
    w = int(SQUARE_SIZE)
    h = int(SQUARE_SIZE)

    square = image[y:y+h, x:x+w]

    red_flag_mask = cv2.inRange(square, (75, 80, 240), (105, 110, 255))
    red_pixels = cv2.countNonZero(red_flag_mask)

    return red_pixels > 15


def _is_adjacent_to_my_territory(image, row, col, device=None):
    """Check if square is DIRECTLY next to own territory."""
    my_team = config.get_device_config(device, "my_team") if device else config.MY_TEAM_COLOR
    neighbors = [
        (row-1, col),
        (row+1, col),
        (row, col-1),
        (row, col+1),
    ]

    for r, c in neighbors:
        if not (0 <= r < GRID_HEIGHT and 0 <= c < GRID_WIDTH):
            continue
        if (r, c) in THRONE_SQUARES:
            continue

        border_color = _get_border_color(image, r, c)
        team = _classify_square_team(border_color, device=device)

        if team == my_team:
            return True

    return False


# ============================================================
# TERRITORY ATTACK SYSTEM
# ============================================================

def _build_target_lists(grid, image, device, my_team, enemy_teams, blocked,
                        empty_enemy_squares=None, contested_by_us=None,
                        empty_friendly_squares=None):
    """Build prioritized target lists from a classified grid.

    Args:
        grid: dict mapping (row, col) → team string or "throne"/"blocked"/None
        image: screenshot (np.ndarray) for flag detection; may be None when
               empty_enemy_squares/contested_by_us are provided by protocol data
        device: ADB device ID (for flag detection logging)
        my_team: own team color string
        enemy_teams: list/set of enemy team color strings
        blocked: set of (row, col) squares that are pass-blocked
        empty_enemy_squares: set of (row, col) known to have no defender (protocol).
               When provided, replaces _has_flag — empty → unflagged, defended → flagged,
               contested by us → skipped entirely.
        contested_by_us: set of (row, col) where our team is already contesting.
               Squares in this set are skipped (already handled by our troop).
        empty_friendly_squares: set of (row, col) of own-team squares with no defender
               (protocol only — vision path cannot detect friendly defender status).

    Returns:
        (unflagged_enemies, flagged_enemies, friendly_reinforce, unflagged_friendly)
        unflagged_enemies  = empty enemy frontline squares (no defender).
        flagged_enemies    = defended enemy frontline squares.
        friendly_reinforce = all own frontline squares adjacent to enemy.
        unflagged_friendly = own frontline squares with no defender (protocol) or all
                             frontline squares (vision — can't distinguish defender status).
    """
    unflagged_enemies = []
    flagged_enemies = []
    friendly_reinforce = []
    unflagged_friendly = []
    use_protocol_flags = empty_enemy_squares is not None

    for row in range(GRID_HEIGHT):
        for col in range(GRID_WIDTH):
            if (row, col) in THRONE_SQUARES:
                continue
            if (row, col) in config.MANUAL_IGNORE_SQUARES:
                continue

            team = grid.get((row, col))

            if team in enemy_teams:
                # Skip if we already have a troop contesting this square
                if contested_by_us and (row, col) in contested_by_us:
                    continue
                adj = False
                for nr, nc in [(row-1, col), (row+1, col), (row, col-1), (row, col+1)]:
                    if grid.get((nr, nc)) == my_team:
                        adj = True
                        break
                if adj:
                    if use_protocol_flags:
                        if (row, col) in empty_enemy_squares:
                            unflagged_enemies.append((row, col))
                        else:
                            flagged_enemies.append((row, col))
                    else:
                        if _has_flag(image, row, col):
                            flagged_enemies.append((row, col))
                        else:
                            unflagged_enemies.append((row, col))

            elif team == my_team:
                for nr, nc in [(row-1, col), (row+1, col), (row, col-1), (row, col+1)]:
                    if grid.get((nr, nc)) in enemy_teams:
                        friendly_reinforce.append((row, col))
                        # Protocol: only unflagged (no defender). Vision: include all.
                        if empty_friendly_squares is not None:
                            if (row, col) in empty_friendly_squares:
                                unflagged_friendly.append((row, col))
                        else:
                            unflagged_friendly.append((row, col))
                        break

    return unflagged_enemies, flagged_enemies, friendly_reinforce, unflagged_friendly


def scan_targets(device):
    """Scan the territory grid and return prioritized target lists.

    Must be called while on the TERRITORY screen.

    Returns dict with keys:
        'unflagged_enemies': [(row, col), ...] — empty enemy squares adjacent to own territory
                              (no defender = walk straight in; or unflagged when using vision)
        'flagged_enemies':   [(row, col), ...] — defended enemy squares adjacent to own territory
                              (need battle; or flagged when using vision)
        'friendly':          [(row, col), ...] — own team squares adjacent to enemy territory (for reinforcement)
        'image':             np.ndarray or None — screenshot used for vision path (None for protocol path)
    Returns None if screenshot fails (vision path only).
    """
    log = get_logger("territory", device)
    my_team = config.get_device_config(device, "my_team")
    enemy_teams = config.get_device_enemy_teams(device)
    blocked = config.PASS_BLOCKED_SQUARES
    log.debug("Scanning grid — my_team=%s, enemies=%s", my_team, enemy_teams)

    # --- Protocol fast path: no screenshot needed when fresh territory data available ---
    try:
        import startup
        proto_grid = startup.get_protocol_territory_grid(device)
    except Exception:
        proto_grid = None

    if proto_grid is not None:
        # proto_grid: {(row,col): (owner_team, contester_team, has_defender)}
        empty_enemy_squares = set()    # no defending troop — walk straight in
        empty_friendly_squares = set() # own squares with no defender
        contested_by_us = set()        # our troop already contesting — skip
        grid = {}
        for (row, col), (owner_team, contester_team, has_defender) in proto_grid.items():
            if owner_team:
                grid[(row, col)] = owner_team
            if not has_defender and owner_team in enemy_teams:
                empty_enemy_squares.add((row, col))
            if not has_defender and owner_team == my_team:
                empty_friendly_squares.add((row, col))
            if contester_team == my_team:
                contested_by_us.add((row, col))
        for sq in THRONE_SQUARES:
            grid[sq] = "throne"
        for sq in blocked:
            grid.setdefault(sq, "blocked")
        log.info("scan_targets: protocol grid — %d towers, %d empty enemy, %d empty friendly, %d contested by us",
                 len(proto_grid), len(empty_enemy_squares), len(empty_friendly_squares), len(contested_by_us))
        unflagged_enemies, flagged_enemies, friendly_reinforce, unflagged_friendly = _build_target_lists(
            grid, None, device, my_team, enemy_teams, blocked,
            empty_enemy_squares=empty_enemy_squares, contested_by_us=contested_by_us,
            empty_friendly_squares=empty_friendly_squares)
        image = None
    else:
        # --- Vision path: take screenshot and classify every square ---
        image = load_screenshot(device)
        if image is None:
            log.error("scan_targets: failed to load screenshot")
            return None
        zone_expected = config.ZONE_EXPECTED_TEAMS
        zone_overrides = 0
        grid = {}
        for row in range(GRID_HEIGHT):
            for col in range(GRID_WIDTH):
                if (row, col) in THRONE_SQUARES:
                    grid[(row, col)] = "throne"
                    continue
                if (row, col) in blocked:
                    grid[(row, col)] = "blocked"
                    continue
                border_color = _get_border_color(image, row, col)
                candidates = zone_expected.get((row, col))
                team = _classify_square_team(border_color, device=device,
                                             candidate_teams=candidates)
                if candidates:
                    team_raw = _classify_square_team(border_color, device=device)
                    if team_raw != team:
                        zone_overrides += 1
                grid[(row, col)] = team
        if zone_overrides:
            log.info("Zone hints overrode %d square classifications", zone_overrides)

        # --- Neighbor voting: fix isolated misclassified squares ---
        all_teams_set = set(ALL_TEAMS)
        fixes = 0
        for (row, col), team in list(grid.items()):
            if team not in all_teams_set:
                continue
            neighbors = [(row-1, col), (row+1, col), (row, col-1), (row, col+1)]
            neighbor_teams = [grid.get((nr, nc)) for nr, nc in neighbors
                             if grid.get((nr, nc)) in all_teams_set]
            if not neighbor_teams:
                continue
            if team not in neighbor_teams:
                counts = Counter(neighbor_teams)
                majority_team, majority_count = counts.most_common(1)[0]
                if majority_count >= 2:
                    grid[(row, col)] = majority_team
                    fixes += 1
        if fixes:
            log.debug("Neighbor voting fixed %d squares", fixes)

        unflagged_enemies, flagged_enemies, friendly_reinforce, unflagged_friendly = _build_target_lists(
            grid, image, device, my_team, enemy_teams, blocked)

    # Manual attack overrides replace auto-detected targets entirely
    if config.MANUAL_ATTACK_SQUARES:
        unflagged_enemies = [s for s in config.MANUAL_ATTACK_SQUARES
                             if s not in blocked]
        flagged_enemies = []
        log.info("Using ONLY manual attack squares (%d)", len(unflagged_enemies))

    log.info("Scan: %d unflagged enemies, %d flagged, %d friendly frontline, %d unflagged friendly",
             len(unflagged_enemies), len(flagged_enemies), len(friendly_reinforce), len(unflagged_friendly))

    return {
        "unflagged_enemies": unflagged_enemies,
        "flagged_enemies": flagged_enemies,
        "friendly": friendly_reinforce,
        "unflagged_friendly": unflagged_friendly,
        "image": image,
    }


def _pick_target(scan_result):
    """Pick a target from scan results by priority.

    Priority:
        1. Unflagged enemy squares (best — no one marching there yet)
        2. Flagged enemy squares (fallback — someone else may be attacking)
        3. Friendly frontline squares (reinforce when no enemy targets)

    Returns (row, col, action_type) or None if no targets.
    action_type is "attack" or "reinforce".
    """
    for targets, action in [
        (scan_result["unflagged_enemies"], "attack"),
        (scan_result["flagged_enemies"], "attack"),
        (scan_result["friendly"], "reinforce"),
    ]:
        if targets:
            row, col = random.choice(targets)
            return row, col, action
    return None


def _pick_frontline_target(scan_result, mode):
    """Pick a frontline target based on mode setting.

    "attack"    — targets only unflagged enemy squares (no defender).
    "reinforce" — targets only unflagged friendly squares (no defender).

    Returns (row, col, action_type) or None if no targets available.
    """
    if mode == "attack":
        targets = scan_result["unflagged_enemies"]
        action = "attack"
    else:
        targets = scan_result["unflagged_friendly"]
        action = "reinforce"

    if targets:
        row, col = random.choice(targets)
        return row, col, action
    return None


@timed_action("attack_territory")
def attack_territory(device, debug=False, target_picker=None):
    """Scan territory grid and pick a target.

    Returns (row, col, action_type) on success, or None if no targets.
    action_type is "attack" or "reinforce".

    Side effect: stores target in config.LAST_ATTACKED_SQUARE[device]
    and taps the target square on the territory grid (camera trick).

    target_picker: optional callable(scan_result) → (row, col, action_type) | None.
                   Defaults to _pick_target (normal priority order).
    """
    log = get_logger("territory", device)
    log.info("Scanning territory for targets...")

    if not navigate(Screen.TERRITORY, device):
        log.warning("Failed to navigate to territory screen")
        return None

    time.sleep(1)

    scan = scan_targets(device)
    if scan is None:
        return None

    picker = target_picker or _pick_target
    target = picker(scan)
    if target is None:
        log.warning("No valid targets found")
        return None

    target_row, target_col, action_type = target
    click_x, click_y = _get_square_center(target_row, target_col)

    log.info("Selected target (%d, %d) — action: %s", target_row, target_col, action_type)

    # Remember this square PER DEVICE
    config.LAST_ATTACKED_SQUARE[device] = (target_row, target_col)

    # Tap square on territory grid — this centers the camera on the tower
    adb_tap(device, click_x, click_y)

    return (target_row, target_col, action_type)

# ============================================================
# AUTO OCCUPY SYSTEM
# ============================================================

def _interruptible_sleep(seconds, stop_check):
    """Sleep in 1-second chunks, returning True immediately if stopped."""
    for _ in range(int(seconds)):
        if stop_check():
            return True
        time.sleep(1)
    return False


def _check_and_revive(device, log, stop_check):
    """Check for dead.png. If found, tap to revive and wait for MAP.

    Returns True if we were dead and revived (caller should pick a new target).
    Returns False if alive.
    Returns None if stopped.
    """
    if tap_image("dead.png", device):
        log.warning("Dead! Tapping to revive...")
        config.set_device_status(device, "Reviving...")
        time.sleep(3)
        # Wait for MAP screen after revive (up to 30s)
        for _ in range(30):
            if stop_check():
                return None
            from navigation import check_screen
            if check_screen(device) == Screen.MAP:
                log.info("Revived — back on MAP")
                return True
            time.sleep(1)
        log.warning("Revive timeout — could not confirm MAP screen")
        return True
    return False


def _tap_tower_and_detect_menu(device, log, timeout=10):
    """Tap the tower at center screen repeatedly until a menu button appears.

    Returns:
        "attack"    — attack_button.png found (enemy troops in tower)
        "reinforce" — territory_reinforce.png found (empty or friendly tower)
        None        — neither found within timeout
    """
    start = time.time()
    attempt = 0
    while time.time() - start < timeout:
        attempt += 1
        log.debug("Tapping tower (540, 900), attempt %d...", attempt)
        adb_tap(device, 540, 900)
        time.sleep(1)

        screen = load_screenshot(device)
        if screen is None:
            continue

        if find_image(screen, "attack_button.png", threshold=0.7):
            log.debug("Attack button detected")
            return "attack"

        if find_image(screen, "territory_reinforce.png", threshold=0.7):
            log.debug("Reinforce button detected")
            return "reinforce"

    log.debug("No menu button found after %ds", timeout)
    return None


def _do_depart(device, log, action_type):
    """Tap the action button and depart. Handles depart_anyway fallback.

    action_type: "attack" or "reinforce"
    Returns True if depart was tapped successfully.
    """
    # Tap the action button
    if action_type == "attack":
        if not tap_image("attack_button.png", device, threshold=0.7):
            log.warning("attack_button.png not found for tap")
            return False
    else:
        if not tap_image("territory_reinforce.png", device, threshold=0.7):
            log.warning("territory_reinforce.png not found for tap")
            return False

    time.sleep(1)

    # Try depart
    if wait_for_image_and_tap("depart.png", device, timeout=5):
        log.info("Depart tapped (%s)", action_type)
        return True

    # Depart Anyway fallback (low health troops)
    s = load_screenshot(device)
    if s is not None and find_image(s, "depart_anyway.png", threshold=0.65) is not None:
        log.warning("Low health troops — 'Depart Anyway' visible")
        if config.get_device_config(device, "auto_heal"):
            log.info("Healing troops before retry — returning False for cycle retry")
            # Dismiss the depart dialog first — BACK key closes it reliably
            adb_keyevent(device, 4)  # KEYCODE_BACK
            time.sleep(0.5)
            navigate(Screen.MAP, device)
            heal_all(device)
            return False  # caller will retry the full cycle from MAP
        else:
            log.info("Auto heal off — tapping Depart Anyway")
            if tap_image("depart_anyway.png", device, threshold=0.65):
                return True

    log.warning("Depart failed — neither depart.png nor depart_anyway.png found")
    save_failure_screenshot(device, "occupy_depart_fail")
    return False


class _ProtocolDataPending(Exception):
    """Raised when protocol is active but territory grid data hasn't arrived yet."""


def _grid_to_world(row, col):
    """Convert territory grid (row, col) to world (x, z) coordinates.

    Derived from the protocol formula: row = coord.Z // 300000, col = coord.X // 300000.
    Returns center of the grid square.
    """
    return col * 300000 + 150000, row * 300000 + 150000


def _wait_for_capture(device, row, col, my_team, log, stop_check,
                      poll_interval=10):
    """Poll protocol grid until tower (row, col) is owned by my_team.

    Returns True when captured, False only if stopped.
    Waits indefinitely — the bot stays until the tower flips.
    """
    import startup
    while not stop_check():
        grid = startup.get_protocol_territory_grid(device)
        if grid is not None:
            entry = grid.get((row, col))
            if entry is not None:
                owner_team = entry[0]
                if owner_team == my_team:
                    log.info("Tower (%d,%d) is now %s — captured!", row, col, my_team)
                    return True
        if _interruptible_sleep(poll_interval, stop_check):
            return False
    return False


def _pick_frontline_target_from_protocol(device, mode, my_team, enemy_teams):
    """Use protocol territory grid to pick a frontline target directly.

    Returns (row, col, action_type, world_x, world_z) or None.
    No territory screen navigation needed.
    Returns None only when protocol is disabled or not active for this device.
    Raises _ProtocolDataPending if protocol is active but data hasn't arrived yet.
    """
    if device not in config.PROTOCOL_ACTIVE_DEVICES:
        return None
    try:
        import startup
        proto_grid = startup.get_protocol_territory_grid(device)
    except Exception as e:
        get_logger("territory", device).debug("Protocol grid error: %s", e)
        proto_grid = None

    if proto_grid is None:
        raise _ProtocolDataPending("Territory grid not ready yet")

    log = get_logger("territory", device)
    candidates = []
    skip_team = skip_defender = skip_adj = 0

    for (row, col), (owner_team, contester_team, has_defender) in proto_grid.items():
        if (row, col) in config.THRONE_SQUARES:
            continue
        if (row, col) in config.MANUAL_IGNORE_SQUARES:
            continue

        if mode == "attack":
            # Empty enemy frontline squares
            if owner_team not in enemy_teams:
                skip_team += 1
                continue
            if has_defender:
                skip_defender += 1
                continue
            # Must be adjacent to our territory
            adj = any(
                proto_grid.get((row + dr, col + dc), (None,))[0] == my_team
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]
            )
            if adj:
                candidates.append((row, col, "attack"))
            else:
                skip_adj += 1
        else:
            # Empty friendly frontline squares (reinforce mode)
            if owner_team != my_team:
                skip_team += 1
                continue
            if has_defender:
                skip_defender += 1
                continue
            # Must be adjacent to enemy territory
            adj = any(
                proto_grid.get((row + dr, col + dc), (None,))[0] in enemy_teams
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]
            )
            if adj:
                candidates.append((row, col, "reinforce"))
            else:
                skip_adj += 1

    log.info(
        "Protocol scan (%s mode): grid=%d my_team=%s enemies=%s "
        "skip_team=%d skip_defender=%d skip_adj=%d candidates=%d",
        mode, len(proto_grid), my_team, enemy_teams,
        skip_team, skip_defender, skip_adj, len(candidates)
    )

    if not candidates:
        return None

    row, col, action_type = random.choice(candidates)
    world_x, world_z = _grid_to_world(row, col)
    return row, col, action_type, world_x, world_z


@timed_action("frontline_occupy")
def frontline_occupy_loop(device, stop_check):
    """Frontline occupy loop — targets only unflagged towers on the immediate border.

    When protocol is active: derives world coordinates from the grid position
    (col*300000+150000, row*300000+150000) and navigates directly via map search —
    no territory screen needed.

    When protocol is inactive: falls back to territory grid tap (same as auto-occupy).

    Mode is read from the 'frontline_occupy_action' setting per device:
        "attack"    — find empty enemy frontline towers, tap attack button.
        "reinforce" — find empty friendly frontline towers, tap reinforce button.

    Waits for all troops home before each cycle. If no targets are available,
    waits 30s and rescans. No fallback to other target categories.
    """
    log = get_logger("territory", device)
    log.info("Frontline occupy started")

    # Ensure we start from a known state regardless of which screen is active.
    navigate(Screen.MAP, device)

    consecutive_tp_fails = 0
    _MAX_CONSECUTIVE_TP_FAILS = 3

    # --- One-time protocol bootstrap (first-ever run only) ---
    # Territory data is normally loaded from disk cache on startup.
    # If no cache exists yet (very first run), open territory screen once to
    # fetch the full grid from the game server. Subsequent restarts use the cache.
    if device in config.PROTOCOL_ACTIVE_DEVICES:
        import startup
        if not startup.get_protocol_territory_grid(device):
            log.info("No territory cache — fetching from game server (one-time)...")
            config.set_device_status(device, "Loading Territory Data...")
            if navigate(Screen.TERRITORY, device):
                for _ in range(15):
                    if stop_check():
                        break
                    if startup.get_protocol_territory_grid(device) is not None:
                        log.info("Territory data fetched and cached")
                        break
                    time.sleep(1)
                navigate(Screen.MAP, device)
            else:
                log.warning("Could not reach TERRITORY screen for initial fetch")
        if stop_check():
            return

    while not stop_check():
        try:
            # --- Death check at start of each cycle ---
            revive_result = _check_and_revive(device, log, stop_check)
            if revive_result is None:
                break
            if stop_check():
                break

            # --- Wait for all troops home ---
            config.set_device_status(device, "Checking Troops...")
            if not navigate(Screen.MAP, device):
                log.warning("Cannot reach MAP — retrying in 10s")
                config.set_device_status(device, "Navigating...")
                if _interruptible_sleep(10, stop_check):
                    break
                continue

            if config.get_device_config(device, "auto_heal"):
                heal_all(device)

            if not all_troops_home(device):
                config.set_device_status(device, "Waiting for Troops...")
                if _interruptible_sleep(10, stop_check):
                    break
                continue

            if stop_check():
                break

            mode = config.get_device_config(device, "frontline_occupy_action")
            my_team = config.get_device_config(device, "my_team")
            enemy_teams = config.get_device_enemy_teams(device)

            # --- Protocol path: derive coords directly, skip territory screen ---
            proto_target = None
            try:
                proto_target = _pick_frontline_target_from_protocol(
                    device, mode, my_team, enemy_teams)
            except _ProtocolDataPending:
                log.warning("Protocol data pending — falling back to grid scan")
            except Exception as e:
                log.warning("Protocol target error: %s — falling back to grid scan", e)

            if proto_target is not None:
                target_row, target_col, action_type, world_x, world_z = proto_target
                log.info("Protocol target: (%d,%d) %s — world (%d, %d)",
                         target_row, target_col, action_type, world_x, world_z)
                config.set_device_status(device, f"Targeting ({target_row},{target_col})...")
                config.LAST_ATTACKED_SQUARE[device] = (target_row, target_col)

                # Pan camera directly to tower via map search — no territory screen
                config.set_device_status(device, "Navigating to Tower...")
                if not navigate_to_coord(device, world_x, world_z, stop_check):
                    log.warning("navigate_to_coord failed — skipping cycle")
                    if _interruptible_sleep(5, stop_check):
                        break
                    continue

                # Wait briefly for the game to send entity data for the now-visible tower.
                if _interruptible_sleep(2, stop_check):
                    break
                import startup as _startup
                tower_troops = _startup.get_protocol_kvk_tower_troops(device)
                if tower_troops is not None:
                    troop_count = tower_troops.get((target_row, target_col), -1)
                    if troop_count > 0:
                        log.info("Tower (%d,%d) has %d troop(s) per entity feed — skipping",
                                 target_row, target_col, troop_count)
                        if _interruptible_sleep(5, stop_check):
                            break
                        continue
                    elif troop_count == -1:
                        log.debug("Tower (%d,%d) not yet seen in entity feed — proceeding",
                                  target_row, target_col)

            else:
                # --- Vision fallback: territory grid scan + tap ---
                # Used when protocol is disabled, data pending, or no targets found
                log.info("Using territory grid scan (protocol %s)",
                         "had no targets" if device in config.PROTOCOL_ACTIVE_DEVICES
                         else "unavailable")
                config.set_device_status(device, "Scanning Territory...")
                target_result = attack_territory(
                    device,
                    target_picker=lambda sr: _pick_frontline_target(sr, mode)
                )

                if target_result is None:
                    log.info("No frontline targets (%s mode) — waiting 30s", mode)
                    config.set_device_status(device, "No Targets...")
                    if _interruptible_sleep(30, stop_check):
                        break
                    continue

                target_row, target_col, action_type = target_result
                config.set_device_status(device, f"Targeting ({target_row},{target_col})...")

                if stop_check():
                    break
                time.sleep(2)  # Let camera settle after territory grid tap

            if stop_check():
                break

            # --- Check for alliance occupation before teleporting ---
            # Camera is centered on the tower — alliance_occupied.png (white hammer)
            # is visible if an alliance member is already defending it.
            _screen = load_screenshot(device)
            if _screen is not None:
                _occ_match = find_image(_screen, "alliance_occupied.png", threshold=0.0)
                _occ_score = round(_occ_match[0], 3) if _occ_match else 0.0
                log.info("Tower (%d,%d) alliance_occupied score=%.3f", target_row, target_col, _occ_score)
            if _screen is not None and find_image(_screen, "alliance_occupied.png", threshold=0.92):
                log.info("Tower (%d,%d) is alliance-occupied (visual) — skipping",
                         target_row, target_col)
                config.set_device_status(device, "Scanning Territory...")
                if _interruptible_sleep(5, stop_check):
                    break
                continue

            # --- Teleport adjacent to the target tower ---
            # teleport_to_tower navigates to coordinate positions around the tower
            # and checks for a valid (green) placement circle at each one.
            # Compute world coords for vision fallback path (protocol path already has them).
            if proto_target is None:
                world_x = target_col * 300000 + 150000
                world_z = target_row * 300000 + 150000
            config.set_device_status(device, "Teleporting...")
            if not teleport_to_tower(device, world_x, world_z, stop_check):
                # If alliance-occupied was the reason, skip silently to next target.
                _s = load_screenshot(device)
                if _s is not None and find_image(_s, "alliance_occupied.png", threshold=0.92):
                    log.info("Tower (%d,%d) occupied — moving to next target",
                             target_row, target_col)
                    if _interruptible_sleep(2, stop_check):
                        break
                    continue
                consecutive_tp_fails += 1
                log.warning("Teleport failed (%d consecutive)", consecutive_tp_fails)
                if consecutive_tp_fails >= _MAX_CONSECUTIVE_TP_FAILS:
                    log.warning("Too many consecutive teleport fails — trying different target")
                    consecutive_tp_fails = 0
                    if _interruptible_sleep(5, stop_check):
                        break
                    continue
                if _interruptible_sleep(10, stop_check):
                    break
                continue

            consecutive_tp_fails = 0

            if stop_check():
                break
            time.sleep(2)

            # --- Death check after teleport ---
            revive_result = _check_and_revive(device, log, stop_check)
            if revive_result is None:
                break
            if revive_result:
                continue

            if stop_check():
                break

            # --- Recenter camera on tower using its world coordinates ---
            label = "Attacking" if action_type == "attack" else "Reinforcing"
            config.set_device_status(device, f"{label} Tower...")

            if not navigate_to_coord(device, world_x, world_z, stop_check):
                log.warning("Cannot recenter on tower (%d,%d) — skipping cycle",
                            target_row, target_col)
                if _interruptible_sleep(10, stop_check):
                    break
                continue

            if stop_check():
                break
            time.sleep(1)

            # --- Open tower menu (tower is now at screen center) ---
            menu_type = _tap_tower_and_detect_menu(device, log, timeout=10)
            if menu_type is None:
                log.warning("Tower menu did not open for (%d, %d) — skipping",
                            target_row, target_col)
                save_failure_screenshot(device, "frontline_occupy_menu_fail")
                if _interruptible_sleep(5, stop_check):
                    break
                continue

            if stop_check():
                break

            # --- Depart with the configured action ---
            config.set_device_status(device, f"Deploying ({label})...")
            if _do_depart(device, log, action_type):
                log.info("Cycle complete — %s (%d, %d)", action_type, target_row, target_col)

                # --- In attack mode: wait for capture, then recall ---
                if action_type == "attack":
                    config.set_device_status(device,
                        f"Waiting for Capture ({target_row},{target_col})...")
                    captured = _wait_for_capture(
                        device, target_row, target_col, my_team, log, stop_check)
                    if stop_check():
                        break
                    if captured:
                        log.info("Tower (%d,%d) captured — recalling troop",
                                 target_row, target_col)
                        config.set_device_status(device, "Recalling Troop...")
                        from actions.quests import recall_tower_troop
                        recall_tower_troop(device, stop_check)
            else:
                log.warning("Depart failed for (%d, %d)", target_row, target_col)
                adb_keyevent(device, 4)
                time.sleep(1)

            # --- Wait before next cycle ---
            config.set_device_status(device, "Waiting for Troops...")
            if _interruptible_sleep(10, stop_check):
                break

        except Exception as e:
            log.error("Error in frontline occupy loop: %s", e, exc_info=True)
            save_failure_screenshot(device, "frontline_occupy_exception")
            if _interruptible_sleep(10, stop_check):
                break

    log.info("Frontline occupy stopped")


# ============================================================
# DEBUG FUNCTIONS
# ============================================================

def diagnose_grid(device):
    """Full grid diagnostic — classifies all 576 squares, saves debug image, JSON, and report.

    Navigates to territory screen, screenshots, then runs the same border-color
    classification pipeline as attack_territory.  Outputs:
      - Per-team counts and grid map to INFO log
      - Unknown squares with BGR values to DEBUG log
      - Color-coded debug image to debug/territory_diag_{device}.png
      - Structured JSON to data/territory_diag_{device}_{timestamp}.json
    """
    log = get_logger("territory", device)

    # Navigate to territory screen
    if not navigate(Screen.TERRITORY, device):
        log.warning("diagnose_grid: failed to navigate to territory screen")
        return

    time.sleep(1)
    image = load_screenshot(device)
    if image is None:
        log.error("diagnose_grid: failed to load screenshot")
        return

    debug_img = image.copy()

    # --- Classify every square ---------------------------------------------------
    team_counts = {}          # team_name -> count
    unknown_details = []      # (row, col, bgr_tuple, best_team, distance)
    grid_map = []             # list of 24 strings, each 24 chars
    square_data = []          # per-square JSON export

    # Dot colors for the debug image (BGR)
    DOT_COLORS = {
        "yellow": (0, 220, 255),
        "green":  (0, 200, 0),
        "red":    (0, 0, 255),
        "blue":   (255, 150, 100),
        "unknown": (128, 128, 128),
    }
    TEAM_CHAR = {"yellow": "Y", "green": "G", "red": "R", "blue": "B", "unknown": "?"}

    enemy_teams = set(config.get_device_enemy_teams(device))

    for row in range(GRID_HEIGHT):
        row_chars = []
        for col in range(GRID_WIDTH):
            if (row, col) in THRONE_SQUARES:
                row_chars.append("T")
                continue

            bgr = _get_border_color(image, row, col)
            candidates = config.ZONE_EXPECTED_TEAMS.get((row, col))
            team = _classify_square_team(bgr, device=device,
                                         candidate_teams=candidates)
            team_counts[team] = team_counts.get(team, 0) + 1
            row_chars.append(TEAM_CHAR.get(team, "?"))

            # Compute distance to nearest known color
            best_team = "unknown"
            best_dist = float("inf")
            for t, (tb, tg, tr) in BORDER_COLORS.items():
                d = ((bgr[0]-tb)**2 + (bgr[1]-tg)**2 + (bgr[2]-tr)**2)**0.5
                if d < best_dist:
                    best_dist = d
                    best_team = t

            has_flg = _has_flag(image, row, col)
            is_adj = _is_adjacent_to_my_territory(image, row, col, device=device)

            sq = {
                "row": row,
                "col": col,
                "bgr_avg": [int(v) for v in bgr],
                "classified_team": team,
                "nearest_team": best_team,
                "distance": round(best_dist, 1),
                "has_flag": has_flg,
                "is_adjacent": is_adj,
                "zone_expected": sorted(candidates) if candidates else None,
            }
            square_data.append(sq)

            # Collect unknown details for threshold tuning
            if team == "unknown":
                unknown_details.append((row, col, tuple(int(v) for v in bgr),
                                        best_team, round(best_dist, 1)))

            # Draw colored dot on debug image
            cx, cy = _get_square_center(row, col)
            color = DOT_COLORS.get(team, DOT_COLORS["unknown"])
            cv2.circle(debug_img, (cx, cy), 6, color, -1)
            cv2.circle(debug_img, (cx, cy), 6, (0, 0, 0), 1)  # outline

            # Mark flags and targets on debug image
            if team in enemy_teams and is_adj:
                if has_flg:
                    cv2.line(debug_img, (cx-7, cy-7), (cx+7, cy+7), (0, 0, 200), 2)
                    cv2.line(debug_img, (cx-7, cy+7), (cx+7, cy-7), (0, 0, 200), 2)
                else:
                    cv2.circle(debug_img, (cx, cy), 9, (0, 255, 0), 2)

        grid_map.append("".join(row_chars))

    # --- Summary stats -----------------------------------------------------------
    enemy_count = sum(v for k, v in team_counts.items() if k in enemy_teams)
    flagged = sum(1 for sq in square_data
                  if sq["classified_team"] in enemy_teams and sq["is_adjacent"] and sq["has_flag"])
    adjacent = sum(1 for sq in square_data
                   if sq["classified_team"] in enemy_teams and sq["is_adjacent"])
    valid_targets = adjacent - flagged

    # --- Save debug image --------------------------------------------------------
    safe_device = device.replace(":", "_").replace(".", "_")
    debug_path = os.path.join("debug", f"territory_diag_{safe_device}.png")
    os.makedirs("debug", exist_ok=True)
    cv2.imwrite(debug_path, debug_img)

    # --- Save JSON diagnostic data -----------------------------------------------
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join("data", f"territory_diag_{safe_device}_{timestamp}.json")
    os.makedirs("data", exist_ok=True)
    diag_json = {
        "device": device,
        "timestamp": datetime.now().isoformat(),
        "my_team": config.get_device_config(device, "my_team"),
        "enemy_teams": list(enemy_teams),
        "team_counts": dict(sorted(team_counts.items())),
        "summary": {
            "enemy_total": enemy_count,
            "enemy_adjacent": adjacent,
            "enemy_flagged": flagged,
            "valid_targets": valid_targets,
        },
        "squares": square_data,
    }
    try:
        with open(json_path, "w") as f:
            json.dump(diag_json, f, indent=2)
        log.info("JSON diagnostic saved: %s", json_path)
    except OSError as e:
        log.warning("Failed to save JSON diagnostic: %s", e)

    # --- Log results -------------------------------------------------------------
    log.info("=== TERRITORY GRID DIAGNOSTIC ===")
    log.info("Team config: my_team=%s, enemies=%s", config.get_device_config(device, "my_team"), config.get_device_enemy_teams(device))
    log.info("Classification counts: %s", dict(sorted(team_counts.items())))
    log.info("Enemy: %d total, %d adjacent, %d flagged, %d valid targets",
             enemy_count, adjacent, flagged, valid_targets)
    log.info("Grid map (Y=yellow G=green R=red B=blue ?=unknown T=throne):")
    for i, row_str in enumerate(grid_map):
        log.info("  row %2d: %s", i, row_str)

    if unknown_details:
        log.info("Unknown squares (%d) — nearest color & distance:", len(unknown_details))
        for row, col, bgr, nearest, dist in sorted(unknown_details, key=lambda x: x[4]):
            log.info("  (%2d,%2d) BGR=%-18s nearest=%-7s dist=%.1f",
                     row, col, bgr, nearest, dist)
    else:
        log.info("No unknown squares — all 576 classified!")

    log.info("Debug image saved: %s", debug_path)
    log.info("=== END DIAGNOSTIC ===")

    # Return to map screen
    navigate(Screen.MAP, device)


# Backwards-compatible alias
sample_specific_squares = diagnose_grid


# ============================================================
# TERRITORY COORDINATE SCANNER
# ============================================================

# Region where world coordinates appear on MAP screen (bottom area)
# Initial broad region — will narrow after first calibration run
_COORD_OCR_REGION = (0, 1750, 1080, 1870)

_COORD_DB_PATH = os.path.join("data", "territory_coordinates.json")


def _parse_coordinates(text):
    """Extract (x, y) world coordinates from OCR text like 'x:150, y:7050'.

    Handles common OCR artifacts: 'X' vs 'x', missing colons, extra spaces.
    Returns (x, y) tuple or None if parsing fails.
    """
    import re
    # Normalize common OCR issues
    text = text.replace(" ", "").lower()
    match = re.search(r"x[:\.]?(\d+)[,\s]*y[:\.]?(\d+)", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def scan_territory_coordinates(device, squares=None, save_screenshots=True):
    """Scan territory squares to map grid positions to world coordinates.

    Clicks each territory square, which teleports to the tower's map location,
    then OCR-reads the world coordinates from the MAP screen.

    Args:
        device: ADB device ID
        squares: List of (row, col) tuples to scan. None = all non-throne squares.
        save_screenshots: Save a screenshot per square for calibration/debugging.

    Results are saved to data/territory_coordinates.json.
    """
    from vision import load_screenshot, read_text

    log = get_logger("territory", device)
    log.info("=== TERRITORY COORDINATE SCAN ===")

    # Load existing database if any
    coord_db = {}
    if os.path.isfile(_COORD_DB_PATH):
        try:
            with open(_COORD_DB_PATH, "r") as f:
                import json
                coord_db = json.load(f)
        except Exception as e:
            log.warning("Failed to load coordinate database: %s", e)

    if squares is None:
        squares = [(r, c) for r in range(GRID_HEIGHT) for c in range(GRID_WIDTH)
                    if (r, c) not in THRONE_SQUARES]

    log.info("Scanning %d squares...", len(squares))
    scanned = 0
    failed = 0

    for row, col in squares:
        # Navigate to territory screen
        if not navigate(Screen.TERRITORY, device):
            log.warning("Failed to navigate to territory, aborting scan")
            break

        time.sleep(0.5)

        # Click the square
        cx, cy = _get_square_center(row, col)
        log.debug("Clicking square (%d, %d) at pixel (%d, %d)", row, col, cx, cy)
        adb_tap(device, cx, cy)

        # Wait for MAP transition
        time.sleep(2)

        # Take screenshot
        screen = load_screenshot(device)
        if screen is None:
            log.warning("Screenshot failed for square (%d, %d)", row, col)
            failed += 1
            continue

        # Save debug screenshot if requested
        if save_screenshots:
            safe_device = device.replace(":", "_").replace(".", "_")
            shot_dir = os.path.join("debug", "territory_coords")
            os.makedirs(shot_dir, exist_ok=True)
            shot_path = os.path.join(shot_dir, f"{safe_device}_{row:02d}_{col:02d}.png")
            cv2.imwrite(shot_path, screen)

        # OCR the coordinate region
        text = read_text(screen, region=_COORD_OCR_REGION,
                         allowlist="0123456789xy:,. ", device=device)
        coords = _parse_coordinates(text)

        key = f"{row},{col}"
        if coords:
            coord_db[key] = {"x": coords[0], "y": coords[1]}
            log.info("  (%2d,%2d) → x:%d, y:%d", row, col, coords[0], coords[1])
            scanned += 1
        else:
            log.warning("  (%2d,%2d) → OCR failed: '%s'", row, col, text)
            coord_db[key] = {"x": None, "y": None, "ocr_raw": text}
            failed += 1

    # Save database
    try:
        os.makedirs(os.path.dirname(_COORD_DB_PATH), exist_ok=True)
        import json
        with open(_COORD_DB_PATH, "w") as f:
            json.dump(coord_db, f, indent=2, sort_keys=True)
    except OSError as e:
        log.error("Failed to save coordinate database: %s", e)

    log.info("Scan complete: %d succeeded, %d failed", scanned, failed)
    log.info("Database saved to %s (%d total entries)", _COORD_DB_PATH, len(coord_db))
    log.info("=== END COORDINATE SCAN ===")

    # Return to map
    navigate(Screen.MAP, device)


def scan_test_squares(device):
    """Quick scan of just the 4 corner squares to calibrate OCR region.

    Run this first to verify coordinate reading works before doing a full scan.
    Screenshots saved to debug/territory_coords/ for visual inspection.
    """
    corners = [(0, 0), (0, 23), (23, 0), (23, 23)]
    scan_territory_coordinates(device, squares=corners, save_screenshots=True)
