"""Tests for titan rally and AP restoration (actions/titans.py).

Covers: _close_ap_menu, _read_ap_from_menu, _read_gem_cost,
        _restore_ap_from_open_menu, restore_ap, rally_titan.
All ADB and vision calls are mocked — no emulator needed.
"""

import time
import numpy as np
from unittest.mock import patch, MagicMock, call

import config
from config import Screen
from actions.titans import (
    _close_ap_menu,
    _read_ap_from_menu,
    _read_gem_cost,
    _restore_ap_from_open_menu,
    restore_ap,
    rally_titan,
    _MAX_TITAN_SEARCH_ATTEMPTS,
    _AP_POTIONS_SMALL,
    _AP_POTIONS_LARGE,
    _AP_GEM_BUTTON,
    _AP_GEM_CONFIRM,
    _AP_FREE_OPEN,
)


# ============================================================
# _close_ap_menu
# ============================================================

class TestCloseApMenu:
    def test_double_close_taps_twice(self, mock_device):
        """double_close=True closes AP Recovery menu + search menu behind it."""
        with patch("actions.titans.tap_image") as mock_tap, \
             patch("actions.titans.time.sleep"):
            _close_ap_menu(mock_device, double_close=True)
        assert mock_tap.call_count == 2
        mock_tap.assert_any_call("close_x.png", mock_device)

    def test_single_close_taps_once(self, mock_device):
        """double_close=False only closes the AP Recovery popup."""
        with patch("actions.titans.tap_image") as mock_tap, \
             patch("actions.titans.time.sleep"):
            _close_ap_menu(mock_device, double_close=False)
        assert mock_tap.call_count == 1
        mock_tap.assert_called_once_with("close_x.png", mock_device)

    def test_default_is_double_close(self, mock_device):
        """Default argument should be double_close=True."""
        with patch("actions.titans.tap_image") as mock_tap, \
             patch("actions.titans.time.sleep"):
            _close_ap_menu(mock_device)
        assert mock_tap.call_count == 2


# ============================================================
# _read_ap_from_menu
# ============================================================

class TestReadApFromMenu:
    def _make_screen(self):
        return np.zeros((1920, 1080, 3), dtype=np.uint8)

    def test_returns_tuple_on_valid_ocr(self, mock_device):
        """OCR reads '136/400' → returns (136, 400)."""
        with patch("actions.titans.load_screenshot", return_value=self._make_screen()), \
             patch("actions.titans.cv2.cvtColor", return_value=np.zeros((60, 390), dtype=np.uint8)), \
             patch("actions.titans.cv2.resize", return_value=np.zeros((120, 780), dtype=np.uint8)), \
             patch("actions.titans.cv2.threshold", return_value=(None, np.zeros((120, 780), dtype=np.uint8))), \
             patch("actions.titans.cv2.imwrite"), \
             patch("vision.ocr_read", return_value=["136/400"]):
            result = _read_ap_from_menu(mock_device)
        assert result == (136, 400)

    def test_returns_none_on_screenshot_failure(self, mock_device):
        with patch("actions.titans.load_screenshot", return_value=None):
            assert _read_ap_from_menu(mock_device) is None

    def test_returns_none_on_no_pattern(self, mock_device):
        """OCR reads junk text → returns None."""
        with patch("actions.titans.load_screenshot", return_value=self._make_screen()), \
             patch("actions.titans.cv2.cvtColor", return_value=np.zeros((60, 390), dtype=np.uint8)), \
             patch("actions.titans.cv2.resize", return_value=np.zeros((120, 780), dtype=np.uint8)), \
             patch("actions.titans.cv2.threshold", return_value=(None, np.zeros((120, 780), dtype=np.uint8))), \
             patch("actions.titans.cv2.imwrite"), \
             patch("vision.ocr_read", return_value=["no match here"]):
            assert _read_ap_from_menu(mock_device) is None

    def test_parses_spaces_in_numbers(self, mock_device):
        """OCR reads '1 36 / 400' → regex still matches '136/400' style."""
        with patch("actions.titans.load_screenshot", return_value=self._make_screen()), \
             patch("actions.titans.cv2.cvtColor", return_value=np.zeros((60, 390), dtype=np.uint8)), \
             patch("actions.titans.cv2.resize", return_value=np.zeros((120, 780), dtype=np.uint8)), \
             patch("actions.titans.cv2.threshold", return_value=(None, np.zeros((120, 780), dtype=np.uint8))), \
             patch("actions.titans.cv2.imwrite"), \
             patch("vision.ocr_read", return_value=["50/400"]):
            result = _read_ap_from_menu(mock_device)
        assert result == (50, 400)


# ============================================================
# _read_gem_cost
# ============================================================

class TestReadGemCost:
    def _make_screen(self):
        return np.zeros((1920, 1080, 3), dtype=np.uint8)

    def test_reads_gem_cost(self, mock_device):
        """OCR reads 'Spend 50 Gems?' → returns 50."""
        with patch("actions.titans.load_screenshot", return_value=self._make_screen()), \
             patch("actions.titans.cv2.cvtColor", return_value=np.zeros((150, 650), dtype=np.uint8)), \
             patch("actions.titans.cv2.resize", return_value=np.zeros((300, 1300), dtype=np.uint8)), \
             patch("vision.ocr_read", return_value=["Spend 50 Gems?"]):
            assert _read_gem_cost(mock_device) == 50

    def test_reads_large_gem_cost(self, mock_device):
        """OCR reads 'Spend 3,500 Gems' → returns 3500."""
        with patch("actions.titans.load_screenshot", return_value=self._make_screen()), \
             patch("actions.titans.cv2.cvtColor", return_value=np.zeros((150, 650), dtype=np.uint8)), \
             patch("actions.titans.cv2.resize", return_value=np.zeros((300, 1300), dtype=np.uint8)), \
             patch("vision.ocr_read", return_value=["Spend 3,500 Gems"]):
            assert _read_gem_cost(mock_device) == 3500

    def test_returns_none_on_screenshot_failure(self, mock_device):
        with patch("actions.titans.load_screenshot", return_value=None):
            assert _read_gem_cost(mock_device) is None

    def test_fallback_to_any_number(self, mock_device):
        """If 'Gem' not found, falls back to first number."""
        with patch("actions.titans.load_screenshot", return_value=self._make_screen()), \
             patch("actions.titans.cv2.cvtColor", return_value=np.zeros((150, 650), dtype=np.uint8)), \
             patch("actions.titans.cv2.resize", return_value=np.zeros((300, 1300), dtype=np.uint8)), \
             patch("vision.ocr_read", return_value=["Cost: 200"]):
            assert _read_gem_cost(mock_device) == 200

    def test_returns_none_on_no_numbers(self, mock_device):
        with patch("actions.titans.load_screenshot", return_value=self._make_screen()), \
             patch("actions.titans.cv2.cvtColor", return_value=np.zeros((150, 650), dtype=np.uint8)), \
             patch("actions.titans.cv2.resize", return_value=np.zeros((300, 1300), dtype=np.uint8)), \
             patch("vision.ocr_read", return_value=["no numbers"]):
            assert _read_gem_cost(mock_device) is None


# ============================================================
# _restore_ap_from_open_menu
# ============================================================

class TestRestoreApFromOpenMenu:
    """Tests for the AP restoration logic on an already-open menu."""

    def _mock_config(self, **overrides):
        """Create a mock config with sensible defaults."""
        defaults = {
            "ap_use_free": True,
            "ap_use_potions": True,
            "ap_allow_large_potions": False,
            "ap_use_gems": False,
            "ap_gem_limit": 0,
        }
        defaults.update(overrides)
        mock_cfg = MagicMock()
        mock_cfg.get_device_config.side_effect = lambda dev, key: defaults.get(key, False)
        return mock_cfg

    def test_already_enough_ap(self, mock_device):
        """If current AP >= needed, return success immediately."""
        with patch("actions.titans._read_ap_from_menu", return_value=(100, 400)), \
             patch("actions.titans.config", self._mock_config()):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is True
        assert current == 100

    def test_returns_false_when_ap_unreadable(self, mock_device):
        """If AP menu OCR fails, return (False, 0)."""
        with patch("actions.titans._read_ap_from_menu", return_value=None), \
             patch("actions.titans.save_failure_screenshot"), \
             patch("actions.titans.config", self._mock_config()):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is False
        assert current == 0

    def test_free_restore_increases_ap(self, mock_device):
        """Free restore bumps AP from 10 → 35 (one use of 25 AP)."""
        ap_reads = iter([(10, 400), (35, 400)])
        with patch("actions.titans._read_ap_from_menu", side_effect=lambda d: next(ap_reads)), \
             patch("actions.titans.adb_tap") as mock_tap, \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config()):
            success, current = _restore_ap_from_open_menu(mock_device, 20)
        assert success is True
        assert current == 35
        # Should have tapped the free button
        mock_tap.assert_any_call(mock_device, *_AP_FREE_OPEN)

    def test_free_restore_exhausted_moves_to_potions(self, mock_device):
        """Free restore has no effect → tries potions."""
        # First read: 10 AP. Free attempt: still 10. Potion: jumps to 60.
        ap_reads = iter([(10, 400), (10, 400), (60, 400)])
        with patch("actions.titans._read_ap_from_menu", side_effect=lambda d: next(ap_reads)), \
             patch("actions.titans.adb_tap") as mock_tap, \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config()):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is True
        assert current == 60

    def test_potions_disabled_skips_to_end(self, mock_device):
        """ap_use_potions=False → skips potions, returns failure."""
        with patch("actions.titans._read_ap_from_menu", return_value=(10, 400)), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config(
                 ap_use_free=False, ap_use_potions=False)):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is False

    def test_free_disabled_skips_free(self, mock_device):
        """ap_use_free=False → goes straight to potions."""
        # First read: 10 AP. Potion: jumps to 60.
        ap_reads = iter([(10, 400), (60, 400)])
        with patch("actions.titans._read_ap_from_menu", side_effect=lambda d: next(ap_reads)), \
             patch("actions.titans.adb_tap") as mock_tap, \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config(ap_use_free=False)):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is True
        # Should NOT have tapped the free button
        free_calls = [c for c in mock_tap.call_args_list
                      if c == call(mock_device, *_AP_FREE_OPEN)]
        assert len(free_calls) == 0

    def test_large_potions_only_when_enabled(self, mock_device):
        """With ap_allow_large_potions=False, only small potions are tried."""
        # 10 AP, all small potions fail (no effect), should not try large ones
        with patch("actions.titans._read_ap_from_menu", return_value=(10, 400)), \
             patch("actions.titans.adb_tap") as mock_tap, \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config(
                 ap_use_free=False, ap_allow_large_potions=False)):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        # Should not have tapped any large potion coordinate
        large_coords = set(_AP_POTIONS_LARGE)
        for c in mock_tap.call_args_list:
            pos = (c[0][1], c[0][2])
            assert pos not in large_coords

    def test_large_potions_included_when_enabled(self, mock_device):
        """With ap_allow_large_potions=True, large potions are available."""
        # 10 AP. Small potions all fail. First large potion works: 10→110.
        call_count = [0]
        def ap_reader(dev):
            call_count[0] += 1
            if call_count[0] == 1:
                return (10, 400)  # initial read
            # Small potions fail (reads 2-7), first large potion succeeds (read 8+)
            # After 3 small potions × 2 reads each = 6 reads, + initial = 7
            # Read 8+ means large potion worked
            if call_count[0] <= 7:
                return (10, 400)  # small potions no effect
            return (110, 400)  # large potion worked
        with patch("actions.titans._read_ap_from_menu", side_effect=ap_reader), \
             patch("actions.titans.adb_tap") as mock_tap, \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config(
                 ap_use_free=False, ap_allow_large_potions=True)):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is True
        assert current == 110


class TestRestoreApFromOpenMenuGems:
    """Tests for gem restore path in _restore_ap_from_open_menu."""

    def _mock_config(self, **overrides):
        defaults = {
            "ap_use_free": False,
            "ap_use_potions": False,
            "ap_allow_large_potions": False,
            "ap_use_gems": True,
            "ap_gem_limit": 500,
        }
        defaults.update(overrides)
        mock_cfg = MagicMock()
        mock_cfg.get_device_config.side_effect = lambda dev, key: defaults.get(key, False)
        return mock_cfg

    def test_gem_restore_works(self, mock_device):
        """Gem restore: 10 AP → tap gem → confirm 50 gems → 60 AP."""
        ap_reads = iter([(10, 400), (60, 400)])
        with patch("actions.titans._read_ap_from_menu", side_effect=lambda d: next(ap_reads)), \
             patch("actions.titans._read_gem_cost", return_value=50), \
             patch("actions.titans.adb_tap") as mock_tap, \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config()):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is True
        assert current == 60
        # Should have tapped gem button then confirm
        mock_tap.assert_any_call(mock_device, *_AP_GEM_BUTTON)
        mock_tap.assert_any_call(mock_device, *_AP_GEM_CONFIRM)

    def test_gem_limit_prevents_overspend(self, mock_device):
        """Gem cost would exceed limit → cancels with close_x."""
        with patch("actions.titans._read_ap_from_menu", return_value=(10, 400)), \
             patch("actions.titans._read_gem_cost", return_value=600), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image") as mock_tap_img, \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config(ap_gem_limit=500)):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is False
        # Should close the confirmation dialog
        mock_tap_img.assert_any_call("close_x.png", mock_device)

    def test_gem_confirmation_not_appearing(self, mock_device):
        """Gem cost returns None (exhausted/unreadable) → stops gem loop."""
        with patch("actions.titans._read_ap_from_menu", return_value=(10, 400)), \
             patch("actions.titans._read_gem_cost", return_value=None), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config()):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is False

    def test_gems_disabled_skips_gems(self, mock_device):
        """ap_use_gems=False → no gem attempts."""
        with patch("actions.titans._read_ap_from_menu", return_value=(10, 400)), \
             patch("actions.titans._read_gem_cost") as mock_gem_cost, \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config(ap_use_gems=False)):
            _restore_ap_from_open_menu(mock_device, 50)
        mock_gem_cost.assert_not_called()

    def test_gem_limit_zero_skips_gems(self, mock_device):
        """ap_gem_limit=0 → no gem attempts even when ap_use_gems=True."""
        with patch("actions.titans._read_ap_from_menu", return_value=(10, 400)), \
             patch("actions.titans._read_gem_cost") as mock_gem_cost, \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config(ap_gem_limit=0)):
            _restore_ap_from_open_menu(mock_device, 50)
        mock_gem_cost.assert_not_called()

    def test_gem_cumulative_limit(self, mock_device):
        """Two gem uses at 50 each = 100 spent, third at 100 would exceed 200 limit."""
        call_count = [0]
        def ap_reader(dev):
            call_count[0] += 1
            ap_values = [10, 60, 110, 110]  # initial, after 1st gem, after 2nd gem, after close
            return (ap_values[min(call_count[0] - 1, len(ap_values) - 1)], 400)

        gem_costs = iter([50, 50, 100])  # third would push to 200, within limit
        with patch("actions.titans._read_ap_from_menu", side_effect=ap_reader), \
             patch("actions.titans._read_gem_cost", side_effect=lambda d: next(gem_costs, None)), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config(ap_gem_limit=200)):
            success, current = _restore_ap_from_open_menu(mock_device, 100)
        assert success is True
        assert current == 110

    def test_gem_no_effect_stops_loop(self, mock_device):
        """Gem restore had no effect (out of gems) → stops."""
        with patch("actions.titans._read_ap_from_menu", return_value=(10, 400)), \
             patch("actions.titans._read_gem_cost", return_value=50), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config", self._mock_config()):
            success, current = _restore_ap_from_open_menu(mock_device, 50)
        assert success is False


# ============================================================
# restore_ap (full flow with menu navigation)
# ============================================================

class TestRestoreAp:
    def test_happy_path(self, mock_device):
        """Navigate to MAP → open search → open AP → restore → close."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.titans.navigate", return_value=True) as mock_nav, \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", return_value=(0.95, (100, 100), 50, 200)), \
             patch("actions.titans._restore_ap_from_open_menu", return_value=(True, 100)), \
             patch("actions.titans._close_ap_menu") as mock_close, \
             patch("actions.titans.stats") as mock_stats:
            result = restore_ap(mock_device, 20)
        assert result is True
        mock_nav.assert_called_with(Screen.MAP, mock_device)
        mock_close.assert_called_once()
        mock_stats.record_action.assert_called_once()

    def test_fails_to_navigate(self, mock_device):
        """If MAP navigation fails, return False."""
        with patch("actions.titans.navigate", return_value=False), \
             patch("actions.titans.stats") as mock_stats:
            result = restore_ap(mock_device, 20)
        assert result is False
        mock_stats.record_action.assert_called_once_with(
            mock_device, "restore_ap", False, mock_stats.record_action.call_args[0][3])

    def test_menu_not_opening(self, mock_device):
        """AP Recovery menu doesn't appear → return False."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", return_value=None), \
             patch("actions.titans.save_failure_screenshot"), \
             patch("actions.titans._close_ap_menu"), \
             patch("actions.titans.stats"):
            result = restore_ap(mock_device, 20)
        assert result is False

    def test_restore_fails_returns_false(self, mock_device):
        """AP Recovery menu opens but restore fails → return False."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", return_value=(0.95, (100, 100), 50, 200)), \
             patch("actions.titans._restore_ap_from_open_menu", return_value=(False, 10)), \
             patch("actions.titans._close_ap_menu"), \
             patch("actions.titans.stats"):
            result = restore_ap(mock_device, 50)
        assert result is False


# ============================================================
# rally_titan
# ============================================================

class TestRallyTitan:
    """Tests for the rally_titan function."""

    def _default_patches(self):
        """Return common patches for rally_titan tests."""
        return {
            "navigate": patch("actions.titans.navigate", return_value=True),
            "heal_all": patch("actions.titans.heal_all"),
            "troops_avail": patch("actions.titans.troops_avail", return_value=5),
            "read_ap": patch("actions.titans.read_ap", return_value=(400, 400)),
            "logged_tap": patch("actions.titans.logged_tap"),
            "adb_tap": patch("actions.titans.adb_tap"),
            "tap_image": patch("actions.titans.tap_image"),
            "timed_wait": patch("actions.titans.timed_wait"),
            "wait_for_image_and_tap": patch("actions.titans.wait_for_image_and_tap", return_value=True),
            "check_screen": patch("actions.titans.check_screen", return_value=Screen.MAP),
            "load_screenshot": patch("actions.titans.load_screenshot",
                                     return_value=np.zeros((1920, 1080, 3), dtype=np.uint8)),
            "find_image": patch("actions.titans.find_image",
                                return_value=(0.9, (400, 800), 50, 100)),
            "save_failure_screenshot": patch("actions.titans.save_failure_screenshot"),
            "capture_departing_portrait": patch("actions.titans.capture_departing_portrait",
                                                 return_value=None),
            "_save_click_trail": patch("actions.titans._save_click_trail"),
            "time_sleep": patch("actions.titans.time.sleep"),
            "config": patch("actions.titans.config"),
        }

    def test_happy_path_depart_found(self, mock_device):
        """Search → find titan → depart → success."""
        patches = self._default_patches()
        mocks = {}
        with patches["navigate"] as m_nav, \
             patches["heal_all"], \
             patches["troops_avail"], \
             patches["read_ap"], \
             patches["logged_tap"], \
             patches["adb_tap"] as m_tap, \
             patches["tap_image"], \
             patches["timed_wait"], \
             patches["wait_for_image_and_tap"], \
             patches["check_screen"], \
             patches["load_screenshot"], \
             patches["find_image"], \
             patches["save_failure_screenshot"], \
             patches["capture_departing_portrait"], \
             patches["_save_click_trail"], \
             patches["time_sleep"], \
             patches["config"] as m_cfg:
            m_cfg.get_device_config.return_value = True
            m_cfg.AP_COST_RALLY_TITAN = 20
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": True, "min_troops": 0, "auto_restore_ap": True
            }.get(k, True)
            result = rally_titan(mock_device)
        assert result is True

    def test_not_enough_troops(self, mock_device):
        """troops_avail < min_troops → return False."""
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=0), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": True, "min_troops": 1,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is False

    def test_not_enough_ap_no_restore(self, mock_device):
        """AP too low and auto_restore_ap=False → return False."""
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(5, 400)), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0, "auto_restore_ap": False,
            }.get(k, False)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is False

    def test_not_enough_ap_restore_succeeds(self, mock_device):
        """AP too low → restore_ap succeeds → continues."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(5, 400)), \
             patch("actions.titans.restore_ap", return_value=True) as m_restore, \
             patch("actions.titans.logged_tap"), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image"), \
             patch("actions.titans.timed_wait"), \
             patch("actions.titans.wait_for_image_and_tap", return_value=True), \
             patch("actions.titans.check_screen", return_value=Screen.MAP), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", return_value=(0.9, (400, 800), 50, 100)), \
             patch("actions.titans.save_failure_screenshot"), \
             patch("actions.titans.capture_departing_portrait", return_value=None), \
             patch("actions.titans._save_click_trail"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0, "auto_restore_ap": True,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is True
        m_restore.assert_called_once_with(mock_device, 20)

    def test_not_enough_ap_restore_fails(self, mock_device):
        """AP too low → restore_ap fails → return False."""
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(5, 400)), \
             patch("actions.titans.restore_ap", return_value=False), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0, "auto_restore_ap": True,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is False

    def test_ap_unreadable_proceeds(self, mock_device):
        """read_ap returns None → proceed anyway (game handles low AP)."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=None), \
             patch("actions.titans.logged_tap"), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image"), \
             patch("actions.titans.timed_wait"), \
             patch("actions.titans.wait_for_image_and_tap", return_value=True), \
             patch("actions.titans.check_screen", return_value=Screen.MAP), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", return_value=(0.9, (400, 800), 50, 100)), \
             patch("actions.titans.capture_departing_portrait", return_value=None), \
             patch("actions.titans._save_click_trail"), \
             patch("actions.titans.save_failure_screenshot"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is True

    def test_navigate_to_map_fails(self, mock_device):
        """If navigate to MAP fails, return False."""
        with patch("actions.titans.navigate", return_value=False), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is False

    def test_titan_select_not_found(self, mock_device):
        """rally_titan_select.png not found → return False."""
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.logged_tap"), \
             patch("actions.titans.timed_wait"), \
             patch("actions.titans.find_image", return_value=None), \
             patch("actions.titans.wait_for_image_and_tap", return_value=False), \
             patch("actions.titans.save_failure_screenshot"), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is False

    def test_depart_not_found_retries(self, mock_device):
        """Depart not found on first attempt → saves screenshot, retries."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        attempt_count = [0]
        def find_image_side_effect(s, name, **kwargs):
            if name == "depart.png":
                attempt_count[0] += 1
                if attempt_count[0] > 8:  # succeed on second search attempt
                    return (0.9, (400, 800), 50, 100)
                return None
            if name == "depart_anyway.png":
                return None
            return None

        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.logged_tap"), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image"), \
             patch("actions.titans.timed_wait"), \
             patch("actions.titans.wait_for_image_and_tap", return_value=True), \
             patch("actions.titans.check_screen", return_value=Screen.MAP), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", side_effect=find_image_side_effect), \
             patch("actions.titans.save_failure_screenshot") as m_save, \
             patch("actions.titans.capture_departing_portrait", return_value=None), \
             patch("actions.titans._save_click_trail"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.time.time", side_effect=[
                 # timed_action entry
                 1000.0,
                 # Search attempt 1: depart poll loop — 8s budget, time() called each iteration
                 1000.0, 1000.5, 1001.0, 1001.5, 1002.0, 1002.5, 1003.0, 1003.5, 1004.0,
                 1009.0,  # exceeds 8s budget
                 # Search attempt 2: depart poll loop
                 1010.0, 1010.5, 1011.0,  # find_image returns match
                 # timed_action exit
                 1012.0,
             ]), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is True

    def test_depart_anyway_with_auto_heal(self, mock_device):
        """Depart Anyway visible + auto_heal=True → heals and returns False (caller retries)."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        def find_image_side_effect(s, name, **kwargs):
            if name == "depart.png":
                return None
            if name == "depart_anyway.png":
                return (0.9, (400, 800), 50, 100)
            return None

        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all") as m_heal, \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.logged_tap"), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image"), \
             patch("actions.titans.timed_wait"), \
             patch("actions.titans.wait_for_image_and_tap", return_value=True), \
             patch("actions.titans.check_screen", return_value=Screen.MAP), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", side_effect=find_image_side_effect), \
             patch("actions.titans.save_failure_screenshot"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.time.time", side_effect=[
                 1000.0,  # timed_action
                 1000.0, 1000.5,  # depart poll
                 1012.0,  # timed_action exit
             ]), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": True, "min_troops": 0,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is False
        # heal_all called twice: once at start, once after depart_anyway
        assert m_heal.call_count == 2

    def test_depart_anyway_without_auto_heal(self, mock_device):
        """Depart Anyway visible + auto_heal=False → taps Depart Anyway, returns True."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        def find_image_side_effect(s, name, **kwargs):
            if name == "depart.png":
                return None
            if name == "depart_anyway.png":
                return (0.9, (400, 800), 50, 100)
            return None

        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.logged_tap"), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image") as m_tap, \
             patch("actions.titans.timed_wait"), \
             patch("actions.titans.wait_for_image_and_tap", return_value=True), \
             patch("actions.titans.check_screen", return_value=Screen.MAP), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", side_effect=find_image_side_effect), \
             patch("actions.titans.save_failure_screenshot"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.time.time", side_effect=[
                 1000.0,  # timed_action
                 1000.0, 1000.5,  # depart poll
                 1012.0,  # timed_action exit
             ]), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, False)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is True
        m_tap.assert_any_call("depart_anyway.png", mock_device)

    def test_all_search_attempts_fail(self, mock_device):
        """All search attempts fail to find depart → return False."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)

        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.logged_tap"), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image"), \
             patch("actions.titans.timed_wait"), \
             patch("actions.titans.wait_for_image_and_tap", return_value=True), \
             patch("actions.titans.check_screen", return_value=Screen.MAP), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", return_value=None), \
             patch("actions.titans.save_failure_screenshot") as m_save, \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.time.time", side_effect=[
                 1000.0,  # timed_action
                 # 3 search attempts, each with poll loop exceeding 8s
                 1000.0, 1009.0,  # attempt 1
                 1010.0, 1019.0,  # attempt 2
                 1020.0, 1029.0,  # attempt 3
                 1030.0,  # timed_action exit
             ]), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is False
        # Should save failure screenshots for each miss
        assert m_save.call_count >= _MAX_TITAN_SEARCH_ATTEMPTS

    def test_popup_after_search_dismissed(self, mock_device):
        """If popup appears after titan search, navigates back to MAP."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        screen_checks = iter([Screen.UNKNOWN, Screen.MAP])  # first UNKNOWN, then MAP

        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.logged_tap"), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image"), \
             patch("actions.titans.timed_wait"), \
             patch("actions.titans.wait_for_image_and_tap", return_value=True), \
             patch("actions.titans.check_screen", side_effect=lambda d: next(screen_checks, Screen.MAP)), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", return_value=(0.9, (400, 800), 50, 100)), \
             patch("actions.titans.save_failure_screenshot"), \
             patch("actions.titans.capture_departing_portrait", return_value=None), \
             patch("actions.titans._save_click_trail"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.time.time", side_effect=[
                 1000.0, 1000.0, 1000.5, 1012.0,
             ]), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is True

    def test_portrait_capture_failure_continues(self, mock_device):
        """Portrait capture exception → continues without slot tracking."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all"), \
             patch("actions.titans.troops_avail", return_value=5), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.logged_tap"), \
             patch("actions.titans.adb_tap"), \
             patch("actions.titans.tap_image"), \
             patch("actions.titans.timed_wait"), \
             patch("actions.titans.wait_for_image_and_tap", return_value=True), \
             patch("actions.titans.check_screen", return_value=Screen.MAP), \
             patch("actions.titans.load_screenshot", return_value=screen), \
             patch("actions.titans.find_image", return_value=(0.9, (400, 800), 50, 100)), \
             patch("actions.titans.save_failure_screenshot"), \
             patch("actions.titans.capture_departing_portrait", side_effect=Exception("fail")), \
             patch("actions.titans._save_click_trail"), \
             patch("actions.titans.time.sleep"), \
             patch("actions.titans.time.time", side_effect=[
                 1000.0, 1000.0, 1000.5, 1012.0,
             ]), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            result = rally_titan(mock_device)
        assert result is True

    def test_heals_before_rally_when_auto_heal(self, mock_device):
        """auto_heal=True → heal_all called before troop check."""
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all") as m_heal, \
             patch("actions.titans.troops_avail", return_value=0), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": True, "min_troops": 1,
            }.get(k, True)
            m_cfg.AP_COST_RALLY_TITAN = 20
            rally_titan(mock_device)
        m_heal.assert_called_once_with(mock_device)

    def test_no_heal_when_auto_heal_off(self, mock_device):
        """auto_heal=False → heal_all not called."""
        with patch("actions.titans.navigate", return_value=True), \
             patch("actions.titans.heal_all") as m_heal, \
             patch("actions.titans.troops_avail", return_value=0), \
             patch("actions.titans.read_ap", return_value=(400, 400)), \
             patch("actions.titans.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 1,
            }.get(k, False)
            m_cfg.AP_COST_RALLY_TITAN = 20
            rally_titan(mock_device)
        m_heal.assert_not_called()
