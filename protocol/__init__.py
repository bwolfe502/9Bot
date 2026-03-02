"""Kingdom Guard protocol package — decode, route, and inspect game messages.

Submodules
----------
registry
    BKDR hash computation and message-ID <-> name mapping.
decoder
    Frame decoding, raw protobuf parsing, and message stream handling.
messages
    Auto-generated dataclasses and enums for known protobuf types.
game_state
    Reactive game-state store built from decoded protocol messages.
events
    Lightweight event bus and message router for dispatching decoded
    protocol messages to registered handlers.

Quick start::

    from protocol import get_registry, EventBus, MessageRouter, EVT_RALLY_CREATED

    bus = EventBus()
    bus.on(EVT_RALLY_CREATED, lambda msg: print("Rally started:", msg))
    router = MessageRouter(bus)

    reg = get_registry()
    name = reg.name(some_msg_id)      # e.g. "RallyNtf"
    router.route(name, decoded_msg)   # fires EVT_RALLY_CREATED
"""

from __future__ import annotations

__version__ = "0.1.0"

# ------------------------------------------------------------------ #
#  Registry (always available)
# ------------------------------------------------------------------ #

from .registry import Registry, bkdr_hash, msg_id, wire_id, get_registry, get_wire_registry  # noqa: E402

# ------------------------------------------------------------------ #
#  Events (always available)
# ------------------------------------------------------------------ #

from .events import (  # noqa: E402
    EventBus,
    MessageRouter,
    EVT_CONNECTED,
    EVT_DISCONNECTED,
    EVT_ERROR,
    EVT_RALLY_CREATED,
    EVT_RALLY_ENDED,
    EVT_QUEST_CHANGED,
    EVT_AP_CHANGED,
    EVT_RESOURCES_CHANGED,
    EVT_CHAT_MESSAGE,
    EVT_ATTACK_INCOMING,
    EVT_ENTITY_SPAWNED,
    EVT_BATTLE_RESULT,
    EVT_CITY_BURNING,
    EVT_BUFF_CHANGED,
    EVT_TROOPS_CHANGED,
    EVT_BROADCAST,
)

# ------------------------------------------------------------------ #
#  Decoder (available once decoder.py is created)
# ------------------------------------------------------------------ #

try:
    from .decoder import (  # noqa: E402
        decode_frame,
        decode_protobuf_raw,
        ProtobufDecoder,
        MessageStream,
    )
except ImportError:  # decoder.py not yet written
    decode_frame = None  # type: ignore[assignment,misc]
    decode_protobuf_raw = None  # type: ignore[assignment,misc]
    ProtobufDecoder = None  # type: ignore[assignment,misc]
    MessageStream = None  # type: ignore[assignment,misc]

# ------------------------------------------------------------------ #
#  Messages (available once messages.py is created)
# ------------------------------------------------------------------ #

try:
    from .messages import *  # noqa: E402, F401, F403
except ImportError:  # messages.py not yet written
    pass

# ------------------------------------------------------------------ #
#  Game State (available once game_state.py is created)
# ------------------------------------------------------------------ #

try:
    from .game_state import GameState, GameStateRegistry, get_game_state  # noqa: E402
except ImportError:
    GameState = None  # type: ignore[assignment,misc]
    GameStateRegistry = None  # type: ignore[assignment,misc]
    get_game_state = None  # type: ignore[assignment,misc]

# ------------------------------------------------------------------ #
#  Public API
# ------------------------------------------------------------------ #

__all__ = [
    # metadata
    "__version__",
    # registry
    "Registry",
    "bkdr_hash",
    "msg_id",
    "get_registry",
    "wire_id",
    "get_wire_registry",
    # decoder
    "decode_frame",
    "decode_protobuf_raw",
    "ProtobufDecoder",
    "MessageStream",
    # events — classes
    "EventBus",
    "MessageRouter",
    # events — protocol lifecycle
    "EVT_CONNECTED",
    "EVT_DISCONNECTED",
    "EVT_ERROR",
    # events — game events
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
    # game state
    "GameState",
    "GameStateRegistry",
    "get_game_state",
]
