"""Tests for evil guard rally (actions/evil_guard.py).

Covers: _handle_ap_popup, search_eg_reset, _probe_priest, rally_eg guards,
        marching-troop guard, and code-level pattern verification.
All ADB and vision calls are mocked — no emulator needed.
"""

import numpy as np
from unittest.mock import patch, MagicMock, call

import config
from config import Screen
from troops import TroopAction, TroopStatus, DeviceTroopSnapshot
from actions.evil_guard import (
    _handle_ap_popup,
    search_eg_reset,
    _probe_priest,
    rally_eg,
    EG_PRIEST_POSITIONS,
)


# ============================================================
# Helpers
# ============================================================

def _make_snapshot(device, actions):
    """Build a DeviceTroopSnapshot from a list of TroopAction values."""
    troops = [TroopStatus(action=a) for a in actions]
    return DeviceTroopSnapshot(device=device, troops=troops)


# ============================================================
# _handle_ap_popup
# ============================================================

class TestHandleApPopup:
    """Tests for game-triggered AP Recovery popup detection and handling."""

    def test_no_popup_returns_false(self, mock_device):
        """No apwindow.png visible → returns False (no popup)."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.evil_guard.load_screenshot", return_value=screen), \
             patch("actions.evil_guard.find_image", return_value=None):
            assert _handle_ap_popup(mock_device, 70) is False

    def test_screenshot_failure_returns_false(self, mock_device):
        with patch("actions.evil_guard.load_screenshot", return_value=None):
            assert _handle_ap_popup(mock_device, 70) is False

    def test_popup_detected_auto_restore_off(self, mock_device):
        """AP popup visible but auto_restore_ap=False → closes popup, returns False."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.evil_guard.load_screenshot", return_value=screen), \
             patch("actions.evil_guard.find_image", return_value=(0.95, (100, 100), 50, 200)), \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard._close_ap_menu") as mock_close, \
             patch("actions.evil_guard.config") as mock_cfg:
            mock_cfg.get_device_config.return_value = False
            result = _handle_ap_popup(mock_device, 70)
        assert result is False
        mock_close.assert_called_once_with(mock_device, double_close=False)

    def test_popup_detected_restore_succeeds(self, mock_device):
        """AP popup visible + auto_restore_ap=True + restore succeeds → returns True."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.evil_guard.load_screenshot", return_value=screen), \
             patch("actions.evil_guard.find_image", return_value=(0.95, (100, 100), 50, 200)), \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard._restore_ap_from_open_menu", return_value=(True, 100)), \
             patch("actions.evil_guard._close_ap_menu") as mock_close, \
             patch("actions.evil_guard.config") as mock_cfg:
            mock_cfg.get_device_config.return_value = True
            result = _handle_ap_popup(mock_device, 70)
        assert result is True
        # Single close (no search menu behind game-opened popup)
        mock_close.assert_called_once_with(mock_device, double_close=False)

    def test_popup_detected_restore_fails(self, mock_device):
        """AP popup visible + restore fails → returns False."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.evil_guard.load_screenshot", return_value=screen), \
             patch("actions.evil_guard.find_image", return_value=(0.95, (100, 100), 50, 200)), \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard._restore_ap_from_open_menu", return_value=(False, 30)), \
             patch("actions.evil_guard._close_ap_menu"), \
             patch("actions.evil_guard.config") as mock_cfg:
            mock_cfg.get_device_config.return_value = True
            result = _handle_ap_popup(mock_device, 70)
        assert result is False


# ============================================================
# search_eg_reset
# ============================================================

class TestSearchEgReset:
    def test_happy_path(self, mock_device):
        """Search EG → close twice → success."""
        with patch("actions.evil_guard._search_eg_center", return_value=True), \
             patch("actions.evil_guard.tap_image") as mock_tap, \
             patch("actions.evil_guard.time.sleep"):
            result = search_eg_reset(mock_device)
        assert result is True
        # Two close_x taps (EG view + search menu)
        assert mock_tap.call_count == 2
        mock_tap.assert_any_call("close_x.png", mock_device)

    def test_search_fails(self, mock_device):
        """_search_eg_center fails → return False."""
        with patch("actions.evil_guard._search_eg_center", return_value=False):
            result = search_eg_reset(mock_device)
        assert result is False


# ============================================================
# _probe_priest
# ============================================================

class TestProbePriest:
    def test_hit_when_checked_visible(self, mock_device):
        """checked.png found after tap → returns True (HIT)."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        checked_tmpl = np.zeros((30, 30, 3), dtype=np.uint8)

        with patch("actions.evil_guard.check_screen", return_value=Screen.MAP), \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard.get_template", return_value=checked_tmpl), \
             patch("actions.evil_guard.logged_tap"), \
             patch("actions.evil_guard.timed_wait"), \
             patch("actions.evil_guard.load_screenshot", return_value=screen), \
             patch("actions.evil_guard.cv2.matchTemplate") as mock_match, \
             patch("actions.evil_guard.cv2.minMaxLoc", return_value=(0, 0.95, (0, 0), (100, 200))), \
             patch("actions.evil_guard.tap_image"), \
             patch("actions.evil_guard.time.sleep"), \
             patch("actions.evil_guard.time.time", side_effect=[0, 0.5]):
            result = _probe_priest(mock_device, 540, 900, "P2")
        assert result is True

    def test_miss_when_no_dialog(self, mock_device):
        """No dialog after 3s → returns False (MISS), taps back."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        checked_tmpl = np.zeros((30, 30, 3), dtype=np.uint8)

        with patch("actions.evil_guard.check_screen", return_value=Screen.MAP), \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard.get_template", return_value=checked_tmpl), \
             patch("actions.evil_guard.logged_tap"), \
             patch("actions.evil_guard.timed_wait"), \
             patch("actions.evil_guard.load_screenshot", return_value=screen), \
             patch("actions.evil_guard.cv2.matchTemplate") as mock_match, \
             patch("actions.evil_guard.cv2.minMaxLoc", return_value=(0, 0.3, (0, 0), (100, 200))), \
             patch("actions.evil_guard.tap_image") as mock_tap, \
             patch("actions.evil_guard.time.sleep"), \
             patch("actions.evil_guard.time.time", side_effect=[0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]):
            result = _probe_priest(mock_device, 540, 900, "P2")
        assert result is False
        mock_tap.assert_any_call("back_arrow.png", mock_device, threshold=0.7)

    def test_recovers_to_map_if_wrong_screen(self, mock_device):
        """If on wrong screen before probe, navigates back to MAP."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        checked_tmpl = np.zeros((30, 30, 3), dtype=np.uint8)

        with patch("actions.evil_guard.check_screen", return_value=Screen.UNKNOWN), \
             patch("actions.evil_guard.navigate", return_value=True) as mock_nav, \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard.get_template", return_value=checked_tmpl), \
             patch("actions.evil_guard.logged_tap"), \
             patch("actions.evil_guard.timed_wait"), \
             patch("actions.evil_guard.load_screenshot", return_value=screen), \
             patch("actions.evil_guard.cv2.matchTemplate"), \
             patch("actions.evil_guard.cv2.minMaxLoc", return_value=(0, 0.95, (0, 0), (100, 200))), \
             patch("actions.evil_guard.tap_image"), \
             patch("actions.evil_guard.time.sleep"), \
             patch("actions.evil_guard.time.time", side_effect=[0, 0.5]):
            _probe_priest(mock_device, 540, 900, "P2")
        mock_nav.assert_called_with(Screen.MAP, mock_device)

    def test_map_recovery_fails_returns_false(self, mock_device):
        """If can't recover to MAP, returns False."""
        with patch("actions.evil_guard.check_screen", return_value=Screen.UNKNOWN), \
             patch("actions.evil_guard.navigate", return_value=False):
            result = _probe_priest(mock_device, 540, 900, "P2")
        assert result is False


# ============================================================
# rally_eg — entry guards
# ============================================================

class TestRallyEgGuards:
    """Tests for rally_eg entry conditions (before the priest loop)."""

    def test_not_enough_troops(self, mock_device):
        """troops_avail <= min_troops → return False."""
        with patch("actions.evil_guard.heal_all"), \
             patch("actions.evil_guard.troops_avail", return_value=0), \
             patch("actions.evil_guard.read_ap", return_value=(400, 400)), \
             patch("actions.evil_guard.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 1,
            }.get(k, False)
            m_cfg.AP_COST_EVIL_GUARD = 70
            result = rally_eg(mock_device)
        assert result is False

    def test_not_enough_ap_no_restore(self, mock_device):
        """AP < 70 and auto_restore_ap=False → return False."""
        with patch("actions.evil_guard.heal_all"), \
             patch("actions.evil_guard.troops_avail", return_value=5), \
             patch("actions.evil_guard.read_ap", return_value=(10, 400)), \
             patch("actions.evil_guard.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0, "auto_restore_ap": False,
            }.get(k, False)
            m_cfg.AP_COST_EVIL_GUARD = 70
            result = rally_eg(mock_device)
        assert result is False

    def test_not_enough_ap_restore_fails(self, mock_device):
        """AP < 70 and restore_ap fails → return False."""
        with patch("actions.evil_guard.heal_all"), \
             patch("actions.evil_guard.troops_avail", return_value=5), \
             patch("actions.evil_guard.read_ap", return_value=(10, 400)), \
             patch("actions.evil_guard.restore_ap", return_value=False), \
             patch("actions.evil_guard.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0, "auto_restore_ap": True,
            }.get(k, True)
            m_cfg.AP_COST_EVIL_GUARD = 70
            result = rally_eg(mock_device)
        assert result is False

    def test_ap_unreadable_proceeds(self, mock_device):
        """read_ap returns None → proceeds (game handles low AP with popup)."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.evil_guard.heal_all"), \
             patch("actions.evil_guard.troops_avail", return_value=5), \
             patch("actions.evil_guard.read_ap", return_value=None), \
             patch("actions.evil_guard._search_eg_center", return_value=False), \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, False)
            m_cfg.AP_COST_EVIL_GUARD = 70
            m_cfg.set_device_status = MagicMock()
            # Will fail at _search_eg_center, but importantly it PASSED the AP check
            result = rally_eg(mock_device)
        assert result is False  # failed at search, but AP check didn't block

    def test_search_eg_fails(self, mock_device):
        """_search_eg_center fails → return False."""
        with patch("actions.evil_guard.heal_all"), \
             patch("actions.evil_guard.troops_avail", return_value=5), \
             patch("actions.evil_guard.read_ap", return_value=(400, 400)), \
             patch("actions.evil_guard._search_eg_center", return_value=False), \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, False)
            m_cfg.AP_COST_EVIL_GUARD = 70
            m_cfg.set_device_status = MagicMock()
            result = rally_eg(mock_device)
        assert result is False

    def test_heals_before_rally_when_auto_heal(self, mock_device):
        """auto_heal=True → heal_all called."""
        with patch("actions.evil_guard.heal_all") as m_heal, \
             patch("actions.evil_guard.troops_avail", return_value=0), \
             patch("actions.evil_guard.read_ap", return_value=(400, 400)), \
             patch("actions.evil_guard.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": True, "min_troops": 1,
            }.get(k, True)
            m_cfg.AP_COST_EVIL_GUARD = 70
            rally_eg(mock_device)
        m_heal.assert_called_once_with(mock_device)

    def test_no_heal_when_auto_heal_off(self, mock_device):
        """auto_heal=False → heal_all not called."""
        with patch("actions.evil_guard.heal_all") as m_heal, \
             patch("actions.evil_guard.troops_avail", return_value=0), \
             patch("actions.evil_guard.read_ap", return_value=(400, 400)), \
             patch("actions.evil_guard.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 1,
            }.get(k, False)
            m_cfg.AP_COST_EVIL_GUARD = 70
            rally_eg(mock_device)
        m_heal.assert_not_called()

    def test_player_detected_at_eg_aborts(self, mock_device):
        """Another player detected near EG → returns False (occupied)."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.evil_guard.heal_all"), \
             patch("actions.evil_guard.troops_avail", return_value=5), \
             patch("actions.evil_guard.read_ap", return_value=(400, 400)), \
             patch("actions.evil_guard._search_eg_center", return_value=True), \
             patch("actions.evil_guard.timed_wait"), \
             patch("actions.evil_guard.check_screen", return_value=Screen.MAP), \
             patch("actions.evil_guard.load_screenshot", return_value=screen), \
             patch("actions.evil_guard._detect_player_at_eg", return_value=True), \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, False)
            m_cfg.AP_COST_EVIL_GUARD = 70
            m_cfg.set_device_status = MagicMock()
            result = rally_eg(mock_device)
        assert result is False


# ============================================================
# Marching-troop guard: unit tests for the guard condition
# ============================================================

class TestMarchingTroopGuard:
    """The marching-troop guard checks read_panel_statuses() after a
    poll_troop_ready timeout.  If any troop is still Marching, the bot
    should stop dispatching more priests.

    These tests verify the guard condition logic directly via the
    DeviceTroopSnapshot / any_doing API that the guard relies on.
    """

    def test_marching_detected_in_snapshot(self, mock_device):
        """any_doing(MARCHING) returns True when a troop is marching."""
        snap = _make_snapshot(mock_device, [
            TroopAction.DEFENDING,
            TroopAction.MARCHING,
            TroopAction.HOME,
            TroopAction.HOME,
            TroopAction.HOME,
        ])
        assert snap.any_doing(TroopAction.MARCHING) is True

    def test_no_marching_in_snapshot(self, mock_device):
        """any_doing(MARCHING) returns False when no troop is marching."""
        snap = _make_snapshot(mock_device, [
            TroopAction.DEFENDING,
            TroopAction.STATIONING,
            TroopAction.HOME,
            TroopAction.HOME,
            TroopAction.HOME,
        ])
        assert snap.any_doing(TroopAction.MARCHING) is False

    def test_multiple_marching_detected(self, mock_device):
        """Guard fires when multiple troops are marching (the bug scenario)."""
        snap = _make_snapshot(mock_device, [
            TroopAction.DEFENDING,
            TroopAction.MARCHING,
            TroopAction.MARCHING,
            TroopAction.HOME,
            TroopAction.HOME,
        ])
        assert snap.any_doing(TroopAction.MARCHING) is True

    def test_rallying_not_confused_with_marching(self, mock_device):
        """RALLYING is a different state — guard should not trigger."""
        snap = _make_snapshot(mock_device, [
            TroopAction.DEFENDING,
            TroopAction.RALLYING,
            TroopAction.HOME,
            TroopAction.HOME,
            TroopAction.HOME,
        ])
        assert snap.any_doing(TroopAction.MARCHING) is False

    def test_returning_not_confused_with_marching(self, mock_device):
        """RETURNING is a different state — guard should not trigger."""
        snap = _make_snapshot(mock_device, [
            TroopAction.DEFENDING,
            TroopAction.RETURNING,
            TroopAction.HOME,
            TroopAction.HOME,
            TroopAction.HOME,
        ])
        assert snap.any_doing(TroopAction.MARCHING) is False


# ============================================================
# Guard present in code: verify the pattern exists in evil_guard.py
# ============================================================

class TestGuardCodePresence:
    """Verify that the marching-troop guard pattern is present in the
    evil_guard.py source — a simple code-level sanity check."""

    def test_guard_pattern_in_priest_loop(self):
        """The priest loop (P2-P5) should check for MARCHING after timeout."""
        import inspect
        from actions import evil_guard
        source = inspect.getsource(evil_guard.rally_eg)
        # The guard reads panel and checks for MARCHING after poll timeout
        assert "any_doing(TroopAction.MARCHING)" in source
        assert "stopping priest dispatch" in source

    def test_guard_pattern_in_retry_loop(self):
        """The retry loop should also check for MARCHING after timeout."""
        import inspect
        from actions import evil_guard
        source = inspect.getsource(evil_guard.rally_eg)
        # Both the main loop and retry loop should have the guard
        assert source.count("any_doing(TroopAction.MARCHING)") >= 2


# ============================================================
# EG_PRIEST_POSITIONS sanity checks
# ============================================================

class TestEgPriestPositions:
    def test_has_6_positions(self):
        """EG_PRIEST_POSITIONS should have exactly 6 entries (P1-P6)."""
        assert len(EG_PRIEST_POSITIONS) == 6

    def test_all_within_screen_bounds(self):
        """All positions should be within 1080x1920 screen."""
        for i, (x, y) in enumerate(EG_PRIEST_POSITIONS):
            assert 0 <= x <= 1080, f"P{i+1} x={x} out of bounds"
            assert 0 <= y <= 1920, f"P{i+1} y={y} out of bounds"

    def test_p1_and_p6_are_center(self):
        """P1 (boss entry) and P6 (final attack) should be near center."""
        p1_x, p1_y = EG_PRIEST_POSITIONS[0]
        p6_x, p6_y = EG_PRIEST_POSITIONS[5]
        assert abs(p1_x - 540) < 100, f"P1 x={p1_x} too far from center"
        assert abs(p6_x - 540) < 100, f"P6 x={p6_x} too far from center"


# ============================================================
# rally_eg — stop_check responsiveness
# ============================================================

class TestRallyEgStopCheck:
    """Verify rally_eg respects stop_check during the priest loop."""

    def test_stop_check_aborts_rally(self, mock_device):
        """stop_check returns True → rally_eg exits early with False."""
        with patch("actions.evil_guard.heal_all"), \
             patch("actions.evil_guard.troops_avail", return_value=5), \
             patch("actions.evil_guard.read_ap", return_value=(400, 400)), \
             patch("actions.evil_guard._search_eg_center", return_value=True), \
             patch("actions.evil_guard.timed_wait"), \
             patch("actions.evil_guard.check_screen", return_value=Screen.MAP), \
             patch("actions.evil_guard.load_screenshot",
                   return_value=np.zeros((1920, 1080, 3), dtype=np.uint8)), \
             patch("actions.evil_guard._detect_player_at_eg", return_value=False), \
             patch("actions.evil_guard.get_template", return_value=None), \
             patch("actions.evil_guard.logged_tap"), \
             patch("actions.evil_guard.save_failure_screenshot"), \
             patch("actions.evil_guard.cv2.matchTemplate"), \
             patch("actions.evil_guard.cv2.minMaxLoc", return_value=(0, 0.3, (0, 0), (0, 0))), \
             patch("actions.evil_guard.tap_image"), \
             patch("actions.evil_guard.time.sleep"), \
             patch("actions.evil_guard.config") as m_cfg:
            m_cfg.get_device_config.side_effect = lambda d, k: {
                "auto_heal": False, "min_troops": 0,
            }.get(k, False)
            m_cfg.AP_COST_EVIL_GUARD = 70
            m_cfg.set_device_status = MagicMock()

            # stop_check returns True on every call — should abort immediately
            result = rally_eg(mock_device, stop_check=lambda: True)
        assert result is False
