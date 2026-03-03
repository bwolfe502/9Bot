"""Lightweight event system for dispatching decoded protocol messages.

Provides an :class:`EventBus` for pub/sub-style event handling and a
:class:`MessageRouter` that maps decoded protocol message names to
semantic game events.

Thread-safe: handlers may be registered from any thread while ``emit``
is called from the protocol I/O thread.  Exceptions in handlers are
logged and never propagate to the emitter.

Usage::

    >>> bus = EventBus()
    >>> bus.on(EVT_RALLY_CREATED, lambda msg: print("Rally!", msg))
    >>> router = MessageRouter(bus)
    >>> router.route("RallyNtf", some_rally_msg)  # fires EVT_RALLY_CREATED
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    # Event constants — protocol lifecycle
    "EVT_CONNECTED",
    "EVT_DISCONNECTED",
    "EVT_ERROR",
    # Event constants — game events
    "EVT_RALLY_CREATED",
    "EVT_RALLY_ENDED",
    "EVT_QUEST_CHANGED",
    "EVT_AP_CHANGED",
    "EVT_RESOURCES_CHANGED",
    "EVT_CHAT_MESSAGE",
    "EVT_ATTACK_INCOMING",
    "EVT_ENTITY_SPAWNED",
    "EVT_BATTLE_RESULT",
    "EVT_CITY_BURNING",
    "EVT_BUFF_CHANGED",
    "EVT_TROOPS_CHANGED",
    "EVT_BROADCAST",
    # Classes
    "EventBus",
    "MessageRouter",
]

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Protocol lifecycle events
# ------------------------------------------------------------------ #

EVT_CONNECTED: str = "protocol:connected"
EVT_DISCONNECTED: str = "protocol:disconnected"
EVT_ERROR: str = "protocol:error"

# ------------------------------------------------------------------ #
#  High-value game events (derived from Ntf messages)
# ------------------------------------------------------------------ #

EVT_RALLY_CREATED: str = "game:rally_created"
EVT_RALLY_ENDED: str = "game:rally_ended"
EVT_QUEST_CHANGED: str = "game:quest_changed"
EVT_AP_CHANGED: str = "game:ap_changed"
EVT_RESOURCES_CHANGED: str = "game:resources_changed"
EVT_CHAT_MESSAGE: str = "game:chat_message"
EVT_ATTACK_INCOMING: str = "game:attack_incoming"
EVT_ENTITY_SPAWNED: str = "game:entity_spawned"
EVT_BATTLE_RESULT: str = "game:battle_result"
EVT_CITY_BURNING: str = "game:city_burning"
EVT_BUFF_CHANGED: str = "game:buff_changed"
EVT_TROOPS_CHANGED: str = "game:troops_changed"
EVT_BROADCAST: str = "game:broadcast"


# ------------------------------------------------------------------ #
#  EventBus
# ------------------------------------------------------------------ #

class EventBus:
    """Thread-safe publish/subscribe event bus.

    Handlers are stored per event name and invoked in registration order.
    Any handler that raises is logged and skipped — it never crashes the
    bus or prevents subsequent handlers from running.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[..., Any]]] = {}
        self._once_wrappers: Dict[int, Callable[..., Any]] = {}
        self._lock = threading.Lock()

    # -- registration ------------------------------------------------ #

    def on(self, event_name: str, handler: Callable[..., Any]) -> None:
        """Register *handler* to be called whenever *event_name* is emitted."""
        with self._lock:
            self._handlers.setdefault(event_name, []).append(handler)

    def once(self, event_name: str, handler: Callable[..., Any]) -> None:
        """Register *handler* to fire exactly once, then auto-unregister."""
        def wrapper(*args: Any, **kwargs: Any) -> None:
            self.off(event_name, wrapper)
            handler(*args, **kwargs)

        # Store the mapping so that ``off(event_name, handler)`` can also
        # remove a ``once``-registered handler before it fires.
        with self._lock:
            self._once_wrappers[id(handler)] = wrapper
            self._handlers.setdefault(event_name, []).append(wrapper)

    def off(self, event_name: str, handler: Callable[..., Any]) -> None:
        """Unregister *handler* from *event_name*.

        Silently does nothing if *handler* was not registered.  Also
        handles removing a handler previously registered via :meth:`once`.
        """
        with self._lock:
            handlers = self._handlers.get(event_name)
            if handlers is None:
                return

            # Try direct removal first.
            try:
                handlers.remove(handler)
                return
            except ValueError:
                pass

            # Fall back to removing the once-wrapper for this handler.
            wrapper = self._once_wrappers.pop(id(handler), None)
            if wrapper is not None:
                try:
                    handlers.remove(wrapper)
                except ValueError:
                    pass

    # -- emission ---------------------------------------------------- #

    def emit(self, event_name: str, *args: Any, **kwargs: Any) -> None:
        """Invoke all handlers registered for *event_name*.

        Each handler is called with the supplied positional and keyword
        arguments.  Exceptions are logged at ERROR level and swallowed.
        """
        with self._lock:
            handlers = list(self._handlers.get(event_name, []))

        for handler in handlers:
            try:
                handler(*args, **kwargs)
            except Exception:
                log.exception(
                    "Handler %r for event %r raised — skipping",
                    handler,
                    event_name,
                )

    # -- message sugar ----------------------------------------------- #

    def on_message(self, msg_name: str, handler: Callable[..., Any]) -> None:
        """Shortcut for ``on(f"msg:{msg_name}", handler)``."""
        self.on(f"msg:{msg_name}", handler)

    def emit_message(self, msg_name: str, msg: object) -> None:
        """Shortcut for ``emit(f"msg:{msg_name}", msg)``."""
        self.emit(f"msg:{msg_name}", msg)

    # -- introspection ----------------------------------------------- #

    def handler_count(self, event_name: str) -> int:
        """Return the number of handlers currently registered for *event_name*."""
        with self._lock:
            return len(self._handlers.get(event_name, []))

    def clear(self) -> None:
        """Remove all handlers and once-wrappers."""
        with self._lock:
            self._handlers.clear()
            self._once_wrappers.clear()

    def __repr__(self) -> str:
        with self._lock:
            total = sum(len(v) for v in self._handlers.values())
            events = len(self._handlers)
        return f"EventBus({total} handlers across {events} events)"


# ------------------------------------------------------------------ #
#  Default routing table: Ntf message name -> EVT_* constant
# ------------------------------------------------------------------ #

DEFAULT_ROUTING_TABLE: Dict[str, str] = {
    "RallyNtf": EVT_RALLY_CREATED,
    "RallyDelNtf": EVT_RALLY_ENDED,
    "QuestChangeNtf": EVT_QUEST_CHANGED,
    "PowerNtf": EVT_AP_CHANGED,
    "AssetNtf": EVT_RESOURCES_CHANGED,
    "ChatOneMsgNtf": EVT_CHAT_MESSAGE,
    "IntelligencesNtf": EVT_ATTACK_INCOMING,
    "EntitiesNtf": EVT_ENTITY_SPAWNED,
    "BattleResultNtf": EVT_BATTLE_RESULT,
    "CombustionStateNtf": EVT_CITY_BURNING,
    "BuffNtf": EVT_BUFF_CHANGED,
    "TroopBackNtf": EVT_TROOPS_CHANGED,
    "TroopMarchNtf": EVT_TROOPS_CHANGED,
    "TroopStateChangeNtf": EVT_TROOPS_CHANGED,
    "BroadcastGameNtf": EVT_BROADCAST,
}


# ------------------------------------------------------------------ #
#  MessageRouter
# ------------------------------------------------------------------ #

class MessageRouter:
    """Routes decoded protocol messages through an :class:`EventBus`.

    For every routed message two things happen:

    1. The *raw* message event ``msg:{msg_name}`` is always emitted so
       that callers who ``on_message("RallyNtf", ...)`` receive it.
    2. If the message name appears in the routing table, the
       corresponding ``EVT_*`` event is also emitted (optionally with a
       transformed payload).

    Parameters
    ----------
    bus : EventBus
        The event bus to emit on.
    routing_table : dict, optional
        Mapping of ``msg_name`` to ``event_name``.  Defaults to
        :data:`DEFAULT_ROUTING_TABLE`.
    """

    def __init__(
        self,
        bus: EventBus,
        routing_table: Optional[Dict[str, str]] = None,
    ) -> None:
        self.bus = bus
        self.routing_table: Dict[str, str] = (
            dict(routing_table) if routing_table is not None
            else dict(DEFAULT_ROUTING_TABLE)
        )

    def route(self, msg_name: str, msg: object) -> None:
        """Emit events for a decoded message.

        Always emits ``msg:{msg_name}`` with the raw message object.
        If *msg_name* is in the routing table, also emits the mapped
        game event — potentially with a transformed payload (see
        :meth:`_transform`).
        """
        # 1. Raw message event — consumers who care about the specific
        #    protobuf type can subscribe to "msg:RallyNtf" etc.
        self.bus.emit_message(msg_name, msg)

        # 2. Semantic game event (if mapped).
        event_name = self.routing_table.get(msg_name)
        if event_name is not None:
            payload = self._transform(msg_name, event_name, msg)
            self.bus.emit(event_name, payload)

    # -- payload transforms ------------------------------------------ #

    @staticmethod
    def _transform(msg_name: str, event_name: str, msg: object) -> object:
        """Optionally transform *msg* before emitting the game event.

        Override or extend this method to extract high-level fields from
        raw protobuf objects.  The default implementation applies a small
        set of built-in transforms; everything else passes through as-is.

        Returns
        -------
        object
            The (possibly transformed) payload to emit.
        """
        # ChatOneMsgNtf: try to pull out the chat text for convenience.
        if msg_name == "ChatOneMsgNtf":
            return _extract_chat_payload(msg)

        # Default: pass the raw message through unchanged.
        return msg

    def __repr__(self) -> str:
        return (
            f"MessageRouter({len(self.routing_table)} routes, "
            f"bus={self.bus!r})"
        )


# ------------------------------------------------------------------ #
#  Transform helpers
# ------------------------------------------------------------------ #

def _extract_chat_payload(msg: object) -> dict:
    """Extract structured chat fields from a ChatOneMsgNtf.

    Navigates the nested dataclass chain:
    ``ChatOneMsgNtf.msg.payload.msgVal`` for text,
    ``ChatOneMsgNtf.msg.playerInfo.head.name`` for sender,
    ``ChatOneMsgNtf.channelType`` for channel.

    Returns a flat dict suitable for storage and API serialization.
    """
    result: Dict[str, Any] = {"raw": msg}

    # ChatOneMsgNtf.msg → ChatOneMsg
    chat_msg = getattr(msg, "msg", None)
    if chat_msg is None:
        return result

    # Channel type from ChatOneMsgNtf
    channel_type = getattr(msg, "channelType", 0)
    result["channel_type"] = channel_type
    try:
        from .messages import ChatChannelType
        result["channel"] = ChatChannelType(channel_type).name
    except (ValueError, ImportError):
        result["channel"] = str(channel_type)

    # Timestamp (epoch milliseconds)
    result["timestamp"] = getattr(chat_msg, "timeStamp", 0)
    result["source_type"] = getattr(chat_msg, "sourceType", 0)

    # Sender info: ChatOneMsg.playerInfo → PlayerHeadInfo → UnifyPlayerHead
    player_info = getattr(chat_msg, "playerInfo", None)
    if player_info is not None:
        head = getattr(player_info, "head", None)
        result["sender"] = head.name if head else ""
        result["sender_id"] = getattr(player_info, "ID", 0)
        result["union_name"] = getattr(player_info, "unionName", "")
    else:
        result["sender"] = ""
        result["sender_id"] = 0
        result["union_name"] = ""

    # Message content: ChatOneMsg.payload → ChatPayload.msgVal
    payload = getattr(chat_msg, "payload", None)
    if payload is not None:
        result["content"] = getattr(payload, "msgVal", "")
        result["payload_type"] = getattr(payload, "payloadTypeEnum", 0)
    else:
        result["content"] = ""
        result["payload_type"] = 0

    return result
