"""Tests for mithril mining (actions/farming.py).

Covers: _is_mine_occupied, mine_mithril, mine_mithril_if_due, _set_gather_level.
All ADB and vision calls are mocked — no emulator needed.
"""

import time
import numpy as np
from unittest.mock import patch, MagicMock

import config
from config import Screen
from actions.farming import (
    _is_mine_occupied,
    mine_mithril,
    mine_mithril_if_due,
    _set_gather_level,
    _MITHRIL_MINES,
    _MITHRIL_SLOTS_X,
    _MITHRIL_SLOT_Y,
    _OCCUPIED_RED_THRESHOLD,
)


# ============================================================
# _is_mine_occupied
# ============================================================

class TestIsMineOccupied:
    def test_no_red_pixels_not_occupied(self):
        """All-black screen → mine is not occupied."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        assert _is_mine_occupied(screen, 540, 820) is False

    def test_red_pixels_above_threshold(self):
        """Enough bright red pixels above mine → occupied."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        # Paint red pixels in the region above the mine (BGR: R=channel 2)
        mine_x, mine_y = 540, 820
        y1 = mine_y - 180
        y2 = mine_y - 60
        x1 = mine_x - 80
        x2 = mine_x + 80
        # Set pixels: B=0, G=0, R=255
        screen[y1:y2, x1:x2, 0] = 0    # B
        screen[y1:y2, x1:x2, 1] = 0    # G
        screen[y1:y2, x1:x2, 2] = 255  # R
        assert _is_mine_occupied(screen, mine_x, mine_y) is True

    def test_red_pixels_below_threshold(self):
        """Few red pixels → not occupied."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        mine_x, mine_y = 540, 820
        # Paint just a few red pixels (below threshold)
        y = mine_y - 120
        for i in range(5):  # Only 5 pixels
            screen[y, mine_x + i, 2] = 255
        assert _is_mine_occupied(screen, mine_x, mine_y) is False

    def test_green_pixels_not_counted(self):
        """Green pixels (G>100) should not count as red."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        mine_x, mine_y = 540, 820
        y1 = mine_y - 180
        y2 = mine_y - 60
        x1 = mine_x - 80
        x2 = mine_x + 80
        screen[y1:y2, x1:x2, 1] = 200  # G=200 (fails G<100 check)
        screen[y1:y2, x1:x2, 2] = 255  # R=255
        assert _is_mine_occupied(screen, mine_x, mine_y) is False

    def test_mine_at_edge_of_screen(self):
        """Mine near screen edge → region clamped, no crash."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        # Mine very close to top — occupied check region would go negative
        assert _is_mine_occupied(screen, 100, 50) is False

    def test_empty_region_not_occupied(self):
        """If region size is 0 (mine position nonsensical), returns False."""
        screen = np.zeros((10, 10, 3), dtype=np.uint8)
        assert _is_mine_occupied(screen, 5, 5) is False


# ============================================================
# _set_gather_level
# ============================================================

class TestSetGatherLevel:
    def test_level_1_only_minus_taps(self, mock_device):
        """Level 1 = 5 minus taps (floor) + 0 plus taps."""
        with patch("actions.farming.logged_tap") as mock_tap, \
             patch("actions.farming.time.sleep"):
            _set_gather_level(mock_device, 1)
        # 5 minus taps to floor + 0 plus taps
        minus_calls = [c for c in mock_tap.call_args_list
                       if c[0][3] == "gather_minus_reset"]
        plus_calls = [c for c in mock_tap.call_args_list
                      if c[0][3] == "gather_plus_set"]
        assert len(minus_calls) == 5
        assert len(plus_calls) == 0

    def test_level_6_full_range(self, mock_device):
        """Level 6 = 5 minus taps (floor) + 5 plus taps."""
        with patch("actions.farming.logged_tap") as mock_tap, \
             patch("actions.farming.time.sleep"):
            _set_gather_level(mock_device, 6)
        minus_calls = [c for c in mock_tap.call_args_list
                       if c[0][3] == "gather_minus_reset"]
        plus_calls = [c for c in mock_tap.call_args_list
                      if c[0][3] == "gather_plus_set"]
        assert len(minus_calls) == 5
        assert len(plus_calls) == 5

    def test_level_3_intermediate(self, mock_device):
        """Level 3 = 5 minus + 2 plus."""
        with patch("actions.farming.logged_tap") as mock_tap, \
             patch("actions.farming.time.sleep"):
            _set_gather_level(mock_device, 3)
        plus_calls = [c for c in mock_tap.call_args_list
                      if c[0][3] == "gather_plus_set"]
        assert len(plus_calls) == 2


# ============================================================
# mine_mithril
# ============================================================

class TestMineMithril:
    def test_nav_fails_returns_false(self, mock_device):
        """Can't navigate to kingdom → return False."""
        with patch("actions.farming.navigate", return_value=False):
            result = mine_mithril(mock_device)
        assert result is False

    def test_stop_check_before_scroll(self, mock_device):
        """stop_check=True after navigation → returns False."""
        with patch("actions.farming.navigate", return_value=True):
            result = mine_mithril(mock_device, stop_check=lambda: True)
        assert result is False

    def test_stop_check_disables_device(self, mock_device):
        """Device removed from MITHRIL_ENABLED_DEVICES mid-mine → stops."""
        config.MITHRIL_ENABLED_DEVICES.discard(mock_device)
        with patch("actions.farming.navigate", return_value=True):
            result = mine_mithril(mock_device)
        assert result is False

    def test_deploys_to_safe_mines(self, mock_device):
        """Full mithril cycle: recall → deploy to safe mines → navigate back."""
        config.MITHRIL_ENABLED_DEVICES.add(mock_device)
        config.DEVICE_TOTAL_TROOPS[mock_device] = 3
        config.LAST_MITHRIL_TIME.pop(mock_device, None)
        config.MITHRIL_DEPLOY_TIME.pop(mock_device, None)

        # Create screen with NO red pixels (all mines safe)
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)

        with patch("actions.farming.navigate", return_value=True), \
             patch("actions.farming.adb_swipe"), \
             patch("actions.farming.adb_tap"), \
             patch("actions.farming.logged_tap"), \
             patch("actions.farming.timed_wait"), \
             patch("actions.farming.wait_for_image_and_tap") as mock_wfit, \
             patch("actions.farming.tap_image"), \
             patch("actions.farming.load_screenshot", return_value=screen), \
             patch("actions.farming.save_failure_screenshot"), \
             patch("actions.farming._save_mithril_times"), \
             patch("actions.farming.time.sleep"), \
             patch("actions.farming.time.time", return_value=1000.0):
            # Return True for mithril_return (recall), mithril_attack, mithril_depart
            mock_wfit.return_value = True
            result = mine_mithril(mock_device)

        assert result is True
        assert mock_device in config.LAST_MITHRIL_TIME

        # Cleanup
        config.MITHRIL_ENABLED_DEVICES.discard(mock_device)
        config.DEVICE_TOTAL_TROOPS.pop(mock_device, None)
        config.LAST_MITHRIL_TIME.pop(mock_device, None)

    def test_skips_occupied_mines(self, mock_device):
        """Occupied mines should be skipped (red pixels detected)."""
        config.MITHRIL_ENABLED_DEVICES.add(mock_device)
        config.DEVICE_TOTAL_TROOPS[mock_device] = 2

        # Create screen with ALL mines occupied (solid red above each mine)
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        for mine_x, mine_y in _MITHRIL_MINES:
            y1 = max(0, mine_y - 180)
            y2 = min(screen.shape[0], mine_y - 60)
            x1 = max(0, mine_x - 80)
            x2 = min(screen.shape[1], mine_x + 80)
            screen[y1:y2, x1:x2, 2] = 255  # R channel

        deploy_count = [0]
        original_wfit = MagicMock(return_value=True)
        def mock_wfit(name, device, **kwargs):
            if name == "mithril_attack.png":
                deploy_count[0] += 1
            return True

        with patch("actions.farming.navigate", return_value=True), \
             patch("actions.farming.adb_swipe"), \
             patch("actions.farming.adb_tap"), \
             patch("actions.farming.logged_tap"), \
             patch("actions.farming.timed_wait"), \
             patch("actions.farming.wait_for_image_and_tap", side_effect=mock_wfit), \
             patch("actions.farming.tap_image"), \
             patch("actions.farming.load_screenshot", return_value=screen), \
             patch("actions.farming.save_failure_screenshot"), \
             patch("actions.farming._save_mithril_times"), \
             patch("actions.farming.time.sleep"), \
             patch("actions.farming.time.time", return_value=1000.0):
            mine_mithril(mock_device)

        # mithril_attack should never be tapped since all mines are occupied
        # (the SEARCH refresh may find new mines, but the mock screen stays occupied)
        # So deploy_count should stay at 0 for each page
        # Note: some taps still happen (recall slots), but not mithril_attack
        assert deploy_count[0] == 0

        # Cleanup
        config.MITHRIL_ENABLED_DEVICES.discard(mock_device)
        config.DEVICE_TOTAL_TROOPS.pop(mock_device, None)


# ============================================================
# mine_mithril_if_due
# ============================================================

class TestMineMithrilIfDue:
    def test_not_enabled_does_nothing(self, mock_device):
        """Device not in MITHRIL_ENABLED_DEVICES → no action."""
        config.MITHRIL_ENABLED_DEVICES.discard(mock_device)
        with patch("actions.farming.mine_mithril") as mock_mine:
            mine_mithril_if_due(mock_device)
        mock_mine.assert_not_called()

    def test_not_due_does_nothing(self, mock_device):
        """Interval not elapsed → no action."""
        config.MITHRIL_ENABLED_DEVICES.add(mock_device)
        config.LAST_MITHRIL_TIME[mock_device] = time.time()  # just now
        with patch("actions.farming.mine_mithril") as mock_mine, \
             patch("actions.farming.config") as mock_cfg:
            mock_cfg.MITHRIL_ENABLED_DEVICES = config.MITHRIL_ENABLED_DEVICES
            mock_cfg.LAST_MITHRIL_TIME = config.LAST_MITHRIL_TIME
            mock_cfg.get_device_config.return_value = 60  # 60 min interval
            mine_mithril_if_due(mock_device)
        mock_mine.assert_not_called()

        # Cleanup
        config.MITHRIL_ENABLED_DEVICES.discard(mock_device)
        config.LAST_MITHRIL_TIME.pop(mock_device, None)

    def test_due_triggers_mining(self, mock_device):
        """Interval elapsed → mine_mithril called."""
        config.MITHRIL_ENABLED_DEVICES.add(mock_device)
        config.LAST_MITHRIL_TIME[mock_device] = time.time() - 7200  # 2 hours ago
        with patch("actions.farming.mine_mithril") as mock_mine, \
             patch("actions.farming.config") as mock_cfg:
            mock_cfg.MITHRIL_ENABLED_DEVICES = config.MITHRIL_ENABLED_DEVICES
            mock_cfg.LAST_MITHRIL_TIME = config.LAST_MITHRIL_TIME
            mock_cfg.get_device_config.return_value = 60  # 60 min interval
            mine_mithril_if_due(mock_device)
        mock_mine.assert_called_once()

        # Cleanup
        config.MITHRIL_ENABLED_DEVICES.discard(mock_device)
        config.LAST_MITHRIL_TIME.pop(mock_device, None)

    def test_never_mined_triggers_immediately(self, mock_device):
        """No previous mithril time → interval check passes (0 → elapsed=now)."""
        config.MITHRIL_ENABLED_DEVICES.add(mock_device)
        config.LAST_MITHRIL_TIME.pop(mock_device, None)
        with patch("actions.farming.mine_mithril") as mock_mine, \
             patch("actions.farming.config") as mock_cfg:
            mock_cfg.MITHRIL_ENABLED_DEVICES = config.MITHRIL_ENABLED_DEVICES
            mock_cfg.LAST_MITHRIL_TIME = config.LAST_MITHRIL_TIME
            mock_cfg.get_device_config.return_value = 60
            mine_mithril_if_due(mock_device)
        mock_mine.assert_called_once()

        # Cleanup
        config.MITHRIL_ENABLED_DEVICES.discard(mock_device)


# ============================================================
# Mithril constants sanity checks
# ============================================================

class TestMithrilConstants:
    def test_6_mine_positions(self):
        assert len(_MITHRIL_MINES) == 6

    def test_5_slot_positions(self):
        assert len(_MITHRIL_SLOTS_X) == 5

    def test_mines_within_screen(self):
        for i, (x, y) in enumerate(_MITHRIL_MINES):
            assert 0 <= x <= 1080, f"Mine {i+1} x={x} out of bounds"
            assert 0 <= y <= 1920, f"Mine {i+1} y={y} out of bounds"

    def test_slots_within_screen(self):
        for i, x in enumerate(_MITHRIL_SLOTS_X):
            assert 0 <= x <= 1080, f"Slot {i+1} x={x} out of bounds"
        assert 0 <= _MITHRIL_SLOT_Y <= 1920
