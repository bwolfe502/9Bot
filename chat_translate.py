"""Chat auto-translation via Claude Haiku.

Detects non-English messages using a Unicode regex heuristic (free, no API call)
and translates them via Claude Haiku with game-aware context. Background worker
thread batches requests for efficiency.

Lifecycle: ``configure(enabled, api_key)`` / ``shutdown()`` — called from
``startup.py``, same pattern as ``training.py``.

Key exports:
    configure            — enable/disable + set API key
    shutdown             — stop worker thread
    request_translation  — fire-and-forget for a single chat message dict
    request_batch_translation — for chat history bursts
"""

import logging
import queue
import re
import threading
import time
from typing import List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Unicode heuristic for non-English detection
# ---------------------------------------------------------------------------

# CJK Unified Ideographs, CJK Ext A/B, Hangul, Cyrillic, Arabic, Thai,
# Devanagari, Japanese Kana, and other non-Latin scripts.
_NON_ASCII_RE = re.compile(
    r"[\u0400-\u04FF"     # Cyrillic
    r"\u0600-\u06FF"      # Arabic
    r"\u0900-\u097F"      # Devanagari
    r"\u0E00-\u0E7F"      # Thai
    r"\u3040-\u309F"      # Hiragana
    r"\u30A0-\u30FF"      # Katakana
    r"\u3400-\u4DBF"      # CJK Extension A
    r"\u4E00-\u9FFF"      # CJK Unified Ideographs
    r"\uAC00-\uD7AF"      # Hangul Syllables
    r"\U00020000-\U0002A6DF"  # CJK Extension B
    r"]"
)

_NON_ASCII_THRESHOLD = 0.15  # >15% non-ASCII chars → needs translation

# Payload types to skip (system notifications, coordinate shares)
_SKIP_PAYLOAD_TYPES = {5, 11}

# ---------------------------------------------------------------------------
#  Module state
# ---------------------------------------------------------------------------

_enabled = False
_api_key = ""
_client = None           # anthropic.Anthropic instance (lazy)
_client_lock = threading.Lock()

_queue: queue.Queue = queue.Queue()
_worker_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

_SYSTEM_PROMPT = (
    "You are a translator for a mobile strategy game chat. "
    "Translate the following message(s) to English. "
    "Game terms to preserve: rally, titan, evil guard (EG), AP (action points), "
    "BL (broken lands), fortress, groot, mithril, territory, reinforce, "
    "depart, march, troop, lineup, alliance, kingdom. "
    "Return ONLY the English translation, nothing else. "
    "If the text is already in English, return exactly an empty string."
)

_BATCH_SIZE = 10
_BATCH_WAIT_S = 0.5   # accumulation window before sending batch


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def configure(enabled: bool, api_key: str = "") -> None:
    """Set the master toggle and API key. Called from startup."""
    global _enabled, _api_key, _client
    was_enabled = _enabled
    _enabled = enabled and bool(api_key)
    _api_key = api_key

    # Reset client when key changes
    with _client_lock:
        _client = None

    if _enabled and not was_enabled:
        _start_worker()
        log.info("Chat translation enabled")
    elif not _enabled and was_enabled:
        _stop_worker()
        log.info("Chat translation disabled")


def shutdown() -> None:
    """Stop the worker thread. Called from startup.shutdown()."""
    _stop_worker()


def request_translation(msg: dict) -> None:
    """Fire-and-forget: enqueue a chat message dict for translation if needed.

    Sets ``msg["translated"]`` to ``None`` (pending) immediately if queued.
    The worker thread will update it to the translation string or ``""``
    (confirmed English) when done.
    """
    if not _enabled:
        return
    if not _needs_translation(msg):
        return
    msg["translated"] = None  # mark as pending
    try:
        _queue.put_nowait(msg)
    except queue.Full:
        pass  # drop silently — not critical


def request_batch_translation(msgs: list) -> None:
    """Enqueue multiple messages (e.g. from chat history pull)."""
    if not _enabled:
        return
    for msg in msgs:
        request_translation(msg)


# ---------------------------------------------------------------------------
#  Detection heuristic
# ---------------------------------------------------------------------------

def _needs_translation(msg: dict) -> bool:
    """Return True if the message likely needs translation."""
    # Skip non-text message types
    payload_type = msg.get("payload_type", 0)
    if payload_type in _SKIP_PAYLOAD_TYPES:
        return False

    # Skip if already translated
    if "translated" in msg:
        return False

    content = msg.get("content", "")
    if not content or len(content.strip()) == 0:
        return False

    # Check source_language hint from protocol
    src_lang = msg.get("source_language", "")
    if src_lang and src_lang.lower() not in ("", "en", "english"):
        return True

    # Unicode heuristic: count non-ASCII script chars
    non_ascii_count = len(_NON_ASCII_RE.findall(content))
    total = len(content.replace(" ", ""))
    if total == 0:
        return False
    ratio = non_ascii_count / total
    return ratio > _NON_ASCII_THRESHOLD


# ---------------------------------------------------------------------------
#  Claude Haiku API
# ---------------------------------------------------------------------------

def _get_client():
    """Lazy-init the Anthropic client."""
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        if not _api_key:
            return None
        try:
            import anthropic
            _client = anthropic.Anthropic(api_key=_api_key)
            return _client
        except Exception:
            log.warning("Failed to create Anthropic client", exc_info=True)
            return None


def _translate_single(content: str) -> str:
    """Translate a single message. Returns translation or "" if English."""
    client = _get_client()
    if client is None:
        return ""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        result = response.content[0].text.strip()
        return result
    except Exception:
        log.warning("Translation API error", exc_info=True)
        return ""


def _translate_batch(items: List[dict]) -> None:
    """Translate a batch of messages and update them in-place."""
    if not items:
        return

    # Single message — skip batch formatting overhead
    if len(items) == 1:
        content = items[0].get("content", "")
        translation = _translate_single(content)
        items[0]["translated"] = translation
        return

    # Build numbered batch prompt
    lines = []
    for i, msg in enumerate(items, 1):
        lines.append(f"{i}. {msg.get('content', '')}")
    batch_text = "\n".join(lines)

    client = _get_client()
    if client is None:
        for msg in items:
            msg["translated"] = ""
        return

    prompt = (
        f"Translate each numbered message to English. "
        f"Return one translation per line, numbered to match. "
        f"If a message is already English, return an empty line for it.\n\n"
        f"{batch_text}"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        result_text = response.content[0].text.strip()
        result_lines = result_text.split("\n")

        for i, msg in enumerate(items):
            if i < len(result_lines):
                line = result_lines[i].strip()
                # Strip leading number prefix like "1. " or "1) "
                line = re.sub(r"^\d+[\.\)]\s*", "", line)
                msg["translated"] = line
            else:
                msg["translated"] = ""
    except Exception:
        log.warning("Batch translation API error", exc_info=True)
        for msg in items:
            msg["translated"] = ""


# ---------------------------------------------------------------------------
#  Background worker
# ---------------------------------------------------------------------------

def _worker_loop() -> None:
    """Drain the queue, batch up messages, translate."""
    log.debug("Translation worker started")
    while not _stop_event.is_set():
        # Wait for first item
        try:
            first = _queue.get(timeout=2.0)
        except queue.Empty:
            continue

        # Accumulate batch
        batch = [first]
        deadline = time.monotonic() + _BATCH_WAIT_S
        while len(batch) < _BATCH_SIZE and time.monotonic() < deadline:
            try:
                remaining = max(0.01, deadline - time.monotonic())
                item = _queue.get(timeout=remaining)
                batch.append(item)
            except queue.Empty:
                break

        if _stop_event.is_set():
            break

        try:
            _translate_batch(batch)
        except Exception:
            log.debug("Translation worker error", exc_info=True)

    log.debug("Translation worker stopped")


def _start_worker() -> None:
    """Start the background worker thread if not already running."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop, daemon=True, name="chat-translate"
    )
    _worker_thread.start()


def _stop_worker() -> None:
    """Stop the background worker thread."""
    global _worker_thread
    _stop_event.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout=3.0)
        _worker_thread = None
