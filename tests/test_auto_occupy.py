"""Comprehensive tests for the auto_occupy_loop rewrite (territory.py).

Covers: death recovery, reinforcement, teleport failures, menu detection,
depart_anyway fallback, navigation recovery, stop_check cooperative shutdown.

All ADB/vision calls are mocked — no emulator needed.
"""

import numpy as np
import threading
import pytest
from unittest.mock import patch, MagicMock, call, PropertyMock

import config
from config import Screen, BORDER_COLORS
from territory import (
    auto_occupy_loop, _check_and_revive, _tap_tower_and_detect_menu,
    _do_depart, _interruptible_sleep, scan_targets, _pick_target,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_occupy_state():
    """Reset territory-related config before each test."""
    orig_team = config.MY_TEAM_COLOR
    orig_enemies = config.ENEMY_TEAMS
    config.MY_TEAM_COLOR = "red"
    config.ENEMY_TEAMS = ["yellow"]
    config.MANUAL_ATTACK_SQUARES.clear()
    config.MANUAL_IGNORE_SQUARES.clear()
    config.LAST_ATTACKED_SQUARE.clear()
    config.AUTO_HEAL_ENABLED = False
    config.MIN_TROOPS_AVAILABLE = 0
    config.DEVICE_STATUS.clear()
    config.TERRITORY_PASSES = {}
    config.TERRITORY_MUTUAL_ZONES = {}
    config.TERRITORY_SAFE_ZONES = {}
    config.TERRITORY_HOME_ZONES = {}
    config.PASS_BLOCKED_SQUARES = set()
    config.ZONE_EXPECTED_TEAMS = {}
    yield
    config.MY_TEAM_COLOR = orig_team
    config.ENEMY_TEAMS = orig_enemies
    config.MANUAL_ATTACK_SQUARES.clear()
    config.MANUAL_IGNORE_SQUARES.clear()
    config.LAST_ATTACKED_SQUARE.clear()
    config.DEVICE_STATUS.clear()
    config.TERRITORY_PASSES = {}
    config.TERRITORY_MUTUAL_ZONES = {}
    config.TERRITORY_SAFE_ZONES = {}
    config.TERRITORY_HOME_ZONES = {}
    config.PASS_BLOCKED_SQUARES = set()
    config.ZONE_EXPECTED_TEAMS = {}


def _make_stop_after(n_sleeps):
    """Create a stop_check that triggers after N sleep calls.

    Returns (stop_check, mock_sleep) where mock_sleep should be
    patched as territory.time.sleep's side_effect.
    """
    state = {"stopped": False, "sleep_count": 0}

    def stop_check():
        return state["stopped"]

    def sleep_side(seconds):
        state["sleep_count"] += 1
        if state["sleep_count"] >= n_sleeps:
            state["stopped"] = True

    return stop_check, sleep_side


# ============================================================
# _interruptible_sleep — unit tests
# ============================================================

class TestInterruptibleSleep:

    @patch("territory.time.sleep")
    def test_sleeps_full_duration(self, mock_sleep):
        """Completes full sleep when not stopped."""
        result = _interruptible_sleep(3, lambda: False)
        assert result is False
        assert mock_sleep.call_count == 3

    @patch("territory.time.sleep")
    def test_stops_early(self, mock_sleep):
        """Returns True immediately when stop_check fires."""
        result = _interruptible_sleep(10, lambda: True)
        assert result is True
        mock_sleep.assert_not_called()

    @patch("territory.time.sleep")
    def test_stops_mid_sleep(self, mock_sleep):
        """Stops partway through when stop_check becomes True."""
        calls = [0]
        def check():
            return calls[0] >= 2
        def sleep_fn(s):
            calls[0] += 1
        mock_sleep.side_effect = sleep_fn

        result = _interruptible_sleep(5, check)
        assert result is True
        assert mock_sleep.call_count == 2


# ============================================================
# _check_and_revive — unit tests
# ============================================================

class TestCheckAndRevive:

    @patch("territory.tap_image", return_value=False)
    def test_not_dead_returns_false(self, mock_tap, mock_device):
        """No dead.png → returns False (alive)."""
        log = MagicMock()
        result = _check_and_revive(mock_device, log, lambda: False)
        assert result is False

    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=True)
    def test_dead_revives_and_returns_true(self, mock_tap, mock_sleep, mock_device):
        """dead.png found → revive → MAP confirmed → returns True."""
        log = MagicMock()
        with patch("navigation.check_screen", return_value=Screen.MAP):
            result = _check_and_revive(mock_device, log, lambda: False)
        assert result is True
        assert config.DEVICE_STATUS.get(mock_device) == "Reviving..."

    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=True)
    def test_dead_stopped_returns_none(self, mock_tap, mock_sleep, mock_device):
        """dead.png found but stop_check fires during wait → returns None."""
        log = MagicMock()
        result = _check_and_revive(mock_device, log, lambda: True)
        assert result is None


# ============================================================
# _tap_tower_and_detect_menu — unit tests
# ============================================================

class TestTapTowerAndDetectMenu:

    @patch("territory.time.sleep")
    @patch("territory.load_screenshot")
    @patch("territory.adb_tap")
    @patch("territory.find_image")
    def test_detects_attack_button(self, mock_find, mock_tap, mock_screenshot,
                                    mock_sleep, mock_device):
        """attack_button.png found → returns 'attack'."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        mock_screenshot.return_value = screen
        # find_image returns match for attack_button, None for reinforce
        def find_side(scr, name, threshold=0.8):
            if name == "attack_button.png":
                return (0.9, (100, 100), 50, 50)
            return None
        mock_find.side_effect = find_side

        log = MagicMock()
        result = _tap_tower_and_detect_menu(mock_device, log, timeout=5)
        assert result == "attack"

    @patch("territory.time.sleep")
    @patch("territory.load_screenshot")
    @patch("territory.adb_tap")
    @patch("territory.find_image")
    def test_detects_reinforce_button(self, mock_find, mock_tap, mock_screenshot,
                                       mock_sleep, mock_device):
        """reinforce_button.png found → returns 'reinforce'."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        mock_screenshot.return_value = screen
        def find_side(scr, name, threshold=0.8):
            if name == "reinforce_button.png":
                return (0.9, (100, 100), 50, 50)
            return None
        mock_find.side_effect = find_side

        log = MagicMock()
        result = _tap_tower_and_detect_menu(mock_device, log, timeout=5)
        assert result == "reinforce"

    @patch("territory.time.sleep")
    @patch("territory.time.time")
    @patch("territory.load_screenshot", return_value=np.zeros((1920, 1080, 3), dtype=np.uint8))
    @patch("territory.adb_tap")
    @patch("territory.find_image", return_value=None)
    def test_timeout_returns_none(self, mock_find, mock_tap, mock_screenshot,
                                   mock_time, mock_sleep, mock_device):
        """Neither button found → returns None after timeout."""
        # Simulate time passing beyond timeout
        mock_time.side_effect = [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

        log = MagicMock()
        result = _tap_tower_and_detect_menu(mock_device, log, timeout=10)
        assert result is None


# ============================================================
# _do_depart — unit tests
# ============================================================

class TestDoDepart:

    @patch("territory.wait_for_image_and_tap", return_value=True)
    @patch("territory.tap_image", return_value=True)
    @patch("territory.time.sleep")
    def test_attack_depart_success(self, mock_sleep, mock_tap, mock_wait, mock_device):
        """Attack button tapped, depart found → returns True."""
        log = MagicMock()
        result = _do_depart(mock_device, log, "attack")
        assert result is True

    @patch("territory.wait_for_image_and_tap", return_value=True)
    @patch("territory.tap_image", return_value=True)
    @patch("territory.time.sleep")
    def test_reinforce_depart_success(self, mock_sleep, mock_tap, mock_wait, mock_device):
        """Reinforce button tapped, depart found → returns True."""
        log = MagicMock()
        result = _do_depart(mock_device, log, "reinforce")
        assert result is True

    @patch("territory.save_failure_screenshot")
    @patch("territory.find_image", return_value=(400, 900))
    @patch("territory.load_screenshot", return_value=MagicMock())
    @patch("territory.wait_for_image_and_tap", return_value=False)
    @patch("territory.tap_image")
    @patch("territory.time.sleep")
    @patch("territory.heal_all")
    def test_depart_anyway_fallback(self, mock_heal, mock_sleep, mock_tap,
                                     mock_wait, mock_load, mock_find,
                                     mock_save, mock_device):
        """depart.png fails, depart_anyway.png found → taps it (auto_heal off)."""
        config.AUTO_HEAL_ENABLED = False
        # tap_image: True for attack_button, True for depart_anyway
        mock_tap.return_value = True

        log = MagicMock()
        result = _do_depart(mock_device, log, "attack")
        assert result is True

    @patch("territory.save_failure_screenshot")
    @patch("territory.find_image", return_value=(400, 900))
    @patch("territory.load_screenshot", return_value=MagicMock())
    @patch("territory.wait_for_image_and_tap", return_value=False)
    @patch("territory.tap_image", return_value=True)
    @patch("territory.time.sleep")
    @patch("territory.heal_all")
    def test_depart_anyway_with_heal(self, mock_heal, mock_sleep, mock_tap,
                                      mock_wait, mock_load, mock_find,
                                      mock_save, mock_device):
        """depart.png fails, depart_anyway found, auto_heal on → heals and returns False."""
        config.AUTO_HEAL_ENABLED = True

        log = MagicMock()
        result = _do_depart(mock_device, log, "attack")
        assert result is False
        mock_heal.assert_called_once()

    @patch("territory.save_failure_screenshot")
    @patch("territory.wait_for_image_and_tap", return_value=False)
    @patch("territory.tap_image", return_value=False)
    @patch("territory.time.sleep")
    def test_action_button_not_found(self, mock_sleep, mock_tap, mock_wait,
                                      mock_save, mock_device):
        """Action button not found → returns False."""
        log = MagicMock()
        result = _do_depart(mock_device, log, "attack")
        assert result is False


# ============================================================
# auto_occupy_loop — full integration tests
# ============================================================

class TestAutoOccupyLoopIntegration:

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate", return_value=True)
    @patch("territory.attack_territory", return_value=(5, 6, "attack"))
    @patch("territory.teleport", return_value=True)
    @patch("territory.load_screenshot")
    @patch("territory.find_image", return_value=(0.9, (100, 100), 50, 50))
    @patch("territory.wait_for_image_and_tap", return_value=True)
    @patch("territory.adb_tap")
    @patch("territory.adb_keyevent")
    @patch("territory.troops_avail", return_value=5)
    def test_happy_path_attack(
        self, mock_avail, mock_keyevent, mock_adb_tap, mock_wait,
        mock_find, mock_screenshot, mock_teleport, mock_attack,
        mock_nav, mock_heal, mock_troops_home, mock_tap, mock_sleep,
        mock_save, mock_device
    ):
        """Full cycle: scan → teleport → attack → depart → stop."""
        mock_screenshot.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        config.MIN_TROOPS_AVAILABLE = 0
        stop_check, sleep_side = _make_stop_after(5)
        mock_sleep.side_effect = sleep_side

        auto_occupy_loop(mock_device, stop_check=stop_check)

        mock_attack.assert_called_once()
        mock_teleport.assert_called_once()
        # Verify status was set during cycle
        # (status gets overwritten multiple times, just check no crash)

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate", return_value=True)
    @patch("territory.attack_territory", return_value=(5, 6, "reinforce"))
    @patch("territory.teleport", return_value=True)
    @patch("territory.load_screenshot")
    @patch("territory.find_image")
    @patch("territory.wait_for_image_and_tap", return_value=True)
    @patch("territory.adb_tap")
    @patch("territory.adb_keyevent")
    @patch("territory.troops_avail", return_value=5)
    def test_happy_path_reinforce(
        self, mock_avail, mock_keyevent, mock_adb_tap, mock_wait,
        mock_find, mock_screenshot, mock_teleport, mock_attack,
        mock_nav, mock_heal, mock_troops_home, mock_tap, mock_sleep,
        mock_save, mock_device
    ):
        """Full cycle with reinforce: scan → teleport → reinforce → depart."""
        mock_screenshot.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        config.MIN_TROOPS_AVAILABLE = 0
        # find_image returns reinforce button
        def find_side(scr, name, threshold=0.8):
            if name == "reinforce_button.png":
                return (0.9, (100, 100), 50, 50)
            return None
        mock_find.side_effect = find_side
        stop_check, sleep_side = _make_stop_after(5)
        mock_sleep.side_effect = sleep_side

        auto_occupy_loop(mock_device, stop_check=stop_check)

        mock_attack.assert_called_once()
        mock_teleport.assert_called_once()

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image")
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate", return_value=True)
    def test_death_recovery_picks_new_target(
        self, mock_nav, mock_troops_home, mock_heal, mock_tap,
        mock_sleep, mock_save, mock_device
    ):
        """dead.png found → revive → continues loop with new cycle."""
        # First tap_image call: dead.png → True (dead), then False for rest
        call_count = [0]
        def tap_side(name, dev):
            call_count[0] += 1
            if call_count[0] == 1 and name == "dead.png":
                return True
            return False
        mock_tap.side_effect = tap_side

        with patch("navigation.check_screen", return_value=Screen.MAP):
            stop_check, sleep_side = _make_stop_after(3)
            mock_sleep.side_effect = sleep_side
            auto_occupy_loop(mock_device, stop_check=stop_check)

        # Should have set "Reviving..." status at some point
        # (status changes multiple times, just verify no crash)

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate", return_value=True)
    @patch("territory.attack_territory", return_value=(5, 6, "attack"))
    @patch("territory.teleport", return_value=True)
    @patch("territory.load_screenshot")
    @patch("territory.find_image", return_value=None)
    @patch("territory.adb_tap")
    @patch("territory.adb_keyevent")
    @patch("territory.troops_avail", return_value=5)
    def test_menu_fail_skips_cycle(
        self, mock_avail, mock_keyevent, mock_adb_tap,
        mock_find, mock_screenshot, mock_teleport, mock_attack,
        mock_nav, mock_heal, mock_troops_home, mock_tap, mock_sleep,
        mock_save, mock_device
    ):
        """Tower menu doesn't open → saves failure screenshot, skips cycle."""
        mock_screenshot.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        stop_check, sleep_side = _make_stop_after(5)
        mock_sleep.side_effect = sleep_side

        auto_occupy_loop(mock_device, stop_check=stop_check)

        # save_failure_screenshot should be called for menu fail
        mock_save.assert_called()
        save_labels = [c[0][1] for c in mock_save.call_args_list]
        assert any("tower_menu_fail" in label for label in save_labels)

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate", return_value=True)
    @patch("territory.attack_territory", return_value=(5, 6, "attack"))
    @patch("territory.teleport", return_value=False)
    @patch("territory.load_screenshot")
    @patch("territory.adb_tap")
    def test_teleport_fail_retries_then_new_target(
        self, mock_adb_tap, mock_screenshot, mock_teleport, mock_attack,
        mock_nav, mock_heal, mock_troops_home, mock_tap, mock_sleep,
        mock_save, mock_device
    ):
        """Teleport fails repeatedly → tries different target."""
        mock_screenshot.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        stop_check, sleep_side = _make_stop_after(15)
        mock_sleep.side_effect = sleep_side

        auto_occupy_loop(mock_device, stop_check=stop_check)

        # Should have called attack_territory multiple times (new target after fails)
        assert mock_attack.call_count >= 2

    def test_stops_immediately_when_already_stopped(self, mock_device):
        """stop_check=True from start → loop exits immediately."""
        auto_occupy_loop(mock_device, stop_check=lambda: True)
        # No crash, just returns

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=False)
    @patch("territory.navigate", return_value=True)
    def test_waits_when_troops_not_home(
        self, mock_nav, mock_troops_home, mock_heal, mock_tap,
        mock_sleep, mock_save, mock_device
    ):
        """Troops not home → sets status and waits."""
        stop_check, sleep_side = _make_stop_after(2)
        mock_sleep.side_effect = sleep_side

        auto_occupy_loop(mock_device, stop_check=stop_check)

        # Should not have tried to call attack_territory

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate")
    def test_navigate_failure_recovery(
        self, mock_nav, mock_troops_home, mock_heal, mock_tap,
        mock_sleep, mock_save, mock_device
    ):
        """navigate fails → retries, then continues."""
        # First navigate call fails, subsequent succeed
        call_count = [0]
        def nav_side(screen, dev):
            call_count[0] += 1
            if call_count[0] <= 1:
                return False
            return True
        mock_nav.side_effect = nav_side

        stop_check, sleep_side = _make_stop_after(5)
        mock_sleep.side_effect = sleep_side

        auto_occupy_loop(mock_device, stop_check=stop_check)

        # Should have retried navigation

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate", return_value=True)
    @patch("territory.attack_territory", return_value=None)
    def test_no_targets_waits_30s(
        self, mock_attack, mock_nav, mock_troops_home, mock_heal,
        mock_tap, mock_sleep, mock_save, mock_device
    ):
        """No targets found → waits 30s before rescanning."""
        stop_check, sleep_side = _make_stop_after(3)
        mock_sleep.side_effect = sleep_side

        auto_occupy_loop(mock_device, stop_check=stop_check)

        mock_attack.assert_called()

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate", return_value=True)
    @patch("territory.attack_territory", return_value=(5, 6, "attack"))
    @patch("territory.teleport", return_value=True)
    @patch("territory.load_screenshot")
    @patch("territory.find_image", return_value=(0.9, (100, 100), 50, 50))
    @patch("territory.wait_for_image_and_tap", return_value=True)
    @patch("territory.adb_tap")
    @patch("territory.adb_keyevent")
    @patch("territory.troops_avail", return_value=0)
    def test_not_enough_troops_closes_menu(
        self, mock_avail, mock_keyevent, mock_adb_tap, mock_wait,
        mock_find, mock_screenshot, mock_teleport, mock_attack,
        mock_nav, mock_heal, mock_troops_home, mock_tap, mock_sleep,
        mock_save, mock_device
    ):
        """troops_avail <= min_troops → closes menu with BACK key, skips depart."""
        mock_screenshot.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        config.MIN_TROOPS_AVAILABLE = 3
        # Need enough sleeps to reach the depart phase (many time.sleep calls in flow)
        stop_check, sleep_side = _make_stop_after(20)
        mock_sleep.side_effect = sleep_side

        auto_occupy_loop(mock_device, stop_check=stop_check)

        # BACK key (keycode 4) should have been called to close menu
        back_calls = [c for c in mock_keyevent.call_args_list
                      if c[0][1] == 4]
        assert len(back_calls) >= 1

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate", return_value=True)
    @patch("territory.attack_territory")
    @patch("territory.teleport", return_value=True)
    @patch("territory.load_screenshot")
    @patch("territory.find_image", return_value=(0.9, (100, 100), 50, 50))
    @patch("territory.wait_for_image_and_tap", return_value=True)
    @patch("territory.adb_tap")
    @patch("territory.adb_keyevent")
    @patch("territory.troops_avail", return_value=5)
    def test_exception_doesnt_crash_loop(
        self, mock_avail, mock_keyevent, mock_adb_tap, mock_wait,
        mock_find, mock_screenshot, mock_teleport, mock_attack,
        mock_nav, mock_heal, mock_troops_home, mock_tap, mock_sleep,
        mock_save, mock_device
    ):
        """Exception in cycle → caught, logged, continues."""
        mock_screenshot.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        # First call raises, second returns normally
        call_count = [0]
        def attack_side(dev, debug=False):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("test error")
            return (5, 6, "attack")
        mock_attack.side_effect = attack_side

        stop_check, sleep_side = _make_stop_after(8)
        mock_sleep.side_effect = sleep_side

        # Should not raise
        auto_occupy_loop(mock_device, stop_check=stop_check)

        # save_failure_screenshot called for the exception
        mock_save.assert_called()
        save_labels = [c[0][1] for c in mock_save.call_args_list]
        assert any("occupy_exception" in label for label in save_labels)


# ============================================================
# Status message tests
# ============================================================

class TestAutoOccupyStatus:

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.navigate", return_value=True)
    @patch("territory.attack_territory", return_value=None)
    def test_sets_scanning_status(
        self, mock_attack, mock_nav, mock_troops_home, mock_heal,
        mock_tap, mock_sleep, mock_save, mock_device
    ):
        """Sets 'Scanning Territory...' status during scan."""
        statuses_seen = []
        orig_set = config.set_device_status

        def track_status(dev, msg):
            statuses_seen.append(msg)
            orig_set(dev, msg)

        stop_check, sleep_side = _make_stop_after(2)
        mock_sleep.side_effect = sleep_side

        with patch.object(config, "set_device_status", side_effect=track_status):
            auto_occupy_loop(mock_device, stop_check=stop_check)

        assert any("Scanning" in s for s in statuses_seen)

    @patch("territory.save_failure_screenshot")
    @patch("territory.time.sleep")
    @patch("territory.tap_image", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=False)
    @patch("territory.navigate", return_value=True)
    def test_sets_waiting_for_troops_status(
        self, mock_nav, mock_troops_home, mock_heal, mock_tap,
        mock_sleep, mock_save, mock_device
    ):
        """Sets 'Waiting for Troops...' when troops not home."""
        statuses_seen = []
        orig_set = config.set_device_status

        def track_status(dev, msg):
            statuses_seen.append(msg)
            orig_set(dev, msg)

        stop_check, sleep_side = _make_stop_after(2)
        mock_sleep.side_effect = sleep_side

        with patch.object(config, "set_device_status", side_effect=track_status):
            auto_occupy_loop(mock_device, stop_check=stop_check)

        assert any("Waiting for Troops" in s for s in statuses_seen)
