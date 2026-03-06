"""Frida-based protocol interceptor for Kingdom Guard.

Connects to the Kingdom Guard game process via Frida Gadget (injected into
the APK), loads the hook script (``frida_hook.js``), receives decoded
message data from NetMsgData hooks, decodes protobuf payloads using the
Phase 1 decoder pipeline, and dispatches typed message objects through the
:class:`~protocol.events.EventBus`.

The game uses TLS for transport encryption, so we intercept at the message
framing layer (NetMsgData.FromByte / MakeByte) above the TLS stack.

Connection modes:

- **Gadget mode** (default): Frida Gadget is injected into the APK via
  LIEF NEEDED patching. Connect via ``127.0.0.1:27042`` after
  ``adb forward tcp:27042 tcp:27042``.

- **Server mode**: Traditional frida-server running on the device (requires
  root). Connect via USB or remote device.

Usage::

    from protocol.events import EventBus, EVT_RALLY_CREATED
    from protocol.interceptor import ProtocolInterceptor

    bus = EventBus()
    bus.on(EVT_RALLY_CREATED, lambda msg: print("Rally!", msg))

    interceptor = ProtocolInterceptor(event_bus=bus)
    interceptor.start()

If the ``frida`` package is not installed, this module is still importable
but :meth:`ProtocolInterceptor.start` raises a clear error message.
"""

from __future__ import annotations

import collections
import logging
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .decoder import ProtobufDecoder, decode_protobuf_raw
from .events import (
    EVT_CONNECTED,
    EVT_DISCONNECTED,
    EVT_ERROR,
    EventBus,
    MessageRouter,
)
from .messages import MESSAGE_CLASSES
from .registry import get_wire_registry

__all__ = [
    "ProtocolInterceptor",
    "InterceptorThread",
]

log = logging.getLogger("protocol.interceptor")

# Game package name used to locate the target process.
_GAME_PACKAGE = "com.tap4fun.odin.kingdomguard"

# Hook script lives alongside this module.
_HOOK_SCRIPT_PATH = Path(__file__).with_name("frida_hook.js")

# Path to the proto field map for schema-driven decoding.
_FIELD_MAP_PATH = Path(__file__).resolve().parent / "proto_field_map.json"

# Default Frida Gadget port.
_GADGET_PORT = 27042

# Reconnect delays (seconds).
_RECONNECT_DELAY_FAILED = 10.0
_RECONNECT_DELAY_LOST = 5.0
_RECONNECT_DELAY_MAX = 120.0       # cap for exponential backoff
_RECONNECT_BACKOFF_FACTOR = 2.0    # multiplier per consecutive failure
_MAX_PERMANENT_FAILURES = 3        # give up on version-mismatch errors

# Watchdog: if no messages arrive for this many seconds while connected,
# force a reconnect.  Game heartbeats arrive every ~10s, so 60s silence
# means the hook is dead.
_WATCHDOG_TIMEOUT = 10.0

# CompressedMessage msg_id (BKDR hash of bare "CompressedMessage").
_COMPRESSED_MSG_NAME = "CompressedMessage"


# ------------------------------------------------------------------ #
#  LZ4 decompression (graceful fallback if lz4 not installed)
# ------------------------------------------------------------------ #

try:
    import lz4.block as _lz4_block  # type: ignore[import-untyped]
    _LZ4_AVAILABLE = True
except ImportError:
    _lz4_block = None  # type: ignore[assignment]
    _LZ4_AVAILABLE = False


def _lz4_decompress(data: bytes, uncompressed_size: int) -> Optional[bytes]:
    """Decompress LZ4-block data.  Returns None on failure."""
    if not _LZ4_AVAILABLE:
        log.warning(
            "lz4 package not installed — cannot decompress CompressedMessage. "
            "Install with: pip install lz4"
        )
        return None
    try:
        return _lz4_block.decompress(data, uncompressed_size=uncompressed_size)
    except Exception:
        log.debug("LZ4 decompress failed", exc_info=True)
        return None


# ------------------------------------------------------------------ #
#  Graceful Frida import
# ------------------------------------------------------------------ #

try:
    import frida  # type: ignore[import-untyped]

    _FRIDA_AVAILABLE = True
except ImportError:
    frida = None  # type: ignore[assignment]
    _FRIDA_AVAILABLE = False


def _require_frida() -> None:
    """Raise a helpful error if Frida is not installed."""
    if not _FRIDA_AVAILABLE:
        raise RuntimeError(
            "The 'frida' package is required for protocol interception. "
            "Install it with:  pip install frida frida-tools"
        )


# ------------------------------------------------------------------ #
#  ProtocolInterceptor
# ------------------------------------------------------------------ #

class ProtocolInterceptor:
    """Connects to Kingdom Guard via Frida Gadget and intercepts protocol messages.

    Parameters
    ----------
    gadget_port : int
        TCP port where Frida Gadget is listening (default 27042).
        Set to 0 to use legacy frida-server mode via USB.
    device_id : str, optional
        ADB device ID for frida-server mode. Ignored in gadget mode.
    event_bus : EventBus, optional
        Event bus for dispatching decoded messages.
    """

    def __init__(
        self,
        gadget_port: int = _GADGET_PORT,
        device_id: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._gadget_port = gadget_port
        self._device_id = device_id
        self._bus = event_bus or EventBus()
        self._router = MessageRouter(self._bus)
        self._registry = get_wire_registry()

        # Frida handles (set by start, cleared by stop).
        self._frida_device: Any = None
        self._frida_session: Any = None
        self._frida_script: Any = None

        # Decoder (lazy-initialised in start).
        self._decoder: Optional[ProtobufDecoder] = None

        # Connection state.
        self._connected = False
        self._start_time: Optional[float] = None

        # Statistics.
        self._stats_lock = threading.Lock()
        self._messages_received = 0
        self._messages_sent = 0
        self._bytes_received = 0
        self._bytes_sent = 0
        self._errors = 0
        self._last_message_time: Optional[float] = None
        self._msg_type_counts: Dict[str, int] = collections.defaultdict(int)

        # Compressed message handling.
        self._compressed_msg_id: Optional[int] = None

    # ---------------------------------------------------------------- #
    #  Public API
    # ---------------------------------------------------------------- #

    def start(self) -> bool:
        """Connect to Frida Gadget, attach, and load hooks.

        Returns ``True`` if the hooks were loaded successfully.
        """
        _require_frida()

        try:
            self._frida_device = self._get_frida_device()

            if self._gadget_port > 0:
                log.info(
                    "Connecting to Frida on port %d",
                    self._gadget_port,
                )

            # Try to find the game process by package name first
            # (works for both gadget and frida-server modes).
            pid = self._find_game_pid()
            if pid is not None:
                log.info("Attaching to %s (PID %d)", _GAME_PACKAGE, pid)
                self._frida_session = self._frida_device.attach(pid)
            elif self._gadget_port > 0:
                # Pure gadget fallback: only one process on the device
                procs = self._frida_device.enumerate_processes()
                if procs:
                    pid = procs[0].pid
                    log.info("Gadget host process: PID=%d name=%s", pid, procs[0].name)
                    self._frida_session = self._frida_device.attach(pid)
                else:
                    log.warning("No processes found on Frida device")
                    return False
            else:
                log.info(
                    "Game process %s not found on device %s",
                    _GAME_PACKAGE,
                    self._device_id or "(USB)",
                )
                return False

            self._frida_session.on("detached", self._on_session_detached)

            script_source = self._load_hook_script()
            self._frida_script = self._frida_session.create_script(script_source)
            self._frida_script.on("message", self._on_frida_message)
            self._frida_script.load()

            self._connected = True
            self._start_time = time.monotonic()

            # Look up CompressedMessage ID for decompression.
            self._compressed_msg_id = self._registry.id(_COMPRESSED_MSG_NAME)

            # Initialise the schema-driven decoder if the field map exists.
            if _FIELD_MAP_PATH.exists():
                self._decoder = ProtobufDecoder(str(_FIELD_MAP_PATH))
                log.info("Loaded proto field map from %s", _FIELD_MAP_PATH)
            else:
                log.warning(
                    "Proto field map not found at %s — using raw decoding only",
                    _FIELD_MAP_PATH,
                )

            self._bus.emit(EVT_CONNECTED, self._device_id)
            log.info("Hooks loaded — interception active")
            return True

        except Exception as exc:
            self._cleanup()
            # Re-raise ProtocolError (version mismatch) — caller should
            # not retry indefinitely on these.
            _frida = sys.modules.get("frida")
            if _frida and isinstance(exc, _frida.ProtocolError):
                raise
            log.exception("Failed to start Frida interception")
            return False

    def stop(self) -> None:
        """Detach from the game and clean up the Frida session."""
        log.info("Stopping interceptor")
        self._cleanup()

    def is_connected(self) -> bool:
        """Whether the Frida session is active and hooks are loaded."""
        return self._connected

    @property
    def stats(self) -> dict:
        """Return interception statistics as a plain dict."""
        with self._stats_lock:
            uptime = (
                time.monotonic() - self._start_time
                if self._start_time is not None
                else 0.0
            )
            mps = (
                self._messages_received / uptime if uptime > 0 else 0.0
            )
            top = sorted(
                self._msg_type_counts.items(),
                key=lambda kv: kv[1],
                reverse=True,
            )[:10]

            return {
                "connected": self._connected,
                "mode": "gadget" if self._gadget_port > 0 else "server",
                "messages_received": self._messages_received,
                "messages_sent": self._messages_sent,
                "bytes_received": self._bytes_received,
                "bytes_sent": self._bytes_sent,
                "errors": self._errors,
                "uptime_seconds": round(uptime, 2),
                "last_message_time": self._last_message_time,
                "messages_per_second": round(mps, 2),
                "top_message_types": top,
            }

    def on_message(self, msg_name: str, handler: Callable[..., Any]) -> None:
        """Register *handler* for a specific decoded message type."""
        self._bus.on_message(msg_name, handler)

    # ---------------------------------------------------------------- #
    #  Frida callbacks (called from Frida's thread)
    # ---------------------------------------------------------------- #

    def _on_frida_message(self, message: dict, data: Optional[bytes]) -> None:
        """Frida message callback — dispatches by payload type."""
        if message.get("type") == "error":
            log.error(
                "Frida script error: %s",
                message.get("description", message),
            )
            with self._stats_lock:
                self._errors += 1
            return

        payload = message.get("payload")
        if not isinstance(payload, dict):
            log.debug("Ignoring non-dict Frida payload: %s", message)
            return

        msg_type = payload.get("type")

        try:
            if msg_type == "recv":
                self._handle_recv(payload, data)
            elif msg_type == "send":
                self._handle_send(payload, data)
            else:
                log.debug("Unknown Frida message type: %s", msg_type)
        except Exception:
            log.exception("Error handling Frida message (type=%s)", msg_type)
            with self._stats_lock:
                self._errors += 1

    def _on_session_detached(self, reason: str, crash: Any = None) -> None:
        """Called when the Frida session is lost."""
        log.warning("Frida session detached: reason=%s", reason)
        self._connected = False
        self._bus.emit(EVT_DISCONNECTED, reason)

    # ---------------------------------------------------------------- #
    #  Message handlers
    # ---------------------------------------------------------------- #

    def _handle_recv(self, payload: dict, data: Optional[bytes]) -> None:
        """Process inbound message from the server.

        The hook sends: {type: "recv", msgId: <uint32>, len: <int>}
        with binary data = raw protobuf payload (no msg_id prefix).
        """
        msg_id = payload.get("msgId", 0)
        payload_len = payload.get("len", 0)

        with self._stats_lock:
            self._messages_received += 1
            self._bytes_received += payload_len
            self._last_message_time = time.time()

        proto_data = data if data else b""
        self._decode_and_dispatch(msg_id, proto_data, direction="recv")

    def _handle_send(self, payload: dict, data: Optional[bytes]) -> None:
        """Process outbound message from the client."""
        msg_id = payload.get("msgId", 0)
        payload_len = payload.get("len", 0)

        with self._stats_lock:
            self._messages_sent += 1
            self._bytes_sent += payload_len
            self._last_message_time = time.time()

        proto_data = data if data else b""
        self._decode_and_dispatch(msg_id, proto_data, direction="send")

    # ---------------------------------------------------------------- #
    #  Decode pipeline
    # ---------------------------------------------------------------- #

    def _decode_and_dispatch(
        self,
        msg_id: int,
        payload: bytes,
        direction: str,
    ) -> None:
        """Look up the message name, decode the protobuf, and dispatch."""
        msg_name = self._registry.name(msg_id)

        if msg_name is None:
            log.debug(
                "[%s] Unknown msg_id 0x%08X (%d bytes payload)",
                direction,
                msg_id,
                len(payload),
            )
            with self._stats_lock:
                self._msg_type_counts[f"UNKNOWN:0x{msg_id:08X}"] += 1
            return

        log.debug(
            "[%s] %s (0x%08X) — %d bytes",
            direction,
            msg_name,
            msg_id,
            len(payload),
        )

        with self._stats_lock:
            self._msg_type_counts[msg_name] += 1

        # Handle CompressedMessage: decompress and re-dispatch the inner message.
        if (
            self._compressed_msg_id is not None
            and msg_id == self._compressed_msg_id
        ):
            self._handle_compressed(payload, direction)
            return

        # Decode the protobuf payload.
        try:
            if self._decoder is not None and self._decoder.has_schema(msg_name):
                field_dict = self._decoder.decode(msg_name, payload)
            else:
                field_dict = decode_protobuf_raw(payload)
        except Exception:
            log.warning(
                "Failed to decode %s payload (%d bytes)",
                msg_name,
                len(payload),
                exc_info=True,
            )
            with self._stats_lock:
                self._errors += 1
            return

        # Construct a typed dataclass if one exists.
        msg_cls = MESSAGE_CLASSES.get(msg_name)
        if msg_cls is not None and hasattr(msg_cls, "from_dict"):
            try:
                typed_msg = msg_cls.from_dict(field_dict)
            except Exception:
                log.warning(
                    "Failed to construct %s from decoded dict",
                    msg_name,
                    exc_info=True,
                )
                typed_msg = field_dict
        else:
            typed_msg = field_dict

        # Dispatch through the MessageRouter.
        try:
            self._router.route(msg_name, typed_msg)
        except Exception:
            log.exception("Error in message dispatch for %s", msg_name)
            with self._stats_lock:
                self._errors += 1

    def _handle_compressed(self, payload: bytes, direction: str) -> None:
        """Decompress a CompressedMessage and re-dispatch the inner message.

        CompressedMessage layout (protobuf):
            field 1 = LZ4-block-compressed data (bytes)
            field 2 = srcLength — original uncompressed length (int32)

        Inner format (after LZ4 decompression):
            [4-byte msg_id (little-endian)] [protobuf payload]
        """
        try:
            raw = decode_protobuf_raw(payload)
            compressed_data = raw.get(1, [b""])[0]
            if not isinstance(compressed_data, bytes) or not compressed_data:
                log.debug("CompressedMessage has no field 1 data")
                return

            src_length = raw.get(2, [0])[0]
            if not isinstance(src_length, int) or src_length <= 0:
                log.warning("CompressedMessage missing srcLength (field 2)")
                return

            decompressed = _lz4_decompress(compressed_data, src_length)
            if decompressed is None:
                log.warning(
                    "LZ4 decompression failed for CompressedMessage "
                    "(%d bytes compressed, srcLength=%d)",
                    len(compressed_data),
                    src_length,
                )
                with self._stats_lock:
                    self._errors += 1
                return

            if len(decompressed) < 4:
                log.warning(
                    "Decompressed data too short: %d bytes",
                    len(decompressed),
                )
                return

            inner_msg_id = struct.unpack("<I", decompressed[:4])[0]
            inner_payload = decompressed[4:]

            log.debug(
                "[%s] CompressedMessage → inner msg_id 0x%08X (%d bytes)",
                direction,
                inner_msg_id,
                len(inner_payload),
            )

            # Re-dispatch the inner message
            self._decode_and_dispatch(inner_msg_id, inner_payload, direction)

        except Exception:
            log.warning(
                "Error processing CompressedMessage",
                exc_info=True,
            )
            with self._stats_lock:
                self._errors += 1

    # ---------------------------------------------------------------- #
    #  Frida device / process helpers
    # ---------------------------------------------------------------- #

    def _get_frida_device(self) -> Any:
        """Obtain the Frida device handle.

        In gadget mode, connects to ``127.0.0.1:<gadget_port>`` via
        ``add_remote_device``.  Requires ``adb forward tcp:<port> tcp:<port>``
        to have been run first.

        In server mode, tries remote device, then USB.
        """
        if self._gadget_port > 0:
            addr = f"127.0.0.1:{self._gadget_port}"
            log.info("Connecting to Frida Gadget at %s", addr)
            manager = frida.get_device_manager()
            return manager.add_remote_device(addr)

        # Legacy frida-server mode
        if self._device_id and ":" in self._device_id:
            host_port = self._device_id
            log.info("Connecting to Frida on remote device %s", host_port)
            manager = frida.get_device_manager()
            return manager.add_remote_device(host_port)

        if self._device_id:
            log.info("Looking up Frida device by id: %s", self._device_id)
            manager = frida.get_device_manager()
            for dev in manager.enumerate_devices():
                if dev.id == self._device_id:
                    return dev
            log.warning(
                "Device %s not found in enumeration — trying USB",
                self._device_id,
            )

        try:
            dev = frida.get_remote_device()
            log.info("Using Frida remote device: %s", dev)
            return dev
        except Exception:
            log.debug("No remote Frida device — falling back to USB")

        dev = frida.get_usb_device(timeout=5)
        log.info("Using Frida USB device: %s", dev)
        return dev

    def _find_game_pid(self) -> Optional[int]:
        """Find the PID of the Kingdom Guard process on the attached device."""
        if self._frida_device is None:
            return None

        for proc in self._frida_device.enumerate_processes():
            proc_ident = getattr(proc, "identifier", "") or ""
            if proc.name == _GAME_PACKAGE or proc_ident == _GAME_PACKAGE:
                log.info("Found game process: PID=%d name=%s", proc.pid, proc.name)
                return proc.pid

        for proc in self._frida_device.enumerate_processes():
            proc_ident = getattr(proc, "identifier", "") or ""
            if _GAME_PACKAGE in proc.name or _GAME_PACKAGE in proc_ident:
                log.info(
                    "Found game process (fuzzy): PID=%d name=%s",
                    proc.pid,
                    proc.name,
                )
                return proc.pid

        return None

    def _load_hook_script(self) -> str:
        """Read ``frida_hook.js`` from the same directory as this module."""
        if not _HOOK_SCRIPT_PATH.exists():
            raise FileNotFoundError(
                f"Hook script not found: {_HOOK_SCRIPT_PATH}. "
                f"Ensure frida_hook.js is in the protocol/ directory."
            )
        return _HOOK_SCRIPT_PATH.read_text(encoding="utf-8")

    # ---------------------------------------------------------------- #
    #  Cleanup
    # ---------------------------------------------------------------- #

    def _cleanup(self) -> None:
        """Release all Frida resources."""
        self._connected = False

        if self._frida_script is not None:
            try:
                self._frida_script.unload()
            except Exception:
                log.debug("Script unload failed (already unloaded?)")
            self._frida_script = None

        if self._frida_session is not None:
            try:
                self._frida_session.detach()
            except Exception:
                log.debug("Session detach failed (already detached?)")
            self._frida_session = None

        self._frida_device = None


# ------------------------------------------------------------------ #
#  InterceptorThread
# ------------------------------------------------------------------ #

class InterceptorThread(threading.Thread):
    """Daemon thread that manages a :class:`ProtocolInterceptor` lifecycle.

    Handles initial connection, reconnection on session loss, and graceful
    shutdown.

    Parameters
    ----------
    event_bus : EventBus
        Event bus shared with the rest of the application.
    gadget_port : int
        Frida Gadget TCP port (default 27042). Set to 0 for server mode.
    device_id : str, optional
        ADB device ID (only used in server mode).
    """

    def __init__(
        self,
        event_bus: EventBus,
        gadget_port: int = _GADGET_PORT,
        device_id: Optional[str] = None,
        pre_connect=None,
    ) -> None:
        label = (
            f"gadget:{gadget_port}"
            if gadget_port > 0
            else f"server:{device_id or 'usb'}"
        )
        super().__init__(daemon=True, name=f"interceptor-{label}")
        self.gadget_port = gadget_port
        self.device_id = device_id
        self.event_bus = event_bus
        self._stop_event = threading.Event()
        self._interceptor: Optional[ProtocolInterceptor] = None
        self._pre_connect = pre_connect

    def run(self) -> None:
        """Main loop: connect, handle disconnects, auto-reconnect."""
        log.info("InterceptorThread started (%s)", self.name)

        consecutive_fails = 0
        permanent_fails = 0
        delay = _RECONNECT_DELAY_FAILED

        while not self._stop_event.is_set():
            if self._pre_connect is not None:
                try:
                    self._pre_connect()
                except Exception:
                    log.debug("pre_connect callback failed", exc_info=True)

            self._interceptor = ProtocolInterceptor(
                gadget_port=self.gadget_port,
                device_id=self.device_id,
                event_bus=self.event_bus,
            )

            try:
                success = self._interceptor.start()
            except Exception as exc:
                # ProtocolError = version mismatch or incompatible gadget.
                # These won't fix themselves — limit retries.
                success = False
                permanent_fails += 1
                if permanent_fails == 1:
                    log.error(
                        "Frida protocol error (attempt %d/%d): %s",
                        permanent_fails, _MAX_PERMANENT_FAILURES, exc,
                    )
                if permanent_fails >= _MAX_PERMANENT_FAILURES:
                    log.error(
                        "Giving up after %d permanent failures — "
                        "re-patch the APK or update the frida Python package "
                        "to match the Gadget version",
                        permanent_fails,
                    )
                    break

            if not success:
                consecutive_fails += 1
                self._interceptor = None

                # Log full message on first failure, one-liner afterwards.
                if consecutive_fails == 1:
                    log.warning(
                        "Failed to connect — retrying in %.0fs", delay,
                    )
                elif consecutive_fails % 10 == 0:
                    log.warning(
                        "Still failing to connect (%d attempts) — "
                        "retrying in %.0fs",
                        consecutive_fails, delay,
                    )

                if self._stop_event.wait(timeout=delay):
                    break

                # Exponential backoff, capped.
                delay = min(delay * _RECONNECT_BACKOFF_FACTOR,
                            _RECONNECT_DELAY_MAX)
                continue

            # Connected — reset backoff state.
            consecutive_fails = 0
            permanent_fails = 0
            delay = _RECONNECT_DELAY_FAILED

            # Wait until disconnect, stop signal, or watchdog timeout.
            while (
                not self._stop_event.is_set()
                and self._interceptor is not None
                and self._interceptor.is_connected()
            ):
                self._stop_event.wait(timeout=1.0)
                # Watchdog: force reconnect if no messages for _WATCHDOG_TIMEOUT
                if (
                    self._interceptor is not None
                    and self._interceptor.is_connected()
                    and self._interceptor._last_message_time is not None
                ):
                    silence = time.time() - self._interceptor._last_message_time
                    if silence > _WATCHDOG_TIMEOUT:
                        log.warning(
                            "Watchdog: no messages for %.0fs — forcing reconnect",
                            silence,
                        )
                        self._interceptor.stop()
                        self._interceptor = None
                        break

            # If we get here without a stop signal, the session was lost.
            if not self._stop_event.is_set():
                log.warning(
                    "Connection lost — reconnecting in %.0fs",
                    _RECONNECT_DELAY_LOST,
                )
                if self._interceptor is not None:
                    self._interceptor.stop()
                    self._interceptor = None
                if self._stop_event.wait(timeout=_RECONNECT_DELAY_LOST):
                    break

        # Final cleanup.
        if self._interceptor is not None:
            self._interceptor.stop()
            self._interceptor = None

        log.info("InterceptorThread stopped (%s)", self.name)

    def stop(self) -> None:
        """Signal the thread to stop and clean up."""
        self._stop_event.set()
        if self._interceptor is not None:
            self._interceptor.stop()

    @property
    def is_connected(self) -> bool:
        """Whether the underlying interceptor has an active Frida session."""
        return self._interceptor is not None and self._interceptor.is_connected()

    @property
    def stats(self) -> Optional[dict]:
        """Proxy to the underlying interceptor's stats, or ``None``."""
        if self._interceptor is not None:
            return self._interceptor.stats
        return None
