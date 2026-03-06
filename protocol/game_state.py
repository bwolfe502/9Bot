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
import json
import logging
import os
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

from .events import (
    EVT_ALLY_CITY_SPOTTED,
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
    LandInfo,
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
_TERRITORY_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
_AP_ASSET_ID = 11171002  # AssetNtf recover ID for Action Points

# Categories for freshness tracking.
CATEGORIES = (
    "ap", "rallies", "quests", "resources", "entities",
    "attacks", "chat", "buffs", "heartbeat", "lineups", "territory",
)

# ------------------------------------------------------------------ #
#  Shared player name cache (persisted to disk)
# ------------------------------------------------------------------ #

# In-memory power cache — populated from UnionNtf at login, persisted to disk.
_PLAYER_POWERS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "player_powers.json")
_player_powers: Dict[str, int] = {}  # playerID (str) → power (might)
_player_powers_lock = threading.Lock()
_player_powers_dirty = False
_player_powers_loaded = False


def _load_player_powers() -> None:
    """Load cached player powers from disk (called once)."""
    global _player_powers, _player_powers_loaded
    _player_powers_loaded = True
    try:
        with open(_PLAYER_POWERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _player_powers = {k: int(v) for k, v in data.items()}
            log.debug("Loaded %d cached player powers", len(_player_powers))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass


def _save_player_powers() -> None:
    """Persist player powers to disk."""
    global _player_powers_dirty
    _player_powers_dirty = False
    try:
        os.makedirs(os.path.dirname(_PLAYER_POWERS_FILE), exist_ok=True)
        with open(_PLAYER_POWERS_FILE, "w", encoding="utf-8") as f:
            json.dump(_player_powers, f)
    except OSError:
        log.debug("Failed to save player powers cache", exc_info=True)


def cache_player_power(player_id: Any, power: int) -> None:
    """Cache a player ID → power mapping. Thread-safe, persisted to disk."""
    global _player_powers_dirty
    if not player_id or not power:
        return
    with _player_powers_lock:
        if not _player_powers_loaded:
            _load_player_powers()
        _player_powers[str(player_id)] = int(power)
        _player_powers_dirty = True


def lookup_player_power(player_id: Any) -> int:
    """Look up a cached player power by ID. Returns 0 if unknown."""
    if not player_id:
        return 0
    with _player_powers_lock:
        if not _player_powers_loaded:
            _load_player_powers()
        return _player_powers.get(str(player_id), 0)


def save_player_powers_if_dirty() -> None:
    """Save the power cache to disk if there are unsaved changes."""
    with _player_powers_lock:
        if _player_powers_dirty:
            _save_player_powers()

_PLAYER_NAMES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "player_names.json")
_player_names_lock = threading.Lock()
_player_names: Dict[str, str] = {}   # playerID (str) → display name
_player_names_dirty = False
_player_names_loaded = False


def _load_player_names() -> None:
    """Load cached player names from disk (called once)."""
    global _player_names, _player_names_loaded
    _player_names_loaded = True
    try:
        with open(_PLAYER_NAMES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _player_names = data
            log.debug("Loaded %d cached player names", len(_player_names))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass


def _save_player_names() -> None:
    """Persist player names to disk."""
    global _player_names_dirty
    _player_names_dirty = False
    try:
        os.makedirs(os.path.dirname(_PLAYER_NAMES_FILE), exist_ok=True)
        with open(_PLAYER_NAMES_FILE, "w", encoding="utf-8") as f:
            json.dump(_player_names, f, ensure_ascii=False)
    except OSError:
        log.debug("Failed to save player names cache", exc_info=True)


def cache_player_name(player_id: Any, name: str) -> None:
    """Cache a player ID → name mapping. Thread-safe, shared across devices."""
    global _player_names_dirty
    if not player_id or not name or name.startswith("Player#"):
        return
    key = str(player_id)
    with _player_names_lock:
        if not _player_names_loaded:
            _load_player_names()
        if _player_names.get(key) == name:
            return
        _player_names[key] = name
        _player_names_dirty = True


def lookup_player_name(player_id: Any) -> str:
    """Look up a cached player name by ID. Returns empty string if unknown."""
    if not player_id:
        return ""
    with _player_names_lock:
        if not _player_names_loaded:
            _load_player_names()
        return _player_names.get(str(player_id), "")


def save_player_names_if_dirty() -> None:
    """Save the cache to disk if there are unsaved changes."""
    with _player_names_lock:
        if _player_names_dirty:
            _save_player_names()


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
        self._chat_seen_ids: set = set()  # historyId dedup for ChatPullMsgAck
        self._pending_sends: Dict[str, dict] = {}  # clientUuid → parsed req
        self._city_burning_flag: bool = False
        self._buffs: List[dict] = []
        self._battle_results: Deque[Any] = collections.deque(maxlen=_BATTLE_MAXLEN)
        self._server_ts: Optional[int] = None
        self._lineups: Dict[int, Lineup] = {}                 # lineup.id -> Lineup
        self._lineup_states: Dict[int, NewLineupStateInfo] = {}  # lineupID -> state
        self._union_entities: Dict[Any, dict] = {}           # entity_id -> raw dict (ally PLAYER_CITY only)
        self._own_union_id: int = 0                          # populated from UnionNtf
        self._territory_grid: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}  # (row, col) -> (faction_id, cur_faction_id, legion_id, cur_legion_id)
        self._kvk_tower_troops: Dict[Tuple[int, int], int] = {}  # (row, col) -> troop count from KvkBuilding entity
        self._ally_monitoring: bool = False                  # set True only while auto_reinforce_ally runs

        # -- territory cache ---------------------------------------- #
        import hashlib
        _dhash = hashlib.sha256(device_id.encode()).hexdigest()[:8]
        self._territory_cache_file = os.path.join(
            _TERRITORY_CACHE_DIR, f"territory_grid_{_dhash}.json"
        )

        # -- connection metadata ----------------------------------- #
        self.protocol_connected: bool = False

        # -- freshness --------------------------------------------- #
        # Must be initialized before _load_territory_cache (which calls _touch).
        self._last_update: Dict[str, float] = {}

        self._load_territory_cache()

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

    @property
    def ally_city_entities(self) -> List[dict]:
        """Verified ally PLAYER_CITY entities (own unionID confirmed, from UnionEntitiesNtf)."""
        with self._lock:
            return list(self._union_entities.values())

    @property
    def territory_grid(self) -> Dict[Tuple[int, int], Tuple[int, int, int]]:
        """Territory grid: (row, col) -> (faction_id, cur_faction_id, legion_id). Empty dict if no snapshot."""
        with self._lock:
            return dict(self._territory_grid)

    @property
    def kvk_tower_troops(self) -> Dict[Tuple[int, int], int]:
        """KvkBuilding troop counts observed from entity packets: (row,col) -> troop count.

        Updated whenever a TOWER entity (MapUnitType=11) enters the player's viewport.
        Empty list = 0.  Only covers towers seen since last login.
        """
        with self._lock:
            return dict(self._kvk_tower_troops)

    def set_ally_monitoring(self, enabled: bool) -> None:
        """Enable or disable ally city tracking.  Called by run_auto_reinforce_ally."""
        with self._lock:
            self._ally_monitoring = enabled
            if not enabled:
                self._union_entities.clear()

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
        self._sub("msg:GetPlayerHeadInfoAck", self._on_player_heads)
        self._sub("msg:UnionNtf", self._on_union_members)
        self._sub("msg:ChatSendMsgReq", self._on_chat_send_req)
        self._sub("msg:ChatSendMsgNtf", self._on_chat_send_ntf)
        self._sub("msg:UnionEntitiesNtf", self._on_union_entities)
        self._sub("msg:UnionDelEntitiesNtf", self._on_del_union_entities)
        self._sub("msg:KvkTerritoryInfoAck", self._on_territory_info)
        self._sub("msg:KvkTerritoryInfoNtf", self._on_territory_ntf)

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
            # Track history_id for dedup against later ChatPullMsgAck.
            if isinstance(msg, dict):
                hid = msg.get("history_id", "")
                if hid:
                    self._chat_seen_ids.add(hid)
                # Cache sender name from live messages (they have playerInfo).
                sender = msg.get("sender", "")
                sender_id = msg.get("sender_id") or msg.get("from_id", "")
                cache_player_name(sender_id, sender)
            self._chat.append(msg)
            self._touch("chat")
        # Request translation outside the lock (fire-and-forget).
        if isinstance(msg, dict):
            try:
                import chat_translate
                chat_translate.request_translation(msg)
            except ImportError:
                pass

    def _on_chat_history(self, msg: Any) -> None:
        """msg:ChatPullMsgAck — historical messages from chat pull.

        Uses :func:`parse_chat_msgval` to extract display-friendly content
        from the JSON-encoded ``msgVal`` field.  Deduplicates via
        ``historyId`` to prevent re-appending when the game opens the
        same chat channel multiple times.

        The server does NOT include ``playerInfo`` in history messages —
        only ``fromId`` (numeric player ID) and ``meta`` JSON (which has
        ``playerID`` but no name).  We resolve sender names through:

        1. ``playerInfo`` (if present — future-proof)
        2. ``meta`` JSON (``name`` / ``playerName`` / ``senderName``)
        3. Player name cache (populated from live ``ChatOneMsgNtf``)
        4. ``fromId`` as final fallback (shows ``"Player#12345"``)
        """
        from .messages import ChatPullMsgAck, ChatChannelType
        from .events import parse_chat_msgval, _extract_sender_from_meta
        if not isinstance(msg, ChatPullMsgAck):
            return
        added = 0
        new_entries = []
        with self._lock:
            for chat_one_msg in (msg.msgList or []):
                # Dedup by historyId.
                hid = getattr(chat_one_msg, "historyId", "")
                if hid and hid in self._chat_seen_ids:
                    continue
                if hid:
                    self._chat_seen_ids.add(hid)

                payload = getattr(chat_one_msg, "payload", None)
                player_info = getattr(chat_one_msg, "playerInfo", None)
                head = getattr(player_info, "head", None) if player_info else None

                channel_type = getattr(msg, "channelType", 0)
                try:
                    channel_name = ChatChannelType(channel_type).name
                except ValueError:
                    channel_name = str(channel_type)

                # Parse msgVal JSON for real content + payload type.
                raw_msgval = payload.msgVal if payload else ""
                parsed = parse_chat_msgval(raw_msgval)

                # -- Sender resolution chain --
                sender = head.name if head else ""
                sender_id = getattr(player_info, "ID", 0) if player_info else 0
                union_name = getattr(player_info, "unionName", "") if player_info else ""
                from_id = getattr(chat_one_msg, "fromId", "")

                # 1) Try meta JSON (has chatServerPlayer with playerName).
                if not sender and payload:
                    meta = getattr(payload, "meta", "")
                    meta_sender = _extract_sender_from_meta(meta)
                    sender = meta_sender["sender"]
                    sender_id = sender_id or meta_sender["sender_id"]
                    union_name = union_name or meta_sender["union_name"]

                # 2) Try persistent player name cache.
                if not sender and from_id:
                    sender = lookup_player_name(from_id)

                # 3) Final fallback: show numeric ID.
                if not sender and from_id:
                    sender = f"Player#{from_id}"

                # Resolve sender_id from fromId if still missing.
                if not sender_id and from_id:
                    try:
                        sender_id = int(from_id)
                    except (ValueError, TypeError):
                        pass

                # Cache any resolved name for future lookups.
                if sender and not sender.startswith("Player#"):
                    cache_player_name(from_id or sender_id, sender)

                entry = {
                    "content": parsed["content"],
                    "sender": sender,
                    "channel": channel_name,
                    "channel_type": channel_type,
                    "timestamp": getattr(chat_one_msg, "timeStamp", 0),
                    "payload_type": parsed["payload_type"],
                    "source_language": parsed.get("source_language", ""),
                    "sender_id": sender_id,
                    "union_name": union_name,
                    "history_id": hid,
                    "raw": chat_one_msg,
                }
                self._chat.append(entry)
                new_entries.append(entry)
                added += 1
            if added:
                self._touch("chat")
        # Persist any newly learned names outside the lock.
        save_player_names_if_dirty()
        # Request batch translation for new history messages (outside lock).
        if new_entries:
            try:
                import chat_translate
                chat_translate.request_batch_translation(new_entries)
            except ImportError:
                pass

    def _on_player_heads(self, msg: Any) -> None:
        """msg:GetPlayerHeadInfoAck — cache player names from head lookups.

        The game client sends GetPlayerHeadInfoReq when opening chat to
        resolve player IDs to display names.  We intercept the response
        and cache every name for use in chat history rendering.

        Also retroactively patches any ``Player#<id>`` senders already
        stored in ``_chat`` — the head info response typically arrives
        *after* the ChatPullMsgAck that contains the messages.
        """
        from .messages import GetPlayerHeadInfoAck
        if not isinstance(msg, GetPlayerHeadInfoAck):
            return
        if msg.errCode != 0:
            return
        # Build pid→name lookup from the response.
        resolved: Dict[str, str] = {}
        for pid, head_info in msg.heads.items():
            head = getattr(head_info, "head", None)
            name = head.name if head else ""
            if name:
                cache_player_name(pid, name)
                resolved[str(pid)] = name
        if not resolved:
            return
        log.debug("Cached %d player names from GetPlayerHeadInfoAck", len(resolved))
        # Retroactively fix Player#<id> senders in existing chat entries.
        with self._lock:
            for entry in self._chat:
                if not isinstance(entry, dict):
                    continue
                sender = entry.get("sender", "")
                if not sender.startswith("Player#"):
                    continue
                pid_str = sender[7:]  # strip "Player#"
                name = resolved.get(pid_str)
                if name:
                    entry["sender"] = name
        save_player_names_if_dirty()
        save_player_powers_if_dirty()

    def _on_union_members(self, msg: Any) -> None:
        """msg:UnionNtf — cache player names from the alliance member list and own union ID.

        Sent at login with the full member roster.  The game client uses
        this to resolve alliance chat sender names (no GetPlayerHeadInfoReq
        is sent for alliance channels).
        """
        # Capture own union ID (UnionNtf.ID, field 1).
        union_id = msg.get("ID", 0) if isinstance(msg, dict) else getattr(msg, "ID", 0)
        if union_id:
            with self._lock:
                self._own_union_id = int(union_id)
            log.debug("Own union ID set to %d from UnionNtf", union_id)

        members = None
        if isinstance(msg, dict):
            members = msg.get("members", [])
        else:
            members = getattr(msg, "members", None)
        if not members:
            return
        cached = 0
        for member in members:
            if isinstance(member, dict):
                pid = member.get("playerID", 0)
                name = member.get("name", "")
                if not name:
                    head = member.get("head")
                    if isinstance(head, dict):
                        name = head.get("name", "")
                power = member.get("power", 0)
            else:
                pid = getattr(member, "playerID", 0)
                name = getattr(member, "name", "")
                if not name:
                    head = getattr(member, "head", None)
                    if head:
                        name = getattr(head, "name", "")
                power = getattr(member, "power", 0)
            if pid and name:
                cache_player_name(pid, name)
                cached += 1
            if pid and power:
                cache_player_power(pid, power)
                log.info("UnionNtf member: playerID=%s name=%s power=%s", pid, name, power)
        powers_cached = sum(1 for m in (members or [])
                           if (m.get("playerID", 0) if isinstance(m, dict) else getattr(m, "playerID", 0))
                           and (m.get("power", 0) if isinstance(m, dict) else getattr(m, "power", 0)))
        log.info("UnionNtf: cached %d player names, %d powers", cached, powers_cached)
        if cached:
            log.debug("Cached %d player names from UnionNtf", cached)
            # Retroactively fix Player#<id> senders in existing chat entries.
            with self._lock:
                for entry in self._chat:
                    if not isinstance(entry, dict):
                        continue
                    sender = entry.get("sender", "")
                    if sender.startswith("Player#"):
                        resolved_name = lookup_player_name(sender[7:])
                        if resolved_name:
                            entry["sender"] = resolved_name
            save_player_names_if_dirty()

    def _on_chat_send_req(self, msg: Any) -> None:
        """msg:ChatSendMsgReq — capture outgoing chat content by clientUuid."""
        from .messages import ChatSendMsgReq
        from .events import parse_chat_msgval
        if not isinstance(msg, ChatSendMsgReq):
            return
        uuid = msg.clientUuid
        if not uuid:
            return
        payload = msg.payload
        raw_msgval = payload.msgVal if payload else ""
        parsed = parse_chat_msgval(raw_msgval)
        try:
            from .messages import ChatChannelType
            channel_name = ChatChannelType(msg.channelType).name
        except (ValueError, ImportError):
            channel_name = str(msg.channelType)
        with self._lock:
            self._pending_sends[uuid] = {
                "content": parsed["content"],
                "payload_type": parsed["payload_type"],
                "channel": channel_name,
                "channel_type": msg.channelType,
            }

    def _on_chat_send_ntf(self, msg: Any) -> None:
        """msg:ChatSendMsgNtf — merge with pending req to build self-sent entry."""
        if isinstance(msg, dict):
            err = msg.get("errCode", 0)
            uuid = msg.get("clientUuid", "")
            ts = msg.get("timeStamp", 0)
            hid = msg.get("historyId", "")
            ch_type = msg.get("channelType", 0)
        else:
            err = getattr(msg, "errCode", 0)
            uuid = getattr(msg, "clientUuid", "")
            ts = getattr(msg, "timeStamp", 0)
            hid = getattr(msg, "historyId", "")
            ch_type = getattr(msg, "channelType", 0)
        if err != 0 or not uuid:
            return
        with self._lock:
            pending = self._pending_sends.pop(uuid, None)
            if not pending:
                return
            if hid and hid in self._chat_seen_ids:
                return
            if hid:
                self._chat_seen_ids.add(hid)
            entry = {
                "content": pending["content"],
                "sender": "You",
                "channel": pending["channel"],
                "channel_type": pending.get("channel_type", ch_type),
                "timestamp": ts,
                "payload_type": pending["payload_type"],
                "sender_id": 0,
                "union_name": "",
                "history_id": hid,
            }
            self._chat.append(entry)
            self._touch("chat")

    def _on_attack_incoming(self, msg: Any) -> None:
        """EVT_ATTACK_INCOMING — payload is an IntelligencesNtf."""
        if not isinstance(msg, IntelligencesNtf):
            return
        with self._lock:
            self._attacks = list(msg.intelligences)
            self._touch("attacks")

    @staticmethod
    def _entity_id(ent: dict) -> Any:
        """Extract entity ID from a raw EntityInfo dict.

        EntityInfo has sequential=False so wire fields use positional names:
        field_1 = ID (int64).  Fall back to semantic aliases for robustness.
        """
        return ent.get("field_1") or ent.get("id") or ent.get("ID")

    @staticmethod
    def _entity_coords(ent: dict):
        """Extract (X, Z) world coordinates from a raw EntityInfo dict.

        Tries explicit X/Z keys first (set by _on_position after PositionNtf),
        then EntityInfo.field_4 (PositionInfo decoded via decode_unknown):
            PositionInfo.field_1 = Coord → decoded as {"1": x_int, "2": z_int}
        """
        x = ent.get("X", 0)
        z = ent.get("Z", 0)
        if x or z:
            return x, z
        pos = ent.get("field_4")
        if isinstance(pos, dict):
            coord = pos.get("1")
            if isinstance(coord, dict):
                x = coord.get("X") or coord.get("1") or 0
                z = coord.get("Z") or coord.get("2") or 0
        return x, z

    def _is_ally_city(self, ent: dict) -> bool:
        """Return True if *ent* is a PLAYER_CITY (type=2) belonging to own alliance.

        EntityInfo has sequential=False — wire fields use positional names:
          field_2 = type (MapUnitType enum), field_3 = owner (OwnerInfo).
        OwnerInfo has sequential=True so its sub-fields use semantic names.
        Caller must hold _lock.
        """
        # field_2 = MapUnitType; fall back to "type" for robustness.
        etype = ent.get("field_2", ent.get("type", -1))
        if etype != 2:  # MapUnitType.PLAYER_CITY
            return False
        # field_3 = OwnerInfo (decoded with named keys because OwnerInfo is sequential).
        owner = ent.get("field_3") or ent.get("owner")
        if not isinstance(owner, dict):
            return False
        # Accept when own union ID unknown yet (0) — still better than missing it.
        entity_union_id = owner.get("unionID", 0)
        own_uid = self._own_union_id
        if own_uid and entity_union_id and entity_union_id != own_uid:
            return False
        return bool(entity_union_id)  # must have some unionID to be an alliance member

    def _on_entity_spawned(self, msg: Any) -> None:
        """EVT_ENTITY_SPAWNED — payload is an EntitiesNtf (raw dicts).

        Also routes ally PLAYER_CITY entities into _union_entities so that
        allies who teleport nearby (game sends EntitiesNtf, not UnionEntitiesNtf)
        are detected and reinforced.
        """
        from .messages import EntitiesNtf
        if not isinstance(msg, EntitiesNtf):
            return
        new_city_ids = []
        with self._lock:
            for ent in msg.entities:
                eid = self._entity_id(ent) or id(ent)
                self._entities[eid] = ent

                # Extract KvkBuilding.troops for territory towers (MapUnitType.TOWER = 11).
                # EntityInfo.field_5 = PropertyUnion; PropertyUnion.field_27 = KvkBuilding.
                # KvkBuilding.troops (field 5, repeated int64) lists troop IDs at the tower.
                etype = ent.get("field_2", ent.get("type", -1))
                if etype == 11:
                    x, z = self._entity_coords(ent)
                    if x or z:
                        row, col = z // 300000, x // 300000
                        if 0 <= row < 24 and 0 <= col < 24:
                            prop = ent.get("field_5")
                            kvk_bld = prop.get("field_27") if isinstance(prop, dict) else None
                            troops_raw = kvk_bld.get("troops") if isinstance(kvk_bld, dict) else None
                            troop_count = len(troops_raw) if isinstance(troops_raw, list) else 0
                            self._kvk_tower_troops[(row, col)] = troop_count
                            log.debug("KvkBuilding entity (%d,%d) troops=%d", row, col, troop_count)

                if self._ally_monitoring and self._is_ally_city(ent):
                    owner = ent.get("field_3") or ent.get("owner") or {}
                    name = owner.get("name", "?") if isinstance(owner, dict) else "?"
                    is_new = eid not in self._union_entities
                    self._union_entities[eid] = ent
                    # Pre-extract coordinates so runner can use X/Z immediately.
                    if is_new:
                        x, z = self._entity_coords(ent)
                        if x or z:
                            ent["X"] = x
                            ent["Z"] = z
                        log.debug("EntitiesNtf ally city spotted id=%s name=%s x=%s z=%s",
                                  eid, name, ent.get("X", 0), ent.get("Z", 0))
                        new_city_ids.append(eid)
            self._touch("entities")
        for eid in new_city_ids:
            ent = self._union_entities.get(eid)
            if ent:
                self._bus.emit(EVT_ALLY_CITY_SPOTTED, ent)

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
                        # Keep _union_entities in sync — ally cities move on teleport.
                        if pi.ID in self._union_entities:
                            self._union_entities[pi.ID] = ent
                            log.debug("PositionNtf ally city teleport id=%s x=%s z=%s",
                                      pi.ID, ent.get("X"), ent.get("Z"))
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
            self._lineup_states.clear()  # full snapshot → discard stale state entries
            for lu in msg.lineups:
                if lu.id:
                    self._lineups[lu.id] = lu
            self._touch("lineups")

    def _on_lineup_state(self, msg: Any) -> None:
        """msg:NewLineupStateNtf — update lineup states.

        If a lineupID is unknown (e.g. interceptor restarted mid-session and
        missed the initial LineupsNtf), create a stub Lineup so the troop
        shows up in protocol snapshots.
        """
        if not isinstance(msg, NewLineupStateNtf):
            return
        with self._lock:
            for info in msg.lineups:
                lu = self._lineups.get(info.lineupID)
                if lu is not None:
                    lu.state = info.state
                else:
                    # Create stub lineup from state notification — enough for
                    # troop counting (id + state) even without full Lineup data.
                    self._lineups[info.lineupID] = Lineup(
                        id=info.lineupID, state=info.state,
                    )
                # ERR (0) or DEFENDER (1) = troop at home; remove state entry
                # so Lineup.state (which we just updated) is the source of truth.
                if info.state in (0, 1):
                    self._lineup_states.pop(info.lineupID, None)
                else:
                    self._lineup_states[info.lineupID] = info
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
            # Keep data categories fresh — many protocol messages only
            # arrive on state changes, so categories go stale after 30s
            # even though the data is still valid.  Heartbeats come every ~10s.
            if self._lineups:
                self._touch("lineups")
            if "rallies" in self._last_update:
                self._touch("rallies")
            if self._territory_grid:
                self._touch("territory")

    def _on_union_entities(self, msg: Any) -> None:
        """msg:UnionEntitiesNtf — track ally PLAYER_CITY entities.

        UnionEntitiesNtf is the game's dedicated message for alliance member
        entities (server-filtered). We additionally verify owner.unionID matches
        our own union and only store PLAYER_CITY (type=2) entities.
        Emits EVT_ALLY_CITY_SPOTTED for each newly seen city.

        Also bootstraps _own_union_id from the first entity seen here — since
        the server only sends our own alliance members, their unionID is ours.
        """
        entities = getattr(msg, "entities", None)
        if entities is None and isinstance(msg, dict):
            entities = msg.get("entities", [])
        if not entities:
            return

        with self._lock:
            if not self._ally_monitoring:
                return

        new_city_ids = []
        with self._lock:
            for ent in entities:
                ent_dict = ent if isinstance(ent, dict) else vars(ent)
                eid = self._entity_id(ent_dict) or id(ent_dict)
                is_ally = self._is_ally_city(ent_dict)
                if not is_ally:
                    continue
                # Bootstrap own_union_id from the first confirmed ally entity.
                if not self._own_union_id:
                    owner = ent_dict.get("field_3") or ent_dict.get("owner") or {}
                    uid = owner.get("unionID", 0) if isinstance(owner, dict) else 0
                    if uid:
                        self._own_union_id = int(uid)
                        log.info("Bootstrapped own_union_id=%d from UnionEntitiesNtf", uid)
                is_new = eid not in self._union_entities
                self._union_entities[eid] = ent_dict
                if is_new:
                    x, z = self._entity_coords(ent_dict)
                    if x or z:
                        ent_dict["X"] = x
                        ent_dict["Z"] = z
                    owner = ent_dict.get("field_3") or ent_dict.get("owner") or {}
                    name = owner.get("name", "?") if isinstance(owner, dict) else "?"
                    pid = owner.get("ID", 0) if isinstance(owner, dict) else 0
                    power = lookup_player_power(pid)
                    ent_dict["_power"] = power  # pre-computed for priority queue
                    log.info("UnionEntitiesNtf ally city spotted id=%s name=%s power=%s x=%s z=%s",
                             eid, name, power, ent_dict.get("X", 0), ent_dict.get("Z", 0))
                    new_city_ids.append(eid)
            self._touch("entities")

        # Emit outside lock.
        for eid in new_city_ids:
            ent = self._union_entities.get(eid)
            if ent:
                self._bus.emit(EVT_ALLY_CITY_SPOTTED, ent)

    def _load_territory_cache(self) -> None:
        """Load territory grid from disk cache, if available."""
        try:
            with open(self._territory_cache_file) as f:
                data = json.load(f)
            grid: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}
            for key, val in data.items():
                row, col = map(int, key.split(","))
                # Support both old 3-element caches and new 4-element format.
                grid[(row, col)] = (int(val[0]), int(val[1]), int(val[2]),
                                    int(val[3]) if len(val) > 3 else 0)
            with self._lock:
                self._territory_grid = grid
                self._touch("territory")
            log.info("Territory cache loaded: %d squares from %s",
                     len(grid), self._territory_cache_file)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("Failed to load territory cache: %s", e)

    def _save_territory_cache(self) -> None:
        """Save territory grid to disk for use across restarts."""
        try:
            os.makedirs(_TERRITORY_CACHE_DIR, exist_ok=True)
            with self._lock:
                grid = dict(self._territory_grid)
            data = {f"{r},{c}": list(v) for (r, c), v in grid.items()}
            with open(self._territory_cache_file, "w") as f:
                json.dump(data, f)
            log.info("Territory cache saved: %d squares", len(data))
        except Exception as e:
            log.warning("Failed to save territory cache: %s", e)

    def _on_territory_info(self, msg: Any) -> None:
        """msg:KvkTerritoryInfoAck — full territory grid snapshot."""
        lands = msg.get("lands", []) if isinstance(msg, dict) else getattr(msg, "lands", [])
        if not lands:
            log.info("KvkTerritoryInfoAck: no lands")
            return
        new_grid: Dict[Tuple[int, int], Tuple[int, int, int, int]] = {}
        for raw_land in lands:
            land = LandInfo.from_dict(raw_land) if isinstance(raw_land, dict) else raw_land
            if land.coord is None:
                continue
            row = land.coord.Z // 300000
            col = land.coord.X // 300000
            if 0 <= row < 24 and 0 <= col < 24:
                new_grid[(row, col)] = (land.FactionId, land.curFactionId, land.legionId, land.curLegionId)
        with self._lock:
            self._territory_grid = new_grid
            self._touch("territory")
        log.info("KvkTerritoryInfoAck: stored %d territory squares", len(new_grid))
        self._save_territory_cache()

    def _on_territory_ntf(self, msg: Any) -> None:
        """msg:KvkTerritoryInfoNtf — single tower state change.

        Always updates the grid (whether loaded from cache, Ack, or empty).
        Saves cache periodically so incremental changes persist.
        """
        raw_land = msg.get("land") if isinstance(msg, dict) else getattr(msg, "land", None)
        if raw_land is None:
            return
        land = LandInfo.from_dict(raw_land) if isinstance(raw_land, dict) else raw_land
        if land.coord is None:
            return
        row = land.coord.Z // 300000
        col = land.coord.X // 300000
        if 0 <= row < 24 and 0 <= col < 24:
            with self._lock:
                self._territory_grid[(row, col)] = (land.FactionId, land.curFactionId, land.legionId, land.curLegionId)
                self._touch("territory")
            log.debug("KvkTerritoryInfoNtf: (%d,%d) → faction=%d cur=%d legion=%d curlegion=%d",
                      row, col, land.FactionId, land.curFactionId, land.legionId, land.curLegionId)

    def _on_del_union_entities(self, msg: Any) -> None:
        """msg:UnionDelEntitiesNtf — remove ally entities by ID."""
        ids = getattr(msg, "ids", None)
        if ids is None and isinstance(msg, dict):
            ids = msg.get("ids", [])
        if not ids:
            return
        with self._lock:
            for eid in ids:
                self._union_entities.pop(eid, None)
            self._touch("entities")

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
