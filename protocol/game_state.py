"""Per-device game state store fed by protocol EventBus.

Subscribes to semantic EVT_* events and raw msg:* events, maintaining a
thread-safe in-memory snapshot of the game world.  Bot workers read state
via property accessors instead of (or in addition to) OCR/vision.

Usage::

    from protocol.events import EventBus
    from protocol.game_state import GameStateRegistry, get_game_state

    registry = GameStateRegistry()
    state = registry.get_or_create("127.0.0.1:5555", bus)
    print(state.ap)           # (120, 200) or None
    print(state.city_burning)  # False
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

from .events import (
    EVT_AP_CHANGED,
    EVT_ATTACK_INCOMING,
    EVT_BATTLE_RESULT,
    EVT_BUFF_CHANGED,
    EVT_CHAT_MESSAGE,
    EVT_CITY_BURNING,
    EVT_CONNECTED,
    EVT_DISCONNECTED,
    EVT_ENTITY_SPAWNED,
    EVT_QUEST_CHANGED,
    EVT_RALLY_CREATED,
    EVT_RALLY_ENDED,
    EVT_RESOURCES_CHANGED,
    EventBus,
)
from .messages import (
    Asset,
    AssetNtf,
    BattleResultNtf,
    BuffNtf,
    CombustionStateNtf,
    DelEntitiesNtf,
    HeartBeatAck,
    Intelligence,
    IntelligencesNtf,
    Lineup,
    LineupsNtf,
    NewLineupStateInfo,
    NewLineupStateNtf,
    PosInfo,
    PositionNtf,
    PowerNtf,
    Quest,
    QuestChangeNtf,
    QuestsNtf,
    Rally,
    RallyDelNtf,
    RallyNtf,
)

__all__ = [
    "GameState",
    "GameStateRegistry",
    "get_game_state",
]

log = logging.getLogger(__name__)

_CHAT_MAXLEN = 200
_BATTLE_MAXLEN = 50
_AP_ASSET_ID = 11171002  # AssetNtf recover ID for Action Points

# Categories for freshness tracking.
CATEGORIES = (
    "ap", "rallies", "quests", "resources", "entities",
    "attacks", "chat", "buffs", "heartbeat", "lineups",
)


# ------------------------------------------------------------------ #
#  GameState
# ------------------------------------------------------------------ #

class GameState:
    """Thread-safe per-device state store populated by EventBus handlers."""

    def __init__(self, device_id: str, bus: EventBus) -> None:
        self._device_id = device_id
        self._bus = bus
        self._lock = threading.RLock()

        # -- state buckets ----------------------------------------- #
        self._powers: Dict[int, Tuple[int, int]] = {}      # cfgID -> (cur, max)
        self._rallies: Dict[int, Rally] = {}                # rallyTroopID -> Rally
        self._quests: Dict[int, dict] = {}                  # cfgID -> info dict
        self._resources: Dict[int, Asset] = {}              # Asset.ID -> Asset
        self._entities: Dict[Any, dict] = {}                # entity id -> raw dict
        self._attacks: List[Intelligence] = []
        self._chat: Deque[Any] = collections.deque(maxlen=_CHAT_MAXLEN)
        self._city_burning_flag: bool = False
        self._buffs: List[dict] = []
        self._battle_results: Deque[Any] = collections.deque(maxlen=_BATTLE_MAXLEN)
        self._server_ts: Optional[int] = None
        self._lineups: Dict[int, Lineup] = {}                 # lineup.id -> Lineup
        self._lineup_states: Dict[int, NewLineupStateInfo] = {}  # lineupID -> state

        # -- connection metadata ----------------------------------- #
        self.protocol_connected: bool = False

        # -- freshness --------------------------------------------- #
        self._last_update: Dict[str, float] = {}

        # -- handler refs (for unsubscribe) ------------------------ #
        self._handlers: List[Tuple[str, Any]] = []
        self._register_handlers()

    # ============================================================== #
    #  Public read API (all return copies or immutables)
    # ============================================================== #

    @property
    def ap(self) -> Optional[Tuple[int, int]]:
        """(current, max) AP or None if never received.

        Checks PowerNtf (cfgID=1) first, then falls back to AssetNtf
        (recover asset ID 11171002) which carries AP with grow metadata.
        """
        with self._lock:
            # Prefer PowerNtf data if available.
            power = self._powers.get(1)
            if power is not None:
                return power
            # Fall back to AssetNtf recover data.
            asset = self._resources.get(_AP_ASSET_ID)
            if asset is not None:
                grow = asset.grow or {}
                max_ap = grow.get("growMax", 0)
                return (asset.val, max_ap) if max_ap > 0 else None
            return None

    @property
    def powers(self) -> Dict[int, Tuple[int, int]]:
        """All power cfgIDs -> (current, max)."""
        with self._lock:
            return dict(self._powers)

    @property
    def rallies(self) -> Dict[int, Rally]:
        """Active rallies keyed by rallyTroopID."""
        with self._lock:
            return dict(self._rallies)

    @property
    def quests(self) -> Dict[int, dict]:
        """Quest state keyed by cfgID."""
        with self._lock:
            return {k: dict(v) for k, v in self._quests.items()}

    @property
    def resources(self) -> Dict[int, Asset]:
        """Resources keyed by Asset.ID."""
        with self._lock:
            return dict(self._resources)

    @property
    def entities(self) -> Dict[Any, dict]:
        """Entity cache (raw dicts)."""
        with self._lock:
            return dict(self._entities)

    @property
    def incoming_attacks(self) -> List[Intelligence]:
        """Current incoming attack intelligence list."""
        with self._lock:
            return list(self._attacks)

    @property
    def chat_messages(self) -> List[Any]:
        """Recent chat messages (newest last)."""
        with self._lock:
            return list(self._chat)

    @property
    def city_burning(self) -> bool:
        with self._lock:
            return self._city_burning_flag

    @property
    def buffs(self) -> List[dict]:
        with self._lock:
            return list(self._buffs)

    @property
    def battle_results(self) -> List[Any]:
        with self._lock:
            return list(self._battle_results)

    @property
    def server_time(self) -> Optional[int]:
        """Last HeartBeatAck serverTS, or None."""
        with self._lock:
            return self._server_ts

    @property
    def lineups(self) -> Dict[int, Lineup]:
        """Troop lineups keyed by lineup ID."""
        with self._lock:
            return dict(self._lineups)

    @property
    def lineup_states(self) -> Dict[int, NewLineupStateInfo]:
        """Latest lineup state info keyed by lineupID."""
        with self._lock:
            return dict(self._lineup_states)

    def is_fresh(self, category: str, max_age_s: float = 30.0) -> bool:
        """True if *category* was updated within *max_age_s* seconds."""
        with self._lock:
            ts = self._last_update.get(category)
            if ts is None:
                return False
            return (time.monotonic() - ts) <= max_age_s

    def last_update(self, category: str) -> Optional[float]:
        """Monotonic timestamp of last update for *category*, or None."""
        with self._lock:
            return self._last_update.get(category)

    # ============================================================== #
    #  Shutdown
    # ============================================================== #

    def shutdown(self) -> None:
        """Unregister all handlers from the EventBus."""
        for event_name, handler in self._handlers:
            self._bus.off(event_name, handler)
        self._handlers.clear()
        log.debug("GameState[%s] shut down", self._device_id)

    # ============================================================== #
    #  Handler registration
    # ============================================================== #

    def _register_handlers(self) -> None:
        """Subscribe to all relevant events on the bus."""
        self._sub(EVT_AP_CHANGED, self._on_ap_changed)
        self._sub(EVT_RALLY_CREATED, self._on_rally_created)
        self._sub(EVT_RALLY_ENDED, self._on_rally_ended)
        self._sub(EVT_QUEST_CHANGED, self._on_quest_changed)
        self._sub(EVT_RESOURCES_CHANGED, self._on_resources_changed)
        self._sub(EVT_CHAT_MESSAGE, self._on_chat_message)
        self._sub(EVT_ATTACK_INCOMING, self._on_attack_incoming)
        self._sub(EVT_ENTITY_SPAWNED, self._on_entity_spawned)
        self._sub(EVT_BATTLE_RESULT, self._on_battle_result)
        self._sub(EVT_CITY_BURNING, self._on_city_burning)
        self._sub(EVT_BUFF_CHANGED, self._on_buff_changed)
        self._sub(EVT_CONNECTED, self._on_connected)
        self._sub(EVT_DISCONNECTED, self._on_disconnected)

        # Raw message events without semantic routing.
        self._sub("msg:HeartBeatAck", self._on_heartbeat)
        self._sub("msg:QuestsNtf", self._on_quests_bulk)
        self._sub("msg:DelEntitiesNtf", self._on_del_entities)
        self._sub("msg:PositionNtf", self._on_position)
        self._sub("msg:LineupsNtf", self._on_lineups)
        self._sub("msg:NewLineupStateNtf", self._on_lineup_state)
        self._sub("msg:ChatPullMsgAck", self._on_chat_history)

    def _sub(self, event_name: str, handler: Any) -> None:
        """Subscribe and track for later unsubscribe."""
        self._bus.on(event_name, handler)
        self._handlers.append((event_name, handler))

    def _touch(self, category: str) -> None:
        """Update freshness timestamp (caller must hold _lock)."""
        self._last_update[category] = time.monotonic()

    # ============================================================== #
    #  Event handlers (private)
    # ============================================================== #

    def _on_ap_changed(self, msg: Any) -> None:
        """EVT_AP_CHANGED — payload is a PowerNtf."""
        if not isinstance(msg, PowerNtf):
            return
        with self._lock:
            # Build max lookup from maxPowers.
            max_map: Dict[int, int] = {}
            for p in msg.maxPowers:
                max_map[p.cfgID] = p.val
            # Merge current powers with max.
            for p in msg.powers:
                cur_max = self._powers.get(p.cfgID, (0, 0))[1]
                new_max = max_map.get(p.cfgID, cur_max)
                self._powers[p.cfgID] = (p.val, new_max)
            # Update maxPowers that arrived without a current counterpart.
            for p in msg.maxPowers:
                if p.cfgID not in {pw.cfgID for pw in msg.powers}:
                    cur = self._powers.get(p.cfgID, (0, 0))[0]
                    self._powers[p.cfgID] = (cur, p.val)
            self._touch("ap")

    def _on_rally_created(self, msg: Any) -> None:
        """EVT_RALLY_CREATED — payload is a RallyNtf."""
        if not isinstance(msg, RallyNtf):
            return
        rally = msg.rally
        if rally is None:
            return
        with self._lock:
            self._rallies[rally.rallyTroopID] = rally
            self._touch("rallies")

    def _on_rally_ended(self, msg: Any) -> None:
        """EVT_RALLY_ENDED — payload is a RallyDelNtf."""
        if not isinstance(msg, RallyDelNtf):
            return
        with self._lock:
            self._rallies.pop(msg.rallyTroopID, None)
            self._touch("rallies")

    def _on_quest_changed(self, msg: Any) -> None:
        """EVT_QUEST_CHANGED — payload is a QuestChangeNtf (single quest)."""
        if not isinstance(msg, QuestChangeNtf):
            return
        with self._lock:
            self._quests[msg.cfgID] = {
                "id": msg.id,
                "cfgID": msg.cfgID,
                "quest_type": msg.questType,
                "status": msg.status,
                "state": msg.state,
            }
            self._touch("quests")

    def _on_quests_bulk(self, msg: Any) -> None:
        """msg:QuestsNtf — replaces the full quest list."""
        if not isinstance(msg, QuestsNtf):
            return
        with self._lock:
            self._quests.clear()
            for q in msg.quests:
                self._quests[q.cfgID] = {
                    "cfgID": q.cfgID,
                    "curCnt": q.curCnt,
                    "state": q.state,
                }
            self._touch("quests")

    def _on_resources_changed(self, msg: Any) -> None:
        """EVT_RESOURCES_CHANGED — payload is an AssetNtf."""
        if not isinstance(msg, AssetNtf):
            return
        with self._lock:
            if msg.isInit:
                self._resources.clear()
            for a in msg.assets:
                self._resources[a.ID] = a
                if a.ID == _AP_ASSET_ID:
                    self._touch("ap")
            self._touch("resources")

    def _on_chat_message(self, msg: Any) -> None:
        """EVT_CHAT_MESSAGE — payload is a dict (transformed) or raw object."""
        with self._lock:
            self._chat.append(msg)
            self._touch("chat")

    def _on_chat_history(self, msg: Any) -> None:
        """msg:ChatPullMsgAck — historical messages from chat pull."""
        from .messages import ChatPullMsgAck, ChatChannelType
        if not isinstance(msg, ChatPullMsgAck):
            return
        with self._lock:
            for chat_one_msg in (msg.msgList or []):
                payload = getattr(chat_one_msg, "payload", None)
                player_info = getattr(chat_one_msg, "playerInfo", None)
                head = getattr(player_info, "head", None) if player_info else None
                channel_type = getattr(msg, "channelType", 0)
                try:
                    channel_name = ChatChannelType(channel_type).name
                except ValueError:
                    channel_name = str(channel_type)
                entry = {
                    "content": payload.msgVal if payload else "",
                    "sender": head.name if head else "",
                    "channel": channel_name,
                    "channel_type": channel_type,
                    "timestamp": getattr(chat_one_msg, "timeStamp", 0),
                    "payload_type": payload.payloadTypeEnum if payload else 0,
                    "sender_id": getattr(player_info, "ID", 0) if player_info else 0,
                    "union_name": getattr(player_info, "unionName", "") if player_info else "",
                    "raw": chat_one_msg,
                }
                self._chat.append(entry)
            if msg.msgList:
                self._touch("chat")

    def _on_attack_incoming(self, msg: Any) -> None:
        """EVT_ATTACK_INCOMING — payload is an IntelligencesNtf."""
        if not isinstance(msg, IntelligencesNtf):
            return
        with self._lock:
            self._attacks = list(msg.intelligences)
            self._touch("attacks")

    def _on_entity_spawned(self, msg: Any) -> None:
        """EVT_ENTITY_SPAWNED — payload is an EntitiesNtf (raw dicts)."""
        from .messages import EntitiesNtf
        if not isinstance(msg, EntitiesNtf):
            return
        with self._lock:
            for ent in msg.entities:
                eid = ent.get("id") or ent.get("ID") or id(ent)
                self._entities[eid] = ent
            self._touch("entities")

    def _on_del_entities(self, msg: Any) -> None:
        """msg:DelEntitiesNtf — remove entities by ID."""
        if isinstance(msg, DelEntitiesNtf):
            ids = msg.ids
        elif isinstance(msg, dict):
            ids = msg.get("ids", [])
        else:
            ids = getattr(msg, "ids", None) or []
        if not ids:
            return
        with self._lock:
            for eid in ids:
                self._entities.pop(eid, None)
            self._touch("entities")

    def _on_position(self, msg: Any) -> None:
        """msg:PositionNtf — update entity positions."""
        if isinstance(msg, PositionNtf):
            # Typed: iterate PosInfo objects.
            if not msg.postions:
                return
            with self._lock:
                for pi in msg.postions:
                    if pi.ID and pi.ID in self._entities:
                        ent = self._entities[pi.ID]
                        if pi.coord:
                            ent["X"] = pi.coord.X
                            ent["Z"] = pi.coord.Z
                        if pi.pos_raw:
                            ent["pos_raw"] = pi.pos_raw
                self._touch("entities")
            return
        # Fallback for raw dicts (backward compat).
        positions = getattr(msg, "postions", None) or getattr(msg, "positions", None)
        if positions is None and isinstance(msg, dict):
            positions = msg.get("postions") or msg.get("positions", [])
        if not positions:
            return
        with self._lock:
            for pos in positions:
                if isinstance(pos, dict):
                    eid = pos.get("id") or pos.get("ID")
                    if eid is not None and eid in self._entities:
                        self._entities[eid].update(pos)
            self._touch("entities")

    def _on_lineups(self, msg: Any) -> None:
        """msg:LineupsNtf — full lineup data push (replaces all)."""
        if not isinstance(msg, LineupsNtf):
            return
        with self._lock:
            self._lineups.clear()
            for lu in msg.lineups:
                if lu.id:
                    self._lineups[lu.id] = lu
            self._touch("lineups")

    def _on_lineup_state(self, msg: Any) -> None:
        """msg:NewLineupStateNtf — update lineup states."""
        if not isinstance(msg, NewLineupStateNtf):
            return
        with self._lock:
            for info in msg.lineups:
                self._lineup_states[info.lineupID] = info
                # Also update state on the stored Lineup if we have it.
                lu = self._lineups.get(info.lineupID)
                if lu is not None:
                    lu.state = info.state
            self._touch("lineups")

    def _on_battle_result(self, msg: Any) -> None:
        """EVT_BATTLE_RESULT — payload is a BattleResultNtf."""
        with self._lock:
            self._battle_results.append(msg)
            self._touch("heartbeat")  # no dedicated category — reuse won't hurt

    def _on_city_burning(self, msg: Any) -> None:
        """EVT_CITY_BURNING — payload is a CombustionStateNtf."""
        if not isinstance(msg, CombustionStateNtf):
            return
        with self._lock:
            self._city_burning_flag = msg.isCombustion
            self._touch("buffs")  # grouped under buffs for freshness

    def _on_buff_changed(self, msg: Any) -> None:
        """EVT_BUFF_CHANGED — payload is a BuffNtf."""
        if not isinstance(msg, BuffNtf):
            return
        with self._lock:
            self._buffs = list(msg.buffs)
            self._touch("buffs")

    def _on_heartbeat(self, msg: Any) -> None:
        """msg:HeartBeatAck — track server time."""
        ts = getattr(msg, "serverTS", None)
        if ts is None and isinstance(msg, dict):
            ts = msg.get("serverTS")
        if ts is None:
            return
        with self._lock:
            self._server_ts = ts
            self._touch("heartbeat")

    def _on_connected(self, *args: Any) -> None:
        """EVT_CONNECTED — mark protocol as live."""
        with self._lock:
            self.protocol_connected = True

    def _on_disconnected(self, *args: Any) -> None:
        """EVT_DISCONNECTED — mark protocol as down."""
        with self._lock:
            self.protocol_connected = False

    def __repr__(self) -> str:
        with self._lock:
            cats = sum(1 for c in CATEGORIES if c in self._last_update)
        return f"GameState({self._device_id!r}, {cats}/{len(CATEGORIES)} categories)"


# ------------------------------------------------------------------ #
#  GameStateRegistry
# ------------------------------------------------------------------ #

class GameStateRegistry:
    """Global mapping of device_id -> GameState."""

    def __init__(self) -> None:
        self._states: Dict[str, GameState] = {}
        self._lock = threading.Lock()

    def get(self, device_id: str) -> Optional[GameState]:
        """Return the GameState for *device_id*, or None."""
        with self._lock:
            return self._states.get(device_id)

    def get_or_create(self, device_id: str, event_bus: EventBus) -> GameState:
        """Return existing GameState or create and register a new one."""
        with self._lock:
            state = self._states.get(device_id)
            if state is not None:
                return state
            state = GameState(device_id, event_bus)
            self._states[device_id] = state
            log.info("Created GameState for %s", device_id)
            return state

    def remove(self, device_id: str) -> None:
        """Unsubscribe and remove the GameState for *device_id*."""
        with self._lock:
            state = self._states.pop(device_id, None)
        if state is not None:
            state.shutdown()
            log.info("Removed GameState for %s", device_id)

    def all_devices(self) -> List[str]:
        """Return list of all tracked device IDs."""
        with self._lock:
            return list(self._states.keys())


# ------------------------------------------------------------------ #
#  Module-level singleton registry + convenience accessor
# ------------------------------------------------------------------ #

_registry = GameStateRegistry()


def get_game_state(device_id: str) -> Optional[GameState]:
    """Return the GameState for *device_id* from the global registry."""
    return _registry.get(device_id)
