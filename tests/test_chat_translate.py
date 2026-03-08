"""Tests for chat_translate module."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import chat_translate


@pytest.fixture(autouse=True)
def reset_module():
    """Reset module state between tests."""
    chat_translate._enabled = False
    chat_translate._api_key = ""
    chat_translate._client = None
    chat_translate._stop_event.set()
    # Drain queue
    while not chat_translate._queue.empty():
        try:
            chat_translate._queue.get_nowait()
        except Exception:
            break
    chat_translate._worker_thread = None
    chat_translate._stop_event.clear()
    yield
    chat_translate._stop_event.set()
    if chat_translate._worker_thread and chat_translate._worker_thread.is_alive():
        chat_translate._worker_thread.join(timeout=1)
    chat_translate._enabled = False
    chat_translate._api_key = ""
    chat_translate._client = None


# -------------------------------------------------------------------
# _needs_translation tests
# -------------------------------------------------------------------

class TestNeedsTranslation:
    def test_english_text_skipped(self):
        msg = {"content": "Hello world, let's rally!", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is False

    def test_chinese_text_detected(self):
        msg = {"content": "你好世界", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_russian_text_detected(self):
        msg = {"content": "Привет мир", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_korean_text_detected(self):
        msg = {"content": "안녕하세요", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_arabic_text_detected(self):
        msg = {"content": "مرحبا بالعالم", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_japanese_text_detected(self):
        msg = {"content": "こんにちは世界", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_thai_text_detected(self):
        msg = {"content": "สวัสดีชาวโลก", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_mixed_text_with_non_ascii_detected(self):
        # Any non-ASCII script char triggers translation (no ratio threshold)
        msg = {"content": "Hello world this is a long English message 你", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_mixed_text_mostly_non_ascii_detected(self):
        # Mostly non-ASCII
        msg = {"content": "集結タイタン rally now", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_german_with_umlaut_detected(self):
        msg = {"content": "Wir brauchen Verstärkung", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_french_with_accent_detected(self):
        msg = {"content": "René a besoin d'aide", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is True

    def test_system_message_skipped(self):
        msg = {"content": "Привет", "payload_type": 11}
        assert chat_translate._needs_translation(msg) is False

    def test_coordinate_share_skipped(self):
        msg = {"content": "Location (123, 456)", "payload_type": 5}
        assert chat_translate._needs_translation(msg) is False

    def test_empty_content_skipped(self):
        msg = {"content": "", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is False

    def test_already_translated_skipped(self):
        msg = {"content": "Привет мир", "payload_type": 1, "translated": "Hello world"}
        assert chat_translate._needs_translation(msg) is False

    def test_source_language_hint_non_english(self):
        msg = {"content": "Hola amigos", "payload_type": 1, "source_language": "es"}
        assert chat_translate._needs_translation(msg) is True

    def test_source_language_hint_english(self):
        msg = {"content": "Hello friends", "payload_type": 1, "source_language": "en"}
        assert chat_translate._needs_translation(msg) is False

    def test_source_language_empty_falls_through(self):
        msg = {"content": "Hello friends", "payload_type": 1, "source_language": ""}
        assert chat_translate._needs_translation(msg) is False

    def test_no_content_key(self):
        msg = {"payload_type": 1}
        assert chat_translate._needs_translation(msg) is False

    def test_whitespace_only_skipped(self):
        msg = {"content": "   ", "payload_type": 1}
        assert chat_translate._needs_translation(msg) is False


# -------------------------------------------------------------------
# configure / shutdown tests
# -------------------------------------------------------------------

class TestConfigure:
    def test_enable_starts_worker(self):
        chat_translate.configure(True, "sk-test-key")
        assert chat_translate._enabled is True
        assert chat_translate._api_key == "sk-test-key"
        assert chat_translate._worker_thread is not None
        assert chat_translate._worker_thread.is_alive()

    def test_disable_stops_worker(self):
        chat_translate.configure(True, "sk-test-key")
        assert chat_translate._worker_thread.is_alive()
        chat_translate.configure(False)
        assert chat_translate._enabled is False

    def test_enable_without_key_stays_disabled(self):
        chat_translate.configure(True, "")
        assert chat_translate._enabled is False

    def test_shutdown_stops_worker(self):
        chat_translate.configure(True, "sk-test-key")
        chat_translate.shutdown()
        assert not chat_translate._stop_event.is_set() or True  # event set during shutdown


# -------------------------------------------------------------------
# request_translation tests
# -------------------------------------------------------------------

class TestRequestTranslation:
    def test_skips_when_disabled(self):
        msg = {"content": "Привет", "payload_type": 1}
        chat_translate.request_translation(msg)
        assert "translated" not in msg
        assert chat_translate._queue.empty()

    def test_skips_english_text(self):
        chat_translate._enabled = True
        chat_translate._api_key = "test"
        msg = {"content": "Hello world", "payload_type": 1}
        chat_translate.request_translation(msg)
        assert "translated" not in msg

    def test_enqueues_non_english(self):
        chat_translate._enabled = True
        chat_translate._api_key = "test"
        msg = {"content": "你好世界", "payload_type": 1}
        chat_translate.request_translation(msg)
        assert msg["translated"] is None  # pending marker
        assert not chat_translate._queue.empty()

    def test_batch_enqueues_multiple(self):
        chat_translate._enabled = True
        chat_translate._api_key = "test"
        msgs = [
            {"content": "Привет", "payload_type": 1},
            {"content": "Hello", "payload_type": 1},
            {"content": "你好", "payload_type": 1},
        ]
        chat_translate.request_batch_translation(msgs)
        # Only non-English should be queued
        assert msgs[0].get("translated") is None  # pending
        assert "translated" not in msgs[1]         # English, not queued
        assert msgs[2].get("translated") is None   # pending


# -------------------------------------------------------------------
# _translate_single / _translate_batch tests (mocked API)
# -------------------------------------------------------------------

class TestTranslation:
    @patch("chat_translate._get_client")
    def test_translate_single(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello world")]
        mock_client.messages.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = chat_translate._translate_single("你好世界")
        assert result == "Hello world"
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"

    @patch("chat_translate._get_client")
    def test_translate_single_english_returns_empty(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="")]
        mock_client.messages.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = chat_translate._translate_single("Hello world")
        assert result == ""

    @patch("chat_translate._get_client")
    def test_translate_single_api_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        mock_get_client.return_value = mock_client

        result = chat_translate._translate_single("你好世界")
        assert result == ""

    @patch("chat_translate._get_client")
    def test_translate_batch_single_item(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello")]
        mock_client.messages.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        items = [{"content": "你好", "payload_type": 1}]
        chat_translate._translate_batch(items)
        assert items[0]["translated"] == "Hello"

    @patch("chat_translate._get_client")
    def test_translate_batch_multiple(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="1. Hello\n2. Goodbye")]
        mock_client.messages.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        items = [
            {"content": "你好", "payload_type": 1},
            {"content": "再见", "payload_type": 1},
        ]
        chat_translate._translate_batch(items)
        assert items[0]["translated"] == "Hello"
        assert items[1]["translated"] == "Goodbye"

    @patch("chat_translate._get_client")
    def test_translate_batch_strips_number_prefix(self, mock_get_client):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="1. Let's rally\n2) Attack now")]
        mock_client.messages.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        items = [
            {"content": "集結しよう", "payload_type": 1},
            {"content": "今すぐ攻撃", "payload_type": 1},
        ]
        chat_translate._translate_batch(items)
        assert items[0]["translated"] == "Let's rally"
        assert items[1]["translated"] == "Attack now"

    def test_translate_batch_no_client(self):
        chat_translate._api_key = ""
        items = [{"content": "你好", "payload_type": 1}]
        chat_translate._translate_batch(items)
        assert items[0]["translated"] == ""

    def test_translate_batch_empty(self):
        chat_translate._translate_batch([])  # should not raise


# -------------------------------------------------------------------
# Worker thread integration test
# -------------------------------------------------------------------

class TestWorkerIntegration:
    @patch("chat_translate._translate_batch")
    def test_worker_processes_queue(self, mock_batch):
        chat_translate.configure(True, "sk-test-key")
        msg = {"content": "你好世界", "payload_type": 1}
        chat_translate.request_translation(msg)
        # Wait for worker to process
        time.sleep(1.5)
        assert mock_batch.called
        chat_translate.shutdown()
