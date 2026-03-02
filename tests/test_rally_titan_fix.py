"""Tests for rally_titan tap-and-verify fix (actions/titans.py).

Covers: the new retry loop that taps (540, 900) and polls for
rally_button.png instead of blind-tapping.

All ADB and vision calls are mocked — no emulator needed.
"""

import numpy as np
from unittest.mock import patch, MagicMock
from config import Screen
from actions.titans import rally_titan


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


# Shared patches for all rally_titan tests — covers the pre-flight checks
# and search sequence so we can focus on the tap-and-verify behavior.
_COMMON_PATCHES = [
    patch("actions.titans.save_failure_screenshot"),
    patch("actions.titans.capture_departing_portrait", return_value=None),
    patch("actions.titans._save_click_trail"),
    patch("actions.titans.time.sleep"),
    patch("actions.titans.logged_tap"),
    patch("actions.titans.adb_tap"),
    patch("actions.titans.navigate", return_value=True),
    patch("actions.titans.check_screen", return_value=Screen.MAP),
    patch("actions.titans.heal_all"),
    patch("actions.titans.troops_avail", return_value=5),
    patch("actions.titans.read_ap", return_value=(100, 400)),
    patch("actions.titans.wait_for_image_and_tap", return_value=True),
]


class TestRallyTitanTapAndVerify:
    """Test the new tap-and-verify loop for titan selection."""

    @patch("actions.titans.tap_image", return_value=True)
    @patch("actions.titans.find_image")
    @patch("actions.titans.load_screenshot")
    @patch("actions.titans.timed_wait")
    @patch("actions.titans.time.time", side_effect=_make_time_counter(step=0.1))
    def test_popup_found_first_tap(
        self, mock_time, mock_tw, mock_screenshot, mock_find,
        mock_tap_image, mock_device
    ):
        """rally_button.png found on first tap → depart found → returns True."""
        with _apply_common_patches():
            mock_screenshot.return_value = _dummy_screen()

            # timed_wait behavior:
            # - titan_search_menu_open: True
            # - titan_rally_tab_load: True
            # - titan_select_to_search: True
            # - titan_search_complete: True
            # - titan_popup_check: True (rally_button found on first try)
            mock_tw.return_value = True

            # find_image: depart.png found
            mock_find.return_value = (0.9, (400, 1000), 50, 200)

            result = rally_titan(mock_device)

            assert result is True
            # tap_image should have been called for rally_button.png
            mock_tap_image.assert_any_call("rally_button.png", mock_device,
                                           threshold=0.65)

    @patch("actions.titans.tap_image", return_value=True)
    @patch("actions.titans.find_image")
    @patch("actions.titans.load_screenshot")
    @patch("actions.titans.timed_wait")
    @patch("actions.titans.time.time", side_effect=_make_time_counter(step=0.5))
    def test_popup_found_third_tap(
        self, mock_time, mock_tw, mock_screenshot, mock_find,
        mock_tap_image, mock_device
    ):
        """rally_button.png not found on first 2 taps, found on 3rd."""
        with _apply_common_patches():
            mock_screenshot.return_value = _dummy_screen()

            # titan_popup_check: False, False, True (3rd tap)
            # Other timed_waits (search setup): True
            popup_responses = iter([True, True, True, True,
                                    False, False, True])
            mock_tw.side_effect = lambda *a, **kw: next(popup_responses)

            # find_image: depart.png found
            mock_find.return_value = (0.9, (400, 1000), 50, 200)

            result = rally_titan(mock_device)

            assert result is True

    @patch("actions.titans.tap_image", return_value=False)
    @patch("actions.titans.find_image", return_value=None)
    @patch("actions.titans.load_screenshot", return_value=_dummy_screen())
    @patch("actions.titans.timed_wait")
    @patch("actions.titans.time.time", side_effect=_make_time_counter(step=1.0))
    def test_popup_never_found_returns_false(
        self, mock_time, mock_tw, mock_screenshot, mock_find,
        mock_tap_image, mock_device
    ):
        """rally_button.png never found, depart never found → returns False."""
        with _apply_common_patches():
            # All timed_waits for search setup return True,
            # but titan_popup_check always returns False
            call_count = [0]
            def tw_side_effect(device, condition, budget, label, **kw):
                call_count[0] += 1
                if "popup_check" in label:
                    return False
                return True
            mock_tw.side_effect = tw_side_effect

            result = rally_titan(mock_device)

            assert result is False

    @patch("actions.titans.tap_image", return_value=True)
    @patch("actions.titans.find_image")
    @patch("actions.titans.load_screenshot")
    @patch("actions.titans.timed_wait", return_value=True)
    @patch("actions.titans.time.time", side_effect=_make_time_counter(step=0.1))
    def test_not_enough_troops_returns_false(
        self, mock_time, mock_tw, mock_screenshot, mock_find,
        mock_tap_image, mock_device
    ):
        """troops_avail below threshold → returns False immediately."""
        with _apply_common_patches() as patches:
            patches["troops_avail"].return_value = 0
            result = rally_titan(mock_device)
            assert result is False


class TestRallyTitanDepartSettle:
    """Test that the depart settle uses time.sleep instead of lambda:False."""

    @patch("actions.titans.tap_image", return_value=True)
    @patch("actions.titans.find_image")
    @patch("actions.titans.load_screenshot")
    @patch("actions.titans.timed_wait", return_value=True)
    @patch("actions.titans.time.time", side_effect=_make_time_counter(step=0.1))
    def test_depart_settle_uses_sleep(
        self, mock_time, mock_tw, mock_screenshot, mock_find,
        mock_tap_image, mock_device
    ):
        """After depart found, verify time.sleep(1) is called for settle."""
        with _apply_common_patches() as patches:
            mock_screenshot.return_value = _dummy_screen()
            mock_find.return_value = (0.9, (400, 1000), 50, 200)

            result = rally_titan(mock_device)

            assert result is True
            # time.sleep should have been called with 1 for settle
            patches["sleep"].assert_any_call(1)


# ============================================================
# Helper: context manager that applies all common patches
# ============================================================

class _apply_common_patches:
    """Context manager to apply shared patches for rally_titan tests."""

    def __enter__(self):
        self._patchers = []
        self._mocks = {}
        for p in _COMMON_PATCHES:
            patcher = p
            mock = patcher.start()
            self._patchers.append(patcher)
            # Extract the attribute name from the patch target
            attr = patcher.attribute
            self._mocks[attr] = mock
        return self._mocks

    def __exit__(self, *args):
        for p in self._patchers:
            p.stop()
