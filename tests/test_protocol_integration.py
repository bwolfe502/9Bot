"""Tests for protocol AP integration (startup.get_protocol_ap, vision.read_ap fast path)."""

import json
import sys
import time
import threading
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

import config


# ============================================================
# startup.get_protocol_ap
# ============================================================

class TestGetProtocolAP:
    """Tests for startup.get_protocol_ap() freshness and fallback logic."""

    def test_returns_none_when_no_game_state(self):
        """No game state → None."""
        from startup import get_protocol_ap
        with patch("startup._game_state", None):
            assert get_protocol_ap() is None

    def test_returns_none_when_stale(self):
        """Game state exists but AP data is stale → None."""
        from startup import get_protocol_ap
        mock_state = MagicMock()
        mock_state.is_fresh.return_value = False
        with patch("startup._game_state", mock_state):
            assert get_protocol_ap() is None
            mock_state.is_fresh.assert_called_once_with("ap", max_age_s=10.0)

    def test_returns_none_when_ap_is_none(self):
        """Game state is fresh but AP has never been received → None."""
        from startup import get_protocol_ap
        mock_state = MagicMock()
        mock_state.is_fresh.return_value = True
        mock_state.ap = None
        with patch("startup._game_state", mock_state):
            assert get_protocol_ap() is None

    def test_returns_ap_tuple_when_fresh(self):
        """Fresh AP data → returns (current, max) tuple."""
        from startup import get_protocol_ap
        mock_state = MagicMock()
        mock_state.is_fresh.return_value = True
        mock_state.ap = (120, 400)
        with patch("startup._game_state", mock_state):
            result = get_protocol_ap()
            assert result == (120, 400)


# ============================================================
# startup._start_protocol / _stop_protocol
# ============================================================

class TestProtocolLifecycle:
    """Tests for _start_protocol and _stop_protocol."""

    def setup_method(self):
        """Reset module-level protocol state."""
        import startup
        startup._interceptor_thread = None
        startup._protocol_bus = None
        startup._game_state = None

    def teardown_method(self):
        import startup
        startup._interceptor_thread = None
        startup._protocol_bus = None
        startup._game_state = None

    def test_start_skips_when_already_running(self):
        """If interceptor thread exists, _start_protocol is a no-op."""
        import startup
        sentinel = MagicMock()
        startup._interceptor_thread = sentinel
        startup._start_protocol()
        # Thread was not replaced
        assert startup._interceptor_thread is sentinel

    def test_start_handles_import_error(self):
        """If protocol package is unavailable, _start_protocol logs and returns."""
        import startup
        # Temporarily hide the protocol submodules so the import fails
        saved = {}
        for key in list(sys.modules):
            if key.startswith("protocol"):
                saved[key] = sys.modules.pop(key)
        try:
            with patch.dict(sys.modules, {"protocol.events": None,
                                          "protocol.interceptor": None,
                                          "protocol.game_state": None}):
                startup._start_protocol()
            assert startup._interceptor_thread is None
        finally:
            sys.modules.update(saved)

    def test_stop_when_not_running(self):
        """_stop_protocol when nothing is running is a no-op."""
        import startup
        startup._interceptor_thread = None
        startup._stop_protocol()  # should not raise
        assert startup._interceptor_thread is None

    def test_stop_calls_thread_stop(self):
        """_stop_protocol calls stop() on the interceptor thread."""
        import startup
        mock_thread = MagicMock()
        startup._interceptor_thread = mock_thread
        startup._protocol_bus = MagicMock()
        startup._game_state = MagicMock()

        startup._stop_protocol()

        mock_thread.stop.assert_called_once()
        assert startup._interceptor_thread is None
        assert startup._protocol_bus is None
        assert startup._game_state is None


# ============================================================
# vision.read_ap — protocol fast path
# ============================================================

class TestReadAPProtocol:
    """Tests for the protocol fast path in vision.read_ap()."""

    def setup_method(self):
        self._orig = config.PROTOCOL_ENABLED

    def teardown_method(self):
        config.PROTOCOL_ENABLED = self._orig

    @patch("vision.time.sleep")
    @patch("vision.ocr_read")
    @patch("vision.load_screenshot")
    def test_protocol_disabled_uses_ocr(self, mock_screenshot, mock_ocr, mock_sleep):
        """When protocol is off, read_ap goes straight to OCR."""
        config.PROTOCOL_ENABLED = False
        mock_screenshot.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        mock_ocr.return_value = ["50/200"]

        from vision import read_ap
        result = read_ap("dev1", retries=1)
        assert result == (50, 200)

    @patch("vision.time.sleep")
    @patch("vision.ocr_read")
    @patch("vision.load_screenshot")
    def test_protocol_returns_ap_skips_ocr(self, mock_screenshot, mock_ocr, mock_sleep):
        """When protocol returns fresh AP, OCR is never called."""
        config.PROTOCOL_ENABLED = True
        with patch("startup.get_protocol_ap", return_value=(100, 400)):
            from vision import read_ap
            result = read_ap("dev1", retries=3)

        assert result == (100, 400)
        mock_screenshot.assert_not_called()
        mock_ocr.assert_not_called()

    @patch("vision.time.sleep")
    @patch("vision.ocr_read")
    @patch("vision.load_screenshot")
    def test_protocol_returns_none_falls_through_to_ocr(self, mock_screenshot, mock_ocr, mock_sleep):
        """When protocol returns None (stale), falls through to OCR."""
        config.PROTOCOL_ENABLED = True
        mock_screenshot.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        mock_ocr.return_value = ["75/300"]

        with patch("startup.get_protocol_ap", return_value=None):
            from vision import read_ap
            result = read_ap("dev1", retries=1)

        assert result == (75, 300)
        mock_screenshot.assert_called()

    @patch("vision.time.sleep")
    @patch("vision.ocr_read")
    @patch("vision.load_screenshot")
    def test_protocol_exception_falls_through_to_ocr(self, mock_screenshot, mock_ocr, mock_sleep):
        """If protocol import or call raises, silently falls through to OCR."""
        config.PROTOCOL_ENABLED = True
        mock_screenshot.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        mock_ocr.return_value = ["60/200"]

        with patch("startup.get_protocol_ap", side_effect=RuntimeError("boom")):
            from vision import read_ap
            result = read_ap("dev1", retries=1)

        assert result == (60, 200)


# ============================================================
# config.set_protocol_enabled
# ============================================================

class TestSetProtocolEnabled:
    def setup_method(self):
        self._orig = config.PROTOCOL_ENABLED

    def teardown_method(self):
        config.PROTOCOL_ENABLED = self._orig

    def test_enables(self):
        config.set_protocol_enabled(True)
        assert config.PROTOCOL_ENABLED is True

    def test_disables(self):
        config.set_protocol_enabled(False)
        assert config.PROTOCOL_ENABLED is False

    def test_coerces_to_bool(self):
        config.set_protocol_enabled(1)
        assert config.PROTOCOL_ENABLED is True
        config.set_protocol_enabled(0)
        assert config.PROTOCOL_ENABLED is False


# ============================================================
# web dashboard /api/protocol-toggle
# ============================================================

# Mock tkinter before importing dashboard (same pattern as test_web_dashboard.py)
if "tkinter" not in sys.modules:
    sys.modules["tkinter"] = MagicMock()
if "customtkinter" not in sys.modules:
    sys.modules["customtkinter"] = MagicMock()
if "PIL.ImageTk" not in sys.modules:
    sys.modules["PIL.ImageTk"] = MagicMock()

from web.dashboard import create_app


@pytest.fixture
def app():
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


class TestProtocolToggleEndpoint:
    """Tests for POST /api/protocol-toggle."""

    @patch("web.dashboard._save_settings")
    @patch("web.dashboard._apply_settings")
    @patch("web.dashboard._load_settings", return_value={"protocol_enabled": False})
    def test_toggle_on(self, mock_load, mock_apply, mock_save, client):
        resp = client.post("/api/protocol-toggle")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["enabled"] is True
        # Verify settings were saved with protocol_enabled=True
        saved = mock_save.call_args[0][0]
        assert saved["protocol_enabled"] is True

    @patch("web.dashboard._save_settings")
    @patch("web.dashboard._apply_settings")
    @patch("web.dashboard._load_settings", return_value={"protocol_enabled": True})
    def test_toggle_off(self, mock_load, mock_apply, mock_save, client):
        resp = client.post("/api/protocol-toggle")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["enabled"] is False
        saved = mock_save.call_args[0][0]
        assert saved["protocol_enabled"] is False

    @patch("web.dashboard._save_settings")
    @patch("web.dashboard._apply_settings")
    @patch("web.dashboard._load_settings", return_value={})
    def test_toggle_when_missing_defaults_to_off_then_on(self, mock_load, mock_apply, mock_save, client):
        """When protocol_enabled is missing from settings, defaults to False, toggles to True."""
        resp = client.post("/api/protocol-toggle")
        data = json.loads(resp.data)
        assert data["enabled"] is True
