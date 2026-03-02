"""Tests for tower/fortress quest functions."""
import time
from unittest.mock import patch, MagicMock

import pytest

from config import QuestType, Screen
from actions.quests import (
    _is_troop_defending, _is_troop_defending_relaxed, _navigate_to_tower,
    occupy_tower, recall_tower_troop, _run_tower_quest, _tower_quest_state,
    _marker_errors,
)
from troops import TroopAction, TroopStatus, DeviceTroopSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(device, actions):
    """Build a DeviceTroopSnapshot from a list of TroopAction values."""
    troops = [TroopStatus(action=a, seconds_remaining=60 if a != TroopAction.HOME else None)
              for a in actions]
    return DeviceTroopSnapshot(device=device, troops=troops)


# ---------------------------------------------------------------------------
# _is_troop_defending
# ---------------------------------------------------------------------------

class TestIsTroopDefending:
    def test_returns_true_when_defending(self, mock_device):
        snap = _make_snapshot(mock_device, [TroopAction.HOME, TroopAction.DEFENDING])
        with patch("actions.quests.get_troop_status", return_value=snap):
            assert _is_troop_defending(mock_device) is True

    def test_returns_false_when_not_defending(self, mock_device):
        snap = _make_snapshot(mock_device, [TroopAction.HOME, TroopAction.RALLYING])
        with patch("actions.quests.get_troop_status", return_value=snap):
            assert _is_troop_defending(mock_device) is False

    def test_returns_false_all_home(self, mock_device):
        snap = _make_snapshot(mock_device, [TroopAction.HOME, TroopAction.HOME])
        with patch("actions.quests.get_troop_status", return_value=snap):
            assert _is_troop_defending(mock_device) is False

    def test_falls_back_to_panel_read_when_no_cache(self, mock_device):
        snap = _make_snapshot(mock_device, [TroopAction.DEFENDING])
        with patch("actions.quests.get_troop_status", return_value=None), \
             patch("actions.quests.read_panel_statuses", return_value=snap) as mock_read:
            assert _is_troop_defending(mock_device) is True
            mock_read.assert_called_once_with(mock_device)

    def test_falls_back_to_panel_read_when_cache_stale(self, mock_device):
        stale = _make_snapshot(mock_device, [TroopAction.DEFENDING])
        stale.read_at = time.time() - 60  # 60s old
        fresh = _make_snapshot(mock_device, [TroopAction.DEFENDING])
        with patch("actions.quests.get_troop_status", return_value=stale), \
             patch("actions.quests.read_panel_statuses", return_value=fresh) as mock_read:
            assert _is_troop_defending(mock_device) is True
            mock_read.assert_called_once()

    def test_returns_false_when_panel_read_fails(self, mock_device):
        with patch("actions.quests.get_troop_status", return_value=None), \
             patch("actions.quests.read_panel_statuses", return_value=None):
            assert _is_troop_defending(mock_device) is False


# ---------------------------------------------------------------------------
# _navigate_to_tower
# ---------------------------------------------------------------------------

class TestNavigateToTower:
    def _mock_template(self):
        import numpy as np
        return np.zeros((40, 60, 3), dtype=np.uint8)

    def test_success_uses_friend_tab_and_marker(self, mock_device):
        screen = MagicMock()
        with patch("actions.quests.check_screen", return_value=Screen.MAP), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.logged_tap") as mock_tap, \
             patch("actions.quests.load_screenshot", return_value=screen), \
             patch("actions.quests.find_all_matches", return_value=[(100, 450)]) as mock_find, \
             patch("actions.quests.get_template", return_value=self._mock_template()), \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", return_value=0.0):
            assert _navigate_to_tower(mock_device) is True
            # Should tap Friend tab in RECORD dialog
            mock_tap.assert_any_call(mock_device, 410, 215, "tower_target_friend_tab")
            # Should look for friend_marker.png
            mock_find.assert_called_with(screen, "friend_marker.png",
                                         threshold=0.7, device=mock_device)

    def test_no_friend_marker(self, mock_device):
        t = [0.0]
        def fake_time():
            t[0] += 0.5
            return t[0]
        with patch("actions.quests.check_screen", return_value=Screen.MAP), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.load_screenshot", return_value=MagicMock()), \
             patch("actions.quests.find_all_matches", return_value=[]), \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=fake_time):
            assert _navigate_to_tower(mock_device) == "no_marker"

    def test_target_menu_not_found(self, mock_device):
        with patch("actions.quests.check_screen", return_value=Screen.MAP), \
             patch("actions.quests.tap_image", return_value=False):
            assert _navigate_to_tower(mock_device) is False

    def test_navigates_to_map_first(self, mock_device):
        screen = MagicMock()
        with patch("actions.quests.check_screen", return_value=Screen.UNKNOWN), \
             patch("actions.quests.navigate", return_value=True) as mock_nav, \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.load_screenshot", return_value=screen), \
             patch("actions.quests.find_all_matches", return_value=[(100, 450)]), \
             patch("actions.quests.get_template", return_value=self._mock_template()), \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", return_value=0.0):
            assert _navigate_to_tower(mock_device) is True
            mock_nav.assert_called_once()


# ---------------------------------------------------------------------------
# occupy_tower
# ---------------------------------------------------------------------------

class TestOccupyTower:
    @staticmethod
    def _time_gen(*values):
        """Return a time.time mock that yields values then counts from last."""
        vals = list(values)
        counter = [vals[-1] if vals else 0.0]
        def fake():
            if vals:
                return vals.pop(0)
            counter[0] += 0.5
            return counter[0]
        return fake

    def test_skips_if_already_defending(self, mock_device):
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests._is_troop_defending", return_value=True):
            assert occupy_tower(mock_device) is True

    def test_fails_if_no_troops(self, mock_device):
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests._is_troop_defending", return_value=False), \
             patch("actions.quests.troops_avail", return_value=0):
            assert occupy_tower(mock_device) is False

    def test_deploys_successfully(self, mock_device):
        def find_side_effect(screen, image, threshold=0.8):
            if image == "reinforce_button.png":
                return (0.9, (100, 100), 50, 200)
            return None
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests._is_troop_defending", return_value=False), \
             patch("actions.quests.troops_avail", return_value=3), \
             patch("actions.quests._navigate_to_tower", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.load_screenshot", return_value=MagicMock()), \
             patch("actions.quests.find_image", side_effect=find_side_effect), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.wait_for_image_and_tap", return_value=True), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=self._time_gen(0, 0, 0.5)):
            mock_config.set_device_status = MagicMock()
            assert occupy_tower(mock_device) is True
            assert mock_device in _tower_quest_state

    def test_fails_if_tower_nav_fails(self, mock_device):
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests._is_troop_defending", return_value=False), \
             patch("actions.quests.troops_avail", return_value=3), \
             patch("actions.quests._navigate_to_tower", return_value=False), \
             patch("actions.quests.config") as mock_config:
            mock_config.set_device_status = MagicMock()
            assert occupy_tower(mock_device) is False

    def test_fails_if_reinforce_not_found(self, mock_device):
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests._is_troop_defending", return_value=False), \
             patch("actions.quests.troops_avail", return_value=3), \
             patch("actions.quests._navigate_to_tower", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.load_screenshot", return_value=MagicMock()), \
             patch("actions.quests.find_image", return_value=None), \
             patch("actions.quests.save_failure_screenshot"), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=self._time_gen(0, 0, *[i * 0.5 for i in range(1, 15)])):
            mock_config.set_device_status = MagicMock()
            assert occupy_tower(mock_device) is False

    def test_fails_if_depart_not_found(self, mock_device):
        def find_side_effect(screen, image, threshold=0.8):
            if image == "reinforce_button.png":
                return (0.9, (100, 100), 50, 200)
            return None
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests._is_troop_defending", return_value=False), \
             patch("actions.quests.troops_avail", return_value=3), \
             patch("actions.quests._navigate_to_tower", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.load_screenshot", return_value=MagicMock()), \
             patch("actions.quests.find_image", side_effect=find_side_effect), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.wait_for_image_and_tap", return_value=False), \
             patch("actions.quests.save_failure_screenshot"), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=self._time_gen(0, 0, 0.5)):
            mock_config.set_device_status = MagicMock()
            assert occupy_tower(mock_device) is False

    def test_respects_stop_check(self, mock_device):
        stop = MagicMock(side_effect=[False, True])
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests._is_troop_defending", return_value=False):
            assert occupy_tower(mock_device, stop_check=stop) is False


# ---------------------------------------------------------------------------
# recall_tower_troop
# ---------------------------------------------------------------------------

class TestRecallTowerTroop:
    def test_full_recall_sequence(self, mock_device):
        """Recall succeeds when panel shows no defending troop after sequence."""
        _tower_quest_state[mock_device] = {"deployed_at": time.time()}
        no_defend = _make_snapshot(mock_device, [TroopAction.HOME] * 4)
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.wait_for_image_and_tap", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.read_panel_statuses", return_value=no_defend), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.time.sleep"):
            mock_config.set_device_status = MagicMock()
            assert recall_tower_troop(mock_device) is True
            assert mock_device not in _tower_quest_state

    def test_recall_fails_when_detail_not_found(self, mock_device):
        """Recall returns False when detail_button.png is not found."""
        _tower_quest_state[mock_device] = {"deployed_at": time.time()}
        still_defending = _make_snapshot(mock_device,
                                        [TroopAction.DEFENDING, TroopAction.HOME,
                                         TroopAction.HOME, TroopAction.HOME])
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.wait_for_image_and_tap", return_value=False), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.read_panel_statuses", return_value=still_defending), \
             patch("actions.quests.save_failure_screenshot"), \
             patch("actions.quests._navigate_to_tower", return_value=False), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.time.sleep"):
            mock_config.set_device_status = MagicMock()
            assert recall_tower_troop(mock_device) is False

    def test_recall_fails_when_still_defending(self, mock_device):
        """Recall returns False when troop is still defending after all attempts."""
        _tower_quest_state[mock_device] = {"deployed_at": time.time()}
        still_defending = _make_snapshot(mock_device,
                                        [TroopAction.DEFENDING, TroopAction.HOME,
                                         TroopAction.HOME, TroopAction.HOME])
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.wait_for_image_and_tap", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.read_panel_statuses", return_value=still_defending), \
             patch("actions.quests.save_failure_screenshot"), \
             patch("actions.quests._navigate_to_tower", return_value=False), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.time.sleep"):
            mock_config.set_device_status = MagicMock()
            assert recall_tower_troop(mock_device) is False
            assert mock_device in _tower_quest_state  # not cleared on failure

    def test_fails_if_no_defending_icon(self, mock_device):
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.tap_image", return_value=False), \
             patch("actions.quests.config") as mock_config:
            mock_config.set_device_status = MagicMock()
            assert recall_tower_troop(mock_device) is False

    def test_fails_if_nav_fails(self, mock_device):
        with patch("actions.quests.navigate", return_value=False):
            assert recall_tower_troop(mock_device) is False

    def test_respects_stop_check(self, mock_device):
        stop = MagicMock(side_effect=[False, True])
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.wait_for_image_and_tap", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.time.sleep"):
            mock_config.set_device_status = MagicMock()
            # Should abort mid-recall
            assert recall_tower_troop(mock_device, stop_check=stop) is False


# ---------------------------------------------------------------------------
# _run_tower_quest
# ---------------------------------------------------------------------------

class TestRunTowerQuest:
    def test_deploys_when_not_defending(self, mock_device):
        quests = [
            {"quest_type": QuestType.TOWER, "current": 0, "target": 30, "completed": False},
        ]
        with patch("actions.quests._is_troop_defending_relaxed", return_value=False), \
             patch("actions.quests.occupy_tower", return_value=True) as mock_occ, \
             patch("actions.quests.config") as mock_config:
            mock_config.set_device_status = MagicMock()
            _run_tower_quest(mock_device, quests)
            mock_occ.assert_called_once()

    def test_skips_when_already_defending(self, mock_device):
        quests = [
            {"quest_type": QuestType.FORTRESS, "current": 10, "target": 30, "completed": False},
        ]
        with patch("actions.quests._is_troop_defending_relaxed", return_value=True), \
             patch("actions.quests.occupy_tower") as mock_occ, \
             patch("actions.quests.config") as mock_config:
            mock_config.set_device_status = MagicMock()
            _run_tower_quest(mock_device, quests)
            mock_occ.assert_not_called()

    def test_recalls_when_all_complete(self, mock_device):
        quests = [
            {"quest_type": QuestType.TOWER, "current": 30, "target": 30, "completed": True},
        ]
        with patch("actions.quests._is_troop_defending_relaxed", return_value=True), \
             patch("actions.quests.recall_tower_troop") as mock_recall, \
             patch("actions.quests.config") as mock_config:
            mock_config.get_device_config.return_value = True
            _run_tower_quest(mock_device, quests)
            mock_recall.assert_called_once()

    def test_no_recall_when_complete_but_not_defending(self, mock_device):
        quests = [
            {"quest_type": QuestType.TOWER, "current": 30, "target": 30, "completed": True},
        ]
        with patch("actions.quests._is_troop_defending_relaxed", return_value=False), \
             patch("actions.quests.recall_tower_troop") as mock_recall:
            _run_tower_quest(mock_device, quests)
            mock_recall.assert_not_called()

    def test_handles_mixed_tower_and_fortress(self, mock_device):
        quests = [
            {"quest_type": QuestType.TOWER, "current": 5, "target": 30, "completed": False},
            {"quest_type": QuestType.FORTRESS, "current": 0, "target": 30, "completed": False},
        ]
        with patch("actions.quests._is_troop_defending_relaxed", return_value=False), \
             patch("actions.quests.occupy_tower", return_value=True) as mock_occ, \
             patch("actions.quests.config") as mock_config:
            mock_config.set_device_status = MagicMock()
            _run_tower_quest(mock_device, quests)
            mock_occ.assert_called_once()

    def test_no_tower_quests_no_defending_does_nothing(self, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 0, "target": 15, "completed": False},
        ]
        with patch("actions.quests._is_troop_defending_relaxed", return_value=False), \
             patch("actions.quests.occupy_tower") as mock_occ, \
             patch("actions.quests.recall_tower_troop") as mock_recall:
            _run_tower_quest(mock_device, quests)
            mock_occ.assert_not_called()
            mock_recall.assert_not_called()

    def test_no_tower_quests_recalls_stranded_troop(self, mock_device):
        """If no tower quests exist but a troop is still defending, recall it."""
        quests = [
            {"quest_type": QuestType.TITAN, "current": 0, "target": 15, "completed": False},
        ]
        with patch("actions.quests._is_troop_defending_relaxed", return_value=True), \
             patch("actions.quests.occupy_tower") as mock_occ, \
             patch("actions.quests.recall_tower_troop") as mock_recall, \
             patch("actions.quests.config") as mock_config:
            mock_config.get_device_config.return_value = True
            _run_tower_quest(mock_device, quests)
            mock_occ.assert_not_called()
            mock_recall.assert_called_once_with(mock_device, None)

    def test_marker_error_skips_tower_quest(self, mock_device):
        """If marker error is set for tower, _run_tower_quest skips entirely."""
        _marker_errors[mock_device] = {"Tower ERROR: Friend marker points to an enemy tower"}
        quests = [
            {"quest_type": QuestType.TOWER, "current": 5, "target": 30, "completed": False},
        ]
        with patch("actions.quests._is_troop_defending_relaxed") as mock_def, \
             patch("actions.quests.occupy_tower") as mock_occ, \
             patch("actions.quests.recall_tower_troop") as mock_recall:
            _run_tower_quest(mock_device, quests)
            mock_def.assert_not_called()
            mock_occ.assert_not_called()
            mock_recall.assert_not_called()


# ---------------------------------------------------------------------------
# _navigate_to_tower — duplicate markers
# ---------------------------------------------------------------------------

class TestNavigateToTowerDuplicateMarkers:
    def test_duplicate_markers_returns_string(self, mock_device):
        """2+ friend markers → returns 'duplicate_markers'."""
        with patch("actions.quests.check_screen", return_value=Screen.MAP), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.load_screenshot", return_value=MagicMock()), \
             patch("actions.quests.find_all_matches",
                   return_value=[(100, 400), (100, 600)]), \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", return_value=0.0):
            result = _navigate_to_tower(mock_device)
            assert result == "duplicate_markers"

    def test_no_marker_returns_string(self, mock_device):
        """0 friend markers → returns 'no_marker'."""
        t = [0.0]
        def fake_time():
            t[0] += 0.5
            return t[0]
        with patch("actions.quests.check_screen", return_value=Screen.MAP), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.load_screenshot", return_value=MagicMock()), \
             patch("actions.quests.find_all_matches", return_value=[]), \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=fake_time):
            result = _navigate_to_tower(mock_device)
            assert result == "no_marker"


# ---------------------------------------------------------------------------
# occupy_tower — wrong button detection
# ---------------------------------------------------------------------------

class TestOccupyTowerWrongButton:
    @staticmethod
    def _time_gen(*values):
        """Return a time.time mock that yields values then counts from last."""
        vals = list(values)
        counter = [vals[-1] if vals else 0.0]
        def fake():
            if vals:
                return vals.pop(0)
            counter[0] += 0.5
            return counter[0]
        return fake

    def test_attack_button_sets_marker_error(self, mock_device):
        """Attack button found instead of reinforce → wrong tower, sets error."""
        def find_side_effect(screen, image, threshold=0.8):
            if image == "attack_button.png":
                return (0.9, (100, 100), 50, 200)
            return None
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests._is_troop_defending", return_value=False), \
             patch("actions.quests.troops_avail", return_value=3), \
             patch("actions.quests._navigate_to_tower", return_value=True), \
             patch("actions.quests.logged_tap"), \
             patch("actions.quests.load_screenshot", return_value=MagicMock()), \
             patch("actions.quests.find_image", side_effect=find_side_effect), \
             patch("actions.quests.save_failure_screenshot"), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=self._time_gen(0, 0, 0.5)):
            mock_config.set_device_status = MagicMock()
            result = occupy_tower(mock_device)
            assert result is False
            mock_config.set_device_status.assert_any_call(
                mock_device, "ERROR: Friend Marker \u2192 Enemy Tower!")
            assert mock_device in _marker_errors

    def test_duplicate_markers_from_navigate(self, mock_device):
        """_navigate_to_tower returns 'duplicate_markers' → error status."""
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests._is_troop_defending", return_value=False), \
             patch("actions.quests.troops_avail", return_value=3), \
             patch("actions.quests._navigate_to_tower", return_value="duplicate_markers"), \
             patch("actions.quests.save_failure_screenshot"), \
             patch("actions.quests.config") as mock_config:
            mock_config.set_device_status = MagicMock()
            result = occupy_tower(mock_device)
            assert result is False
            mock_config.set_device_status.assert_any_call(
                mock_device, "ERROR: Duplicate Friend Markers!")
            assert mock_device in _marker_errors
