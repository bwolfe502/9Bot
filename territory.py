import cv2
import json
import numpy as np
import os
import time
import random

import config
from config import (SQUARE_SIZE, GRID_OFFSET_X, GRID_OFFSET_Y,
                    GRID_WIDTH, GRID_HEIGHT, THRONE_SQUARES, BORDER_COLORS, Screen)
from vision import (load_screenshot, tap_image, wait_for_image_and_tap,
                    find_image, adb_tap, adb_keyevent, get_template,
                    save_failure_screenshot)
from navigation import navigate
from troops import troops_avail, all_troops_home, heal_all
from actions import teleport
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


def _classify_square_team(bgr, device=None):
    """Determine team based on border color — find closest Euclidean match.

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

    min_distance = float('inf')
    best_team = "unknown"

    distances = {}
    for team, (target_b, target_g, target_r) in BORDER_COLORS.items():
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
# TERRITORY SQUARE MANAGER GUI
# ============================================================

def open_territory_manager(device):
    """Open a visual interface to manually select squares to attack or ignore"""
    import tkinter as tk
    import customtkinter as ctk
    from PIL import Image, ImageTk

    log = get_logger("territory", device)

    # Take a screenshot of territory screen
    if not navigate(Screen.TERRITORY, device):
        log.warning("Failed to navigate to territory screen")
        return

    time.sleep(1)
    full_image = load_screenshot(device)

    if full_image is None:
        log.error("Failed to load screenshot")
        return

    # Crop to just the grid area with small padding
    grid_pixel_width = int(GRID_WIDTH * SQUARE_SIZE)
    grid_pixel_height = int(GRID_HEIGHT * SQUARE_SIZE)
    padding = 10

    crop_x1 = max(0, GRID_OFFSET_X - padding)
    crop_y1 = max(0, GRID_OFFSET_Y - padding)
    crop_x2 = min(full_image.shape[1], GRID_OFFSET_X + grid_pixel_width + padding)
    crop_y2 = min(full_image.shape[0], GRID_OFFSET_Y + grid_pixel_height + padding)

    image = full_image[crop_y1:crop_y2, crop_x1:crop_x2]

    # Adjust offsets for cropped image
    adjusted_offset_x = GRID_OFFSET_X - crop_x1
    adjusted_offset_y = GRID_OFFSET_Y - crop_y1

    # Create manager window
    manager = ctk.CTkToplevel()
    manager.title(f"Territory Square Manager - {device}")
    manager.configure(fg_color="#0c0c18")

    # Instructions
    ctk.CTkLabel(
        manager,
        text="Click squares: GREEN = Force Attack | RED = Ignore | None = Auto",
        font=ctk.CTkFont(family="Segoe UI", size=11),
        text_color="#e0e0f0", fg_color="#14142a",
        corner_radius=6, height=30
    ).pack(fill=tk.X, padx=6, pady=(6, 2))

    # Stats display
    stats_var = tk.StringVar()
    stats_var.set(f"Attack: {len(config.MANUAL_ATTACK_SQUARES)} | Ignore: {len(config.MANUAL_IGNORE_SQUARES)}")
    ctk.CTkLabel(manager, textvariable=stats_var,
                 font=ctk.CTkFont(family="Segoe UI", size=10),
                 text_color="#8899aa").pack(pady=2)

    # Create canvas with the territory image
    canvas_frame = ctk.CTkFrame(manager, fg_color="transparent")
    canvas_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    # Scale down to 0.75x
    display_scale = 0.75
    display_image = cv2.resize(image, None, fx=display_scale, fy=display_scale, interpolation=cv2.INTER_LINEAR)
    display_image = cv2.cvtColor(display_image, cv2.COLOR_BGR2RGB)

    # Convert to PhotoImage
    pil_image = Image.fromarray(display_image)
    photo = ImageTk.PhotoImage(pil_image)

    # Set window size based on image
    window_width = display_image.shape[1] + 20
    window_height = display_image.shape[0] + 120
    manager.geometry(f"{window_width}x{window_height}")

    canvas = tk.Canvas(
        canvas_frame,
        width=display_image.shape[1],
        height=display_image.shape[0],
        highlightthickness=0
    )
    canvas.pack()

    # Set background image
    canvas.create_image(0, 0, anchor=tk.NW, image=photo)
    canvas.image = photo  # Keep a reference

    # Draw grid overlay - store items in list for faster deletion
    overlay_items = []

    def draw_overlay():
        """Draw colored overlays for manual selections"""
        nonlocal overlay_items

        # Clear existing overlays
        for item_id in overlay_items:
            canvas.delete(item_id)
        overlay_items.clear()

        for row in range(GRID_HEIGHT):
            for col in range(GRID_WIDTH):
                if (row, col) in THRONE_SQUARES:
                    continue

                x = int((adjusted_offset_x + col * SQUARE_SIZE) * display_scale)
                y = int((adjusted_offset_y + row * SQUARE_SIZE) * display_scale)
                w = int(SQUARE_SIZE * display_scale)
                h = int(SQUARE_SIZE * display_scale)

                color = None
                if (row, col) in config.MANUAL_ATTACK_SQUARES:
                    color = "green"
                elif (row, col) in config.MANUAL_IGNORE_SQUARES:
                    color = "red"

                if color:
                    rect_id = canvas.create_rectangle(
                        x, y, x + w, y + h,
                        outline=color,
                        width=2,
                        fill=color,
                        stipple="gray50"
                    )
                    overlay_items.append(rect_id)

    def on_canvas_click(event):
        """Handle clicks on the canvas"""
        # Convert click to grid position
        click_x = event.x / display_scale
        click_y = event.y / display_scale

        col = int((click_x - adjusted_offset_x) / SQUARE_SIZE)
        row = int((click_y - adjusted_offset_y) / SQUARE_SIZE)

        # Validate bounds
        if not (0 <= row < GRID_HEIGHT and 0 <= col < GRID_WIDTH):
            return

        if (row, col) in THRONE_SQUARES:
            _log.debug("Cannot select throne square (%d, %d)", row, col)
            return

        # Toggle state: None -> Attack -> Ignore -> None
        if (row, col) in config.MANUAL_ATTACK_SQUARES:
            config.MANUAL_ATTACK_SQUARES.remove((row, col))
            config.MANUAL_IGNORE_SQUARES.add((row, col))
            _log.debug("Square (%d, %d) set to IGNORE", row, col)
        elif (row, col) in config.MANUAL_IGNORE_SQUARES:
            config.MANUAL_IGNORE_SQUARES.remove((row, col))
            _log.debug("Square (%d, %d) set to AUTO-DETECT", row, col)
        else:
            config.MANUAL_ATTACK_SQUARES.add((row, col))
            _log.debug("Square (%d, %d) set to FORCE ATTACK", row, col)

        # Update display
        draw_overlay()
        stats_var.set(f"Attack: {len(config.MANUAL_ATTACK_SQUARES)} | Ignore: {len(config.MANUAL_IGNORE_SQUARES)}")

    canvas.bind("<Button-1>", on_canvas_click)

    # Buttons
    button_frame = ctk.CTkFrame(manager, fg_color="transparent")
    button_frame.pack(pady=5)

    def clear_all():
        config.MANUAL_ATTACK_SQUARES.clear()
        config.MANUAL_IGNORE_SQUARES.clear()
        draw_overlay()
        stats_var.set(f"Attack: {len(config.MANUAL_ATTACK_SQUARES)} | Ignore: {len(config.MANUAL_IGNORE_SQUARES)}")
        _log.debug("Cleared all manual selections")

    def clear_attack():
        config.MANUAL_ATTACK_SQUARES.clear()
        draw_overlay()
        stats_var.set(f"Attack: {len(config.MANUAL_ATTACK_SQUARES)} | Ignore: {len(config.MANUAL_IGNORE_SQUARES)}")
        _log.debug("Cleared manual attack selections")

    def clear_ignore():
        config.MANUAL_IGNORE_SQUARES.clear()
        draw_overlay()
        stats_var.set(f"Attack: {len(config.MANUAL_ATTACK_SQUARES)} | Ignore: {len(config.MANUAL_IGNORE_SQUARES)}")
        _log.debug("Cleared manual ignore selections")

    _btn_kw = dict(font=ctk.CTkFont(family="Segoe UI", size=10),
                   fg_color="#1e3a5f", hover_color="#1a3a4a",
                   text_color="#e0e0f0", corner_radius=8, height=28, width=90)
    ctk.CTkButton(button_frame, text="Clear All", command=clear_all,
                  fg_color="#c62828", hover_color="#d32f2f",
                  font=ctk.CTkFont(family="Segoe UI", size=10),
                  text_color="#ffffff", corner_radius=8, height=28, width=90).pack(side=tk.LEFT, padx=2)
    ctk.CTkButton(button_frame, text="Clear Attack", command=clear_attack, **_btn_kw).pack(side=tk.LEFT, padx=2)
    ctk.CTkButton(button_frame, text="Clear Ignore", command=clear_ignore, **_btn_kw).pack(side=tk.LEFT, padx=2)
    ctk.CTkButton(button_frame, text="Close", command=manager.destroy, **_btn_kw).pack(side=tk.LEFT, padx=2)

    # Draw initial overlay
    draw_overlay()

    # Just destroy on close
    manager.protocol("WM_DELETE_WINDOW", manager.destroy)

# ============================================================
# TERRITORY ATTACK SYSTEM
# ============================================================

def scan_targets(device):
    """Scan the territory grid and return prioritized target lists.

    Must be called while on the TERRITORY screen.

    Returns dict with keys:
        'unflagged_enemies': [(row, col), ...] — unflagged enemy squares adjacent to own territory
        'flagged_enemies':   [(row, col), ...] — flagged enemy squares adjacent to own territory
        'friendly':          [(row, col), ...] — own team squares adjacent to enemy territory (for reinforcement)
        'image':             np.ndarray — the screenshot used for analysis
    Returns None if screenshot fails.
    """
    log = get_logger("territory", device)
    image = load_screenshot(device)
    if image is None:
        log.error("scan_targets: failed to load screenshot")
        return None

    my_team = config.get_device_config(device, "my_team")
    enemy_teams = config.get_device_enemy_teams(device)
    log.debug("Scanning grid — my_team=%s, enemies=%s", my_team, enemy_teams)

    unflagged_enemies = []
    flagged_enemies = []
    friendly_reinforce = []

    for row in range(GRID_HEIGHT):
        for col in range(GRID_WIDTH):
            if (row, col) in THRONE_SQUARES:
                continue
            if (row, col) in config.MANUAL_IGNORE_SQUARES:
                continue

            border_color = _get_border_color(image, row, col)
            team = _classify_square_team(border_color, device=device)

            if team in enemy_teams:
                if _is_adjacent_to_my_territory(image, row, col, device=device):
                    if _has_flag(image, row, col):
                        flagged_enemies.append((row, col))
                    else:
                        unflagged_enemies.append((row, col))

            elif team == my_team:
                # Check if adjacent to enemy territory (frontline — worth reinforcing)
                neighbors = [(row-1, col), (row+1, col), (row, col-1), (row, col+1)]
                for nr, nc in neighbors:
                    if not (0 <= nr < GRID_HEIGHT and 0 <= nc < GRID_WIDTH):
                        continue
                    if (nr, nc) in THRONE_SQUARES:
                        continue
                    nb_color = _get_border_color(image, nr, nc)
                    nb_team = _classify_square_team(nb_color, device=device)
                    if nb_team in enemy_teams:
                        friendly_reinforce.append((row, col))
                        break

    # Manual attack overrides replace auto-detected targets entirely
    if config.MANUAL_ATTACK_SQUARES:
        unflagged_enemies = list(config.MANUAL_ATTACK_SQUARES)
        flagged_enemies = []
        log.info("Using ONLY manual attack squares (%d)", len(unflagged_enemies))

    log.info("Scan: %d unflagged enemies, %d flagged, %d friendly frontline",
             len(unflagged_enemies), len(flagged_enemies), len(friendly_reinforce))

    return {
        "unflagged_enemies": unflagged_enemies,
        "flagged_enemies": flagged_enemies,
        "friendly": friendly_reinforce,
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


@timed_action("attack_territory")
def attack_territory(device, debug=False):
    """Scan territory grid and pick a target.

    Returns (row, col, action_type) on success, or None if no targets.
    action_type is "attack" or "reinforce".

    Side effect: stores target in config.LAST_ATTACKED_SQUARE[device]
    and taps the target square on the territory grid (camera trick).
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

    target = _pick_target(scan)
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
        "reinforce" — reinforce_button.png found (empty or friendly tower)
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

        if find_image(screen, "reinforce_button.png", threshold=0.7):
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
        if not tap_image("reinforce_button.png", device, threshold=0.7):
            log.warning("reinforce_button.png not found for tap")
            return False

    time.sleep(1)

    # Try depart
    if wait_for_image_and_tap("depart.png", device, timeout=5):
        log.info("Depart tapped (%s)", action_type)
        return True

    # Depart Anyway fallback (low health troops)
    if config.get_device_config(device, "auto_heal"):
        log.info("Depart not found — healing first, then retrying")
        heal_all(device)
        time.sleep(1)
        if wait_for_image_and_tap("depart.png", device, timeout=3):
            log.info("Depart tapped after heal")
            return True

    if wait_for_image_and_tap("depart_anyway.png", device, timeout=3):
        log.warning("Used Depart Anyway fallback")
        return True

    log.warning("Depart failed — neither depart.png nor depart_anyway.png found")
    save_failure_screenshot(device, "occupy_depart_fail")
    return False


@timed_action("auto_occupy")
def auto_occupy_loop(device, stop_check):
    """Auto occupy loop — per-device, cooperative stop via stop_check callback.

    Cycle:
        1. Scan territory grid for targets (priority: unflagged > flagged > reinforce)
        2. Tap target square on grid (camera trick — centers camera on tower)
        3. Teleport near the tower
        4. Navigate back to territory, tap target again (re-centers camera)
        5. Tap tower → detect menu (attack or reinforce) → depart
        6. Wait for troops → repeat

    Handles: death recovery, navigation failures, teleport failures,
    menu detection failures, depart_anyway fallback.
    """
    log = get_logger("territory", device)
    log.info("Auto occupy started")

    consecutive_tp_fails = 0
    _MAX_CONSECUTIVE_TP_FAILS = 3
    _MAX_TOTAL_TP_FAILS = 5

    while not stop_check():
        try:
            # --- Death check at start of each cycle ---
            revive_result = _check_and_revive(device, log, stop_check)
            if revive_result is None:
                break  # Stopped
            if stop_check():
                break

            # --- Wait for troops ---
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
                log.debug("Troops not home, waiting 10s...")
                if _interruptible_sleep(10, stop_check):
                    break
                continue

            if stop_check():
                break

            log.info("=== Starting auto occupy cycle ===")

            # --- Step 1: Scan territory and pick target ---
            config.set_device_status(device, "Scanning Territory...")
            target_result = attack_territory(device)

            if target_result is None:
                log.warning("No targets found — waiting 30s before rescan")
                config.set_device_status(device, "No Targets...")
                if _interruptible_sleep(30, stop_check):
                    break
                continue

            target_row, target_col, action_type = target_result
            config.set_device_status(
                device,
                f"Targeting ({target_row},{target_col})..."
            )

            if stop_check():
                break
            time.sleep(2)  # Let camera settle after territory grid tap

            # --- Step 2: Teleport near the target ---
            # The territory grid tap already centered camera on the tower.
            # Now we're on MAP screen (tapping grid square switches to MAP).
            # The camera trick: tap the tower to open info → camera pans down
            # → close dialog → teleport from the better position below tower.
            config.set_device_status(device, "Teleporting...")
            if not teleport(device):
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

            consecutive_tp_fails = 0  # Reset on success

            if stop_check():
                break
            time.sleep(2)

            # --- Death check after teleport ---
            revive_result = _check_and_revive(device, log, stop_check)
            if revive_result is None:
                break
            if revive_result:
                continue  # Got killed during teleport — restart with new target

            if stop_check():
                break

            # --- Step 3: Navigate back to territory, re-tap target square ---
            config.set_device_status(
                device,
                f"{'Attacking' if action_type == 'attack' else 'Reinforcing'} Tower..."
            )

            if not navigate(Screen.TERRITORY, device):
                log.warning("Cannot reach TERRITORY screen for tower interaction")
                # Try BACK key recovery
                adb_keyevent(device, 4)
                time.sleep(2)
                if not navigate(Screen.TERRITORY, device):
                    log.warning("TERRITORY nav failed twice — skipping cycle")
                    if _interruptible_sleep(10, stop_check):
                        break
                    continue

            if stop_check():
                break
            time.sleep(1)

            # Re-tap the target square to center camera on tower again
            click_x, click_y = _get_square_center(target_row, target_col)
            log.debug("Re-tapping square (%d, %d) at (%d, %d)",
                      target_row, target_col, click_x, click_y)
            adb_tap(device, click_x, click_y)
            time.sleep(2)  # Camera settles on MAP after grid tap

            if stop_check():
                break

            # --- Step 4: Tap tower to open menu ---
            menu_type = _tap_tower_and_detect_menu(device, log, timeout=10)

            if menu_type is None:
                log.warning("Tower menu did not open for (%d, %d) — skipping",
                            target_row, target_col)
                save_failure_screenshot(device, "occupy_tower_menu_fail")
                if _interruptible_sleep(5, stop_check):
                    break
                continue

            # Decide what to do based on menu vs what we expected
            actual_action = menu_type  # "attack" or "reinforce"
            if actual_action != action_type:
                log.info("Expected %s but got %s — proceeding with %s",
                         action_type, actual_action, actual_action)

            if stop_check():
                break

            # --- Step 5: Depart ---
            config.set_device_status(device, "Deploying...")
            troops = troops_avail(device)
            min_troops = config.get_device_config(device, "min_troops")

            if troops <= min_troops:
                log.warning("Not enough troops (have %d, need >%d) — closing menu",
                            troops, min_troops)
                adb_keyevent(device, 4)  # BACK to close menu
                time.sleep(1)
                if _interruptible_sleep(10, stop_check):
                    break
                continue

            if not _do_depart(device, log, actual_action):
                log.warning("Depart failed — skipping cycle")
                adb_keyevent(device, 4)  # Try to close any open menu
                time.sleep(1)
                if _interruptible_sleep(5, stop_check):
                    break
                continue

            log.info("Cycle complete — deployed to (%d, %d) via %s",
                     target_row, target_col, actual_action)

            # --- Wait before next cycle ---
            config.set_device_status(device, "Waiting for Troops...")
            if _interruptible_sleep(10, stop_check):
                break

        except Exception as e:
            log.error("Error in auto occupy loop: %s", e, exc_info=True)
            save_failure_screenshot(device, "occupy_exception")
            if _interruptible_sleep(10, stop_check):
                break

    log.info("Auto occupy stopped")

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
            team = _classify_square_team(bgr, device=device)
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
