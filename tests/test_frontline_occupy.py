"""Tests for frontline_occupy_loop (territory.py).

Covers the protocol-based frontline occupy cycle:
  1. Protocol picks a target (row, col) with world coords
  2. navigate_to_coord pans camera to tower
  3. teleport_to_tower places castle adjacent
  4. navigate_to_coord recenters camera on tower (THE KEY FIX)
  5. _tap_tower_and_detect_menu opens the tower menu
  6. _do_depart sends the troop

All ADB, vision, and protocol calls are mocked — no emulator needed.
"""

import threading
import numpy as np
import pytest
from unittest.mock import patch, MagicMock, call

import config
from territory import frontline_occupy_loop


DEVICE = "127.0.0.1:9999"

# Protocol grid: red owns (5,5), yellow owns (5,6) with no defender — adjacent frontline target.
MOCK_PROTO_GRID = {
    (5, 5): ("red", None, True),    # our tower, has defender
    (5, 6): ("yellow", None, False), # enemy tower, empty — target
    (5, 7): ("yellow", None, True),  # enemy tower, has defender — skip
    (6, 5): ("red", None, True),     # our tower, has defender
}

# Expected world coords for (5, 6): col*300000+150000, row*300000+150000
EXPECTED_WORLD_X = 6 * 300000 + 150000   # 1950000
EXPECTED_WORLD_Z = 5 * 300000 + 150000   # 1650000


@pytest.fixture(autouse=True)
def reset_state():
    """Reset territory globals before each test."""
    config.MY_TEAM_COLOR = "red"
    config.ENEMY_TEAMS = ["yellow"]
    config.MANUAL_ATTACK_SQUARES.clear()
    config.MANUAL_IGNORE_SQUARES.clear()
    config.LAST_ATTACKED_SQUARE.clear()
    config.PROTOCOL_ACTIVE_DEVICES.add(DEVICE)
    config.DEVICE_TOTAL_TROOPS[DEVICE] = 5
    config.DEVICE_STATUS.clear()
    # Ensure device config returns sensible defaults
    yield
    config.PROTOCOL_ACTIVE_DEVICES.discard(DEVICE)
    config.DEVICE_TOTAL_TROOPS.pop(DEVICE, None)
    config.DEVICE_STATUS.clear()


def _make_stop_after_n(n):
    """Return a stop_check that returns True after n calls."""
    counter = {"count": 0}
    def stop_check():
        counter["count"] += 1
        # Let the loop run through one full cycle, then stop
        return counter["count"] > n
    return stop_check


class TestFrontlineOccupyRecenter:
    """After teleport, the loop must navigate_to_coord back to the tower's
    world coordinates so the tower is at screen center for tapping."""

    @patch("territory._interruptible_sleep", return_value=False)
    @patch("territory.save_failure_screenshot")
    @patch("territory.load_screenshot", return_value=np.zeros((1920, 1080, 3), dtype=np.uint8))
    @patch("territory.find_image", return_value=None)  # no alliance_occupied, no dead
    @patch("territory._do_depart", return_value=True)
    @patch("territory._tap_tower_and_detect_menu", return_value="reinforce")
    @patch("territory.teleport_to_tower", return_value=True)
    @patch("territory.navigate_to_coord", return_value=True)
    @patch("territory._check_and_revive", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.troops_avail", return_value=5)
    @patch("territory.navigate", return_value=True)
    @patch("startup.get_protocol_territory_grid", return_value=MOCK_PROTO_GRID)
    @patch("startup.get_protocol_kvk_tower_troops", return_value={})
    def test_recenter_on_tower_after_teleport(
        self,
        mock_kvk_troops,
        mock_proto_grid,
        mock_navigate,
        mock_troops_avail,
        mock_all_troops_home,
        mock_heal_all,
        mock_revive,
        mock_nav_to_coord,
        mock_tp_to_tower,
        mock_tower_menu,
        mock_do_depart,
        mock_find_image,
        mock_screenshot,
        mock_save_fail,
        mock_sleep,
    ):
        """navigate_to_coord is called TWICE per cycle:
        1. First call: pan camera to tower before teleport
        2. Second call: recenter on tower AFTER teleport (the fix)
        """
        # Stop after one full cycle (generous count to cover all stop_check calls)
        stop_after = _make_stop_after_n(50)

        # Mock device config
        with patch("config.get_device_config", side_effect=lambda dev, key: {
            "auto_heal": False,
            "frontline_occupy_action": "attack",
            "my_team": "red",
            "min_troops": 0,
        }.get(key, None)):
            with patch("config.get_device_enemy_teams", return_value=["yellow"]):
                frontline_occupy_loop(DEVICE, stop_after)

        # navigate_to_coord should have been called at least twice:
        # 1st: initial camera pan to tower (pre-teleport)
        # 2nd: recenter after teleport (the fix we're testing)
        assert mock_nav_to_coord.call_count >= 2, (
            f"Expected navigate_to_coord called >=2 times (pre-teleport + recenter), "
            f"got {mock_nav_to_coord.call_count}"
        )

        # Both calls should use the tower's world coordinates
        for c in mock_nav_to_coord.call_args_list:
            args = c[0]  # positional args: (device, world_x, world_z, ...)
            assert args[0] == DEVICE
            assert args[1] == EXPECTED_WORLD_X, f"Expected world_x={EXPECTED_WORLD_X}, got {args[1]}"
            assert args[2] == EXPECTED_WORLD_Z, f"Expected world_z={EXPECTED_WORLD_Z}, got {args[2]}"

    @patch("territory._interruptible_sleep", return_value=False)
    @patch("territory.save_failure_screenshot")
    @patch("territory.load_screenshot", return_value=np.zeros((1920, 1080, 3), dtype=np.uint8))
    @patch("territory.find_image", return_value=None)
    @patch("territory._do_depart", return_value=True)
    @patch("territory._tap_tower_and_detect_menu", return_value="reinforce")
    @patch("territory.teleport_to_tower", return_value=True)
    @patch("territory.navigate_to_coord", return_value=True)
    @patch("territory._check_and_revive", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.troops_avail", return_value=5)
    @patch("territory.navigate", return_value=True)
    @patch("startup.get_protocol_territory_grid", return_value=MOCK_PROTO_GRID)
    @patch("startup.get_protocol_kvk_tower_troops", return_value={})
    def test_recenter_failure_skips_cycle(
        self,
        mock_kvk_troops,
        mock_proto_grid,
        mock_navigate,
        mock_troops_avail,
        mock_all_troops_home,
        mock_heal_all,
        mock_revive,
        mock_nav_to_coord,
        mock_tp_to_tower,
        mock_tower_menu,
        mock_do_depart,
        mock_find_image,
        mock_screenshot,
        mock_save_fail,
        mock_sleep,
    ):
        """If navigate_to_coord fails after teleport, the cycle is skipped —
        _tap_tower_and_detect_menu and _do_depart should NOT be called."""
        # First call succeeds (pre-teleport), second fails (recenter)
        mock_nav_to_coord.side_effect = [True, False]

        stop_after = _make_stop_after_n(50)

        with patch("config.get_device_config", side_effect=lambda dev, key: {
            "auto_heal": False,
            "frontline_occupy_action": "attack",
            "my_team": "red",
            "min_troops": 0,
        }.get(key, None)):
            with patch("config.get_device_enemy_teams", return_value=["yellow"]):
                frontline_occupy_loop(DEVICE, stop_after)

        # Tower menu and depart should never have been called
        mock_tower_menu.assert_not_called()
        mock_do_depart.assert_not_called()

    @patch("territory._interruptible_sleep", return_value=False)
    @patch("territory.save_failure_screenshot")
    @patch("territory.load_screenshot", return_value=np.zeros((1920, 1080, 3), dtype=np.uint8))
    @patch("territory.find_image", return_value=None)
    @patch("territory._do_depart", return_value=True)
    @patch("territory._tap_tower_and_detect_menu", return_value="reinforce")
    @patch("territory.teleport_to_tower", return_value=True)
    @patch("territory.navigate_to_coord", return_value=True)
    @patch("territory._check_and_revive", return_value=False)
    @patch("territory.heal_all")
    @patch("territory.all_troops_home", return_value=True)
    @patch("territory.troops_avail", return_value=5)
    @patch("territory.navigate", return_value=True)
    @patch("startup.get_protocol_territory_grid", return_value=MOCK_PROTO_GRID)
    @patch("startup.get_protocol_kvk_tower_troops", return_value={})
    def test_depart_called_after_successful_recenter(
        self,
        mock_kvk_troops,
        mock_proto_grid,
        mock_navigate,
        mock_troops_avail,
        mock_all_troops_home,
        mock_heal_all,
        mock_revive,
        mock_nav_to_coord,
        mock_tp_to_tower,
        mock_tower_menu,
        mock_do_depart,
        mock_find_image,
        mock_screenshot,
        mock_save_fail,
        mock_sleep,
    ):
        """Full happy path: teleport -> recenter -> menu -> depart all succeed."""
        stop_after = _make_stop_after_n(50)

        with patch("config.get_device_config", side_effect=lambda dev, key: {
            "auto_heal": False,
            "frontline_occupy_action": "attack",
            "my_team": "red",
            "min_troops": 0,
        }.get(key, None)):
            with patch("config.get_device_enemy_teams", return_value=["yellow"]):
                frontline_occupy_loop(DEVICE, stop_after)

        # Teleport was called
        mock_tp_to_tower.assert_called_once()

        # Tower menu was opened
        mock_tower_menu.assert_called_once()

        # Depart was called with the action type
        mock_do_depart.assert_called_once()
        assert mock_do_depart.call_args[0][2] == "attack"  # action_type arg
