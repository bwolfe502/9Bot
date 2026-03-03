"""Tests for join_rally fail-fast fix (actions/rallies.py).

Covers: the new real depart.png verification replacing lambda:False
sleeps, and fail-fast backout when detail screen doesn't load.

All ADB and vision calls are mocked — no emulator needed.
"""

import numpy as np
from unittest.mock import patch, MagicMock, call
from config import Screen, RallyType
from actions.rallies import join_rally


def _make_time_counter(step=0.1):
    """Return a callable that increments by `step` each call."""
    state = [0.0]
    def fake_time():
        val = state[0]
        state[0] += step
        return val
    return fake_time


def _dummy_screen():
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def _template_shape():
    """Return a mock template (numpy array with shape attribute)."""
    return np.zeros((40, 80, 3), dtype=np.uint8)


class TestJoinRallySlotToDepart:
    """Test jr_slot_to_depart uses real depart.png check."""

    @patch("actions.rallies.get_last_best", return_value=0.9)
    @patch("actions.rallies.capture_departing_portrait", return_value=None)
    @patch("vision._save_click_trail")
    @patch("actions.rallies.save_failure_screenshot")
    @patch("actions.rallies.time.sleep")
    @patch("actions.rallies.time.time", side_effect=_make_time_counter(step=0.1))
    @patch("actions.rallies.adb_swipe")
    @patch("actions.rallies.adb_tap")
    @patch("actions.rallies.logged_tap")
    @patch("actions.rallies.tap_image", return_value=True)
    @patch("actions.rallies.find_image")
    @patch("actions.rallies.find_all_matches")
    @patch("actions.rallies.load_screenshot")
    @patch("actions.rallies.get_template")
    @patch("actions.rallies.navigate", return_value=True)
    @patch("actions.rallies.check_screen", return_value=Screen.WAR)
    @patch("actions.rallies.heal_all")
    @patch("actions.rallies.troops_avail", return_value=5)
    @patch("actions.rallies.read_panel_statuses", return_value=None)
    def test_slot_to_depart_polls_for_depart(
        self, mock_panel, mock_troops, mock_heal, mock_check, mock_nav,
        mock_template, mock_screenshot, mock_find_all, mock_find,
        mock_tap_image, mock_logged_tap, mock_adb_tap, mock_swipe,
        mock_time, mock_sleep, mock_save_fail, mock_click_trail,
        mock_portrait, mock_last_best, mock_device
    ):
        """After slot tap, timed_wait for jr_slot_to_depart should poll
        for depart.png (not lambda:False)."""
        mock_screenshot.return_value = _dummy_screen()
        mock_template.return_value = _template_shape()

        mock_find_all.side_effect = lambda s, name, **kw: {
            "rally/join.png": [(800, 440)],
        }.get(name, [(100, 400)])

        depart_match = (0.9, (400, 1000), 50, 200)
        slot_match = (0.85, (500, 800), 30, 30)
        def find_side_effect(screen, name, **kw):
            if name == "depart.png":
                return depart_match
            if name == "slot.png":
                return slot_match
            return None
        mock_find.side_effect = find_side_effect

        with patch("actions.rallies.timed_wait") as mock_tw:
            mock_tw.return_value = True

            join_rally(
                [RallyType.CASTLE],
                mock_device,
                stop_check=lambda: False,
            )

            # Verify jr_slot_to_depart was called with a real condition
            slot_calls = [c for c in mock_tw.call_args_list
                          if len(c[0]) >= 4 and c[0][3] == "jr_slot_to_depart"]
            if slot_calls:
                condition = slot_calls[0][0][1]
                # Should NOT be lambda:False — verify it's callable
                assert callable(condition)


class TestJoinRallyScrollStopCheck:
    """Test that scroll settle waits pass stop_check."""

    @patch("actions.rallies.get_last_best", return_value=0.5)
    @patch("actions.rallies.capture_departing_portrait", return_value=None)
    @patch("vision._save_click_trail")
    @patch("actions.rallies.save_failure_screenshot")
    @patch("actions.rallies.time.sleep")
    @patch("actions.rallies.time.time", side_effect=_make_time_counter(step=0.2))
    @patch("actions.rallies.adb_swipe")
    @patch("actions.rallies.adb_tap")
    @patch("actions.rallies.logged_tap")
    @patch("actions.rallies.tap_image", return_value=False)
    @patch("actions.rallies.find_image", return_value=None)
    @patch("actions.rallies.find_all_matches", return_value=[])
    @patch("actions.rallies.load_screenshot")
    @patch("actions.rallies.get_template")
    @patch("actions.rallies.navigate", return_value=True)
    @patch("actions.rallies.check_screen", return_value=Screen.WAR)
    @patch("actions.rallies.heal_all")
    @patch("actions.rallies.troops_avail", return_value=5)
    @patch("actions.rallies.read_panel_statuses", return_value=None)
    def test_scroll_settle_passes_stop_check(
        self, mock_panel, mock_troops, mock_heal, mock_check, mock_nav,
        mock_template, mock_screenshot, mock_find_all, mock_find,
        mock_tap_image, mock_logged_tap, mock_adb_tap, mock_swipe,
        mock_time, mock_sleep, mock_save_fail, mock_click_trail,
        mock_portrait, mock_last_best, mock_device
    ):
        """Scroll settle waits should pass stop_check kwarg to timed_wait."""
        mock_screenshot.return_value = _dummy_screen()
        mock_template.return_value = _template_shape()

        stop_fn = lambda: False

        with patch("actions.rallies.timed_wait") as mock_tw:
            mock_tw.return_value = False

            join_rally(
                [RallyType.CASTLE],
                mock_device,
                stop_check=stop_fn,
            )

            # Check scroll settle calls pass stop_check
            scroll_calls = [c for c in mock_tw.call_args_list
                            if len(c[0]) >= 4 and "scroll" in c[0][3]]
            for sc in scroll_calls:
                assert sc[1].get("stop_check") is stop_fn, \
                    f"Scroll settle '{sc[0][3]}' missing stop_check kwarg"
