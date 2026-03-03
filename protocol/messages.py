"""Kingdom Guard protocol message dataclasses.

Typed containers for decoded protobuf messages.  Each dataclass has a
``from_dict`` classmethod that constructs an instance from a decoded
protobuf dict (field_name -> value).  Missing fields fall back to
sensible defaults; extra fields are silently ignored.

Usage::

    >>> from protocol.messages import ChatOneMsgNtf, MESSAGE_CLASSES
    >>> ntf = ChatOneMsgNtf.from_dict(decoded_dict)
    >>> cls = MESSAGE_CLASSES["RallyNtf"]
    >>> obj = cls.from_dict(raw)
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import IntEnum
from typing import Any, Dict, List, Optional, Type

__all__ = [
    # Enums
    "ChatChannelType",
    "ChatPayloadType",
    "ChatSourceType",
    "RallyState",
    "RallyDelType",
    "QuestStateType",
    "QuestState",
    "QuestType",
    "MarchAct",
    "MapUnitType",
    "LineupState",
    # Leaf dataclasses
    "Coord",
    "Power",
    "Quest",
    "Asset",
    "PosInfo",
    "LineupHero",
    "Lineup",
    "NewLineupStateInfo",
    "RallyPowerLimit",
    # Chat dataclasses
    "ChatPayload",
    "UnifyPlayerHead",
    "PlayerHeadInfo",
    "ChatOneMsg",
    # Notification dataclasses
    "ChatOneMsgNtf",
    "PowerNtf",
    "RallyTroopDetail",
    "Rally",
    "RallyNtf",
    "RallyDelNtf",
    "QuestChangeNtf",
    "QuestsNtf",
    "AssetNtf",
    "EntitiesNtf",
    "PositionNtf",
    "DelEntitiesNtf",
    "LineupsNtf",
    "NewLineupStateNtf",
    "NewTroopAck",
    "RedPointNtf",
    "RallyPowerLimitAck",
    "Intelligence",
    "IntelligencesNtf",
    "WoundedSoldierInfoNtf",
    "BuffNtf",
    "CombustionStateNtf",
    "BroadcastGameNtf",
    "BattleResultNtf",
    "HeartBeatReq",
    "HeartBeatAck",
    # Chat request / response
    "ChatSendMsgReq",
    "ChatPullMsgReq",
    "ChatPullMsgAck",
    "GetPlayerHeadInfoAck",
    # Lookup
    "MESSAGE_CLASSES",
]


# ------------------------------------------------------------------ #
#  Enums
# ------------------------------------------------------------------ #

class ChatChannelType(IntEnum):
    ILLEGAL = 0
    SERVER = 1
    UNION = 2
    PRIVATE = 3
    WORLD = 4
    FACTION = 5
    LEGION = 6
    KVK = 7
    LUMINARY = 8
    UNION_R3 = 9
    UNION_R4 = 10
    CUSTOM_GROUP = 11
    SECRETARY = 12
    SVS = 13


class ChatPayloadType(IntEnum):
    ILLEGAL = 0
    TEXT = 1
    IMAGE = 2
    AUDIO = 3
    VIDEO = 4
    USERDEFINED = 5
    MAIL = 6
    CHAT = 7
    ALLIANCE_HELP = 8
    SHOW = 9
    RED_PACKET = 10
    SYSTEM = 11


class ChatSourceType(IntEnum):
    ILLEGAL = 0
    PLAYER = 1
    SYSTEM = 2


class RallyState(IntEnum):
    IDLE = 0
    READY = 1
    WAITING = 2
    MARCHING = 3
    BATTLING = 4


class RallyDelType(IntEnum):
    CANCEL = 0
    TARGET_DISAPPEAR = 1
    TARGET_POS_UPDATED = 2
    RALLY_FAILED = 3
    FINISH = 4
    MARCHING_FAILED = 5
    PVE_FAILED = 6


class QuestStateType(IntEnum):
    NOT_ACTION = 0
    ACTION = 1
    SHOW_ACTION = 2
    NOT_REWARD = 3
    GOT_REWARD = 4
    EXPIRED = 5


class QuestState(IntEnum):
    GO_ON = 0
    FINISH = 1
    REWARD = 2
    OUT_OF_TIME = 3


class QuestType(IntEnum):
    ERR = 0
    DAILY = 1
    MAIN = 2
    BRANCH = 3
    MOBILIZE = 4
    NEW_MAIN = 5
    ACHIEVEMENT = 6
    UNION = 7


class MarchAct(IntEnum):
    STUB = 0
    ATTACK = 1
    GATHER = 2
    RETURN = 3
    SCOUT = 4
    TRANSPORT = 5
    RALLY = 7
    JOIN_RALLY = 8
    REINFORCE = 9
    GUARD = 10
    OCCUPY = 11
    MOVE = 12
    REWARD_BOX = 13
    LEGION_RALLY = 14
    LEGION_JOIN_RALLY = 15
    MINE_GATHER = 16


class MapUnitType(IntEnum):
    STUB = 0
    PLAYER_TROOP = 1
    PLAYER_CITY = 2
    NPC_GATHERABLE = 3
    NPC_TROOP = 4
    NPC_CITY = 5
    RALLY_TROOP = 6
    DECORATION = 7
    RESOURCE_POINT = 8
    THRONE = 9
    PASS = 10
    TOWER = 11
    FORT = 12
    STRONGHOLD = 13
    CAMP = 14
    CRYSTAL = 15
    RELIC = 16
    BARRIER = 17
    MINE = 18
    WONDER = 19
    PORTAL = 20
    SANCTUARY = 21
    CARAVAN = 22
    EXPEDITION = 23
    PHANTOM = 24
    ALTAR = 25
    WORLD_BOSS = 26
    EVIL = 27


class LineupState(IntEnum):
    """Troop lineup states — extracted from game binary via Frida IL2CPP API."""
    ERR = 0               # LineupStateErr — no deployment / idle / home
    DEFENDER = 1           # LineupStateDefender — at home (available to defend)
    OUT_CITY = 2           # LineupStateOutCity — marching to target
    CAMP = 3               # LineupStateCamp — stationing at a camp
    RALLY = 4              # LineupStateRally — waiting in a rally
    REINFORCE = 5          # LineupStateReinforce — reinforcing an ally
    GATHERING = 6          # LineupStateGathering — gathering resources
    TROOP_FIGHT = 7        # LineupStateTroopFight — in solo combat
    RALLY_FIGHT = 8        # LineupStateRallyFight — in rally combat
    RETURN = 9             # LineupStateReturn — marching home
    BUILDING_BUILD = 10    # LineupStateBuildingBuild — building construction
    BUILDING_OCCUPY = 11   # LineupStateBuildingOccupy — occupying a building
    BUILDING_DEFEND = 12   # LineupStateBuildingDefend — defending a building
    MINE_EXPLORE = 13      # LineUpStateMineExplore — bizarre cave / adventure
    PICKUP = 14            # LineUpStatePickup — collecting pickup item
    SCORE_GATHERING = 15   # LineUpStateScoreGathering — event gathering


# ------------------------------------------------------------------ #
#  Helper: generic from_dict for simple (non-nested) dataclasses
# ------------------------------------------------------------------ #

def _simple_from_dict(cls: Type, d: Dict[str, Any]) -> Any:
    """Construct a dataclass from *d*, taking only recognised fields."""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in known})


# ------------------------------------------------------------------ #
#  Leaf / simple dataclasses
# ------------------------------------------------------------------ #

@dataclass
class Coord:
    X: int = 0
    Z: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Coord:
        if not d:
            return cls()
        return cls(
            X=d.get("X", 0),
            Z=d.get("Z", 0),
        )


@dataclass
class Power:
    cfgID: int = 0
    val: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Power:
        if not d:
            return cls()
        return cls(
            cfgID=d.get("cfgID", 0),
            val=d.get("val", 0),
        )


@dataclass
class Quest:
    cfgID: int = 0
    curCnt: int = 0
    state: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Quest:
        if not d:
            return cls()
        return cls(
            cfgID=d.get("cfgID", 0),
            curCnt=d.get("curCnt", 0),
            state=d.get("state", 0),
        )


@dataclass
class Asset:
    typ: str = ""
    ID: int = 0
    val: int = 0
    cap: int = 0
    safe: int = 0
    protect: int = 0
    grow: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Asset:
        if not d:
            return cls()
        return cls(
            typ=d.get("typ", ""),
            ID=d.get("ID", 0),
            val=d.get("val", 0),
            cap=d.get("cap", 0),
            safe=d.get("safe", 0),
            protect=d.get("protect", 0),
            grow=d.get("grow"),
        )


@dataclass
class PosInfo:
    """Entity position update entry (PositionNtf inner type).

    The ``pos`` field is a PositionInfo proto — its inner structure is not
    fully in the field map, so we extract the Coord from field '1'.
    """
    ID: int = 0
    coord: Optional[Coord] = None
    pos_raw: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> PosInfo:
        if not d:
            return cls()
        pos = d.get("pos")
        coord = None
        if isinstance(pos, dict):
            # PositionInfo field 1 is a Coord {X, Z}
            coord_raw = pos.get("1") or pos.get(1)
            if isinstance(coord_raw, dict):
                coord = Coord(
                    X=coord_raw.get("X") or coord_raw.get("1") or coord_raw.get(1, 0),
                    Z=coord_raw.get("Z") or coord_raw.get("2") or coord_raw.get(2, 0),
                )
        return cls(
            ID=d.get("ID", 0),
            coord=coord,
            pos_raw=pos,
        )


@dataclass
class LineupHero:
    """Hero assigned to a lineup slot."""
    heroID: int = 0
    power: int = 0
    index: int = 0
    combatPower: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> LineupHero:
        if not d:
            return cls()
        return cls(
            heroID=d.get("heroID") or d.get("1") or d.get(1, 0),
            power=d.get("power") or d.get("2") or d.get(2, 0),
            index=d.get("index") or d.get("3") or d.get(3, 0),
            combatPower=d.get("combatPower") or d.get("4") or d.get(4, 0),
        )


@dataclass
class Lineup:
    """Troop lineup — decoded from non-sequential proto (field_N keys).

    Proto tag mapping (inferred from wire data):
        field_1=id, field_2=heroes, field_3=power, field_4=powerMax,
        field_5=recoverTime, field_6=state, field_7=combatPower,
        field_8=combatPowerMax, field_9=moveSpeed, field_10=collectSpeed,
        field_11=recoverSpeed, field_12=lineupLoad
    """
    id: int = 0
    heroes: List[LineupHero] = field(default_factory=list)
    power: int = 0
    powerMax: int = 0
    recoverTime: int = 0
    state: int = 0
    combatPower: int = 0
    combatPowerMax: int = 0
    moveSpeed: int = 0
    collectSpeed: int = 0
    recoverSpeed: int = 0
    lineupLoad: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Lineup:
        if not d:
            return cls()
        # Parse heroes — may be a single dict or a list
        heroes_raw = d.get("heroes") or d.get("field_2")
        heroes: List[LineupHero] = []
        if isinstance(heroes_raw, list):
            heroes = [LineupHero.from_dict(h) for h in heroes_raw]
        elif isinstance(heroes_raw, dict):
            heroes = [LineupHero.from_dict(heroes_raw)]
        return cls(
            id=d.get("id") or d.get("field_1", 0),
            heroes=heroes,
            power=d.get("power") or d.get("field_3", 0),
            powerMax=d.get("powerMax") or d.get("field_4", 0),
            recoverTime=d.get("recoverTime") or d.get("field_5", 0),
            state=d.get("state") or d.get("field_6", 0),
            combatPower=d.get("combatPower") or d.get("field_7", 0),
            combatPowerMax=d.get("combatPowerMax") or d.get("field_8", 0),
            moveSpeed=d.get("moveSpeed") or d.get("field_9", 0),
            collectSpeed=d.get("collectSpeed") or d.get("field_10", 0),
            recoverSpeed=d.get("recoverSpeed") or d.get("field_11", 0),
            lineupLoad=d.get("lineupLoad") or d.get("field_12", 0),
        )


@dataclass
class NewLineupStateInfo:
    """Lineup state change entry (NewLineupStateNtf inner type).

    Note: ``trooopID`` is misspelled (triple 'o') in the original proto.
    """
    lineupID: int = 0
    troopID: int = 0
    state: int = 0
    stateEndTs: int = 0
    pos: Optional[Coord] = None

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> NewLineupStateInfo:
        if not d:
            return cls()
        pos_raw = d.get("pos")
        # troopID comes as "trooopID" (triple o) from the proto
        troop_id = d.get("troopID") or d.get("trooopID", 0)
        return cls(
            lineupID=d.get("lineupID", 0),
            troopID=troop_id,
            state=d.get("state", 0),
            stateEndTs=d.get("stateEndTs", 0),
            pos=Coord.from_dict(pos_raw) if pos_raw else None,
        )


@dataclass
class RallyPowerLimit:
    """Rally power limit entry."""
    cfgId: int = 0
    power: int = 0
    check: bool = False

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> RallyPowerLimit:
        if not d:
            return cls()
        return cls(
            cfgId=d.get("cfgId", 0),
            power=d.get("power", 0),
            check=d.get("check", False),
        )


# ------------------------------------------------------------------ #
#  Chat dataclasses
# ------------------------------------------------------------------ #

@dataclass
class ChatPayload:
    payloadTypeEnum: int = 0
    meta: str = ""
    msgVal: str = ""

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> ChatPayload:
        if not d:
            return cls()
        return cls(
            payloadTypeEnum=d.get("payloadTypeEnum", 0),
            meta=d.get("meta", ""),
            msgVal=d.get("msgVal", ""),
        )


@dataclass
class UnifyPlayerHead:
    name: str = ""
    avatarCfgID: int = 0
    frame: int = 0
    heroID: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> UnifyPlayerHead:
        if not d:
            return cls()
        return cls(
            name=d.get("name", ""),
            avatarCfgID=d.get("avatarCfgID", 0),
            frame=d.get("frame", 0),
            heroID=d.get("heroID", 0),
        )


@dataclass
class PlayerHeadInfo:
    ID: int = 0
    unionID: int = 0
    unionNickName: str = ""
    unionName: str = ""
    unionFlag: int = 0
    sid: int = 0
    unionOfficialPosition: int = 0
    head: Optional[UnifyPlayerHead] = None

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> PlayerHeadInfo:
        if not d:
            return cls()
        head_raw = d.get("head")
        return cls(
            ID=d.get("ID", 0),
            unionID=d.get("unionID", 0),
            unionNickName=d.get("unionNickName", ""),
            unionName=d.get("unionName", ""),
            unionFlag=d.get("unionFlag", 0),
            sid=d.get("sid", 0),
            unionOfficialPosition=d.get("unionOfficialPosition", 0),
            head=UnifyPlayerHead.from_dict(head_raw) if head_raw else None,
        )


@dataclass
class ChatOneMsg:
    payload: Optional[ChatPayload] = None
    timeStamp: int = 0
    historyId: str = ""
    sourceType: int = 0
    fromId: str = ""
    playerInfo: Optional[PlayerHeadInfo] = None

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> ChatOneMsg:
        if not d:
            return cls()
        payload_raw = d.get("payload")
        player_raw = d.get("playerInfo")
        return cls(
            payload=ChatPayload.from_dict(payload_raw) if payload_raw else None,
            timeStamp=d.get("timeStamp", 0),
            historyId=d.get("historyId", ""),
            sourceType=d.get("sourceType", 0),
            fromId=d.get("fromId", ""),
            playerInfo=PlayerHeadInfo.from_dict(player_raw) if player_raw else None,
        )


# ------------------------------------------------------------------ #
#  Rally dataclasses
# ------------------------------------------------------------------ #

@dataclass
class RallyTroopDetail:
    troopID: int = 0
    cityLevel: int = 0
    name: str = ""
    avatarCfgID: int = 0
    power: int = 0
    ownerID: int = 0
    combatPower: int = 0
    serverId: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> RallyTroopDetail:
        if not d:
            return cls()
        return cls(
            troopID=d.get("troopID", 0),
            cityLevel=d.get("cityLevel", 0),
            name=d.get("name", ""),
            avatarCfgID=d.get("avatarCfgID", 0),
            power=d.get("power", 0),
            ownerID=d.get("ownerID", 0),
            combatPower=d.get("combatPower", 0),
            serverId=d.get("serverId", 0),
        )


@dataclass
class Rally:
    rallyTroopID: int = 0
    rallyCoord: Optional[Coord] = None
    unionNickName: str = ""
    rallyMaxNum: int = 0
    rallyBeginTS: int = 0
    rallyState: int = 0
    rallyStateEndTS: int = 0
    troops: List[RallyTroopDetail] = field(default_factory=list)
    rallyPowerLimit: int = 0
    # Complex nested fields left as raw dicts
    playerCity: Optional[Dict[str, Any]] = None
    npcCity: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Rally:
        if not d:
            return cls()
        coord_raw = d.get("rallyCoord")
        troops_raw = d.get("troops", [])
        return cls(
            rallyTroopID=d.get("rallyTroopID", 0),
            rallyCoord=Coord.from_dict(coord_raw) if coord_raw else None,
            unionNickName=d.get("unionNickName", ""),
            rallyMaxNum=d.get("rallyMaxNum", 0),
            rallyBeginTS=d.get("rallyBeginTS", 0),
            rallyState=d.get("rallyState", 0),
            rallyStateEndTS=d.get("rallyStateEndTS", 0),
            troops=[RallyTroopDetail.from_dict(t) for t in troops_raw],
            rallyPowerLimit=d.get("rallyPowerLimit", 0),
            playerCity=d.get("playerCity"),
            npcCity=d.get("npcCity"),
        )


# ------------------------------------------------------------------ #
#  Intelligence dataclass
# ------------------------------------------------------------------ #

@dataclass
class Intelligence:
    act: int = 0
    target: int = 0
    troopID: int = 0
    enemyID: int = 0
    name: str = ""
    cityCoord: Optional[Coord] = None
    startTime: int = 0
    arriveTime: int = 0
    from_coord: Optional[Coord] = None
    to_coord: Optional[Coord] = None
    cityLevel: int = 0
    unionNickName: str = ""
    targetID: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> Intelligence:
        if not d:
            return cls()
        city_raw = d.get("cityCoord")
        from_raw = d.get("from_coord") or d.get("fromCoord")
        to_raw = d.get("to_coord") or d.get("toCoord")
        return cls(
            act=d.get("act", 0),
            target=d.get("target", 0),
            troopID=d.get("troopID", 0),
            enemyID=d.get("enemyID", 0),
            name=d.get("name", ""),
            cityCoord=Coord.from_dict(city_raw) if city_raw else None,
            startTime=d.get("startTime", 0),
            arriveTime=d.get("arriveTime", 0),
            from_coord=Coord.from_dict(from_raw) if from_raw else None,
            to_coord=Coord.from_dict(to_raw) if to_raw else None,
            cityLevel=d.get("cityLevel", 0),
            unionNickName=d.get("unionNickName", ""),
            targetID=d.get("targetID", 0),
        )


# ------------------------------------------------------------------ #
#  Notification dataclasses
# ------------------------------------------------------------------ #

@dataclass
class ChatOneMsgNtf:
    msg: Optional[ChatOneMsg] = None
    channelType: int = 0
    toPlayerId: int = 0
    toGroupId: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> ChatOneMsgNtf:
        if not d:
            return cls()
        msg_raw = d.get("msg")
        return cls(
            msg=ChatOneMsg.from_dict(msg_raw) if msg_raw else None,
            channelType=d.get("channelType", 0),
            toPlayerId=d.get("toPlayerId", 0),
            toGroupId=d.get("toGroupId", 0),
        )


@dataclass
class PowerNtf:
    powers: List[Power] = field(default_factory=list)
    maxPowers: List[Power] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> PowerNtf:
        if not d:
            return cls()
        return cls(
            powers=[Power.from_dict(p) for p in d.get("powers", [])],
            maxPowers=[Power.from_dict(p) for p in d.get("maxPowers", [])],
        )


@dataclass
class RallyNtf:
    rally: Optional[Rally] = None

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> RallyNtf:
        if not d:
            return cls()
        rally_raw = d.get("rally")
        return cls(
            rally=Rally.from_dict(rally_raw) if rally_raw else None,
        )


@dataclass
class RallyDelNtf:
    rallyTroopID: int = 0
    type: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> RallyDelNtf:
        if not d:
            return cls()
        return cls(
            rallyTroopID=d.get("rallyTroopID", 0),
            type=d.get("type", 0),
        )


@dataclass
class QuestChangeNtf:
    id: int = 0
    cfgID: int = 0
    questType: int = 0
    status: int = 0
    state: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> QuestChangeNtf:
        if not d:
            return cls()
        return cls(
            id=d.get("id", 0),
            cfgID=d.get("cfgID", 0),
            questType=d.get("questType", 0),
            status=d.get("status", 0),
            state=d.get("state", 0),
        )


@dataclass
class QuestsNtf:
    quests: List[Quest] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> QuestsNtf:
        if not d:
            return cls()
        return cls(
            quests=[Quest.from_dict(q) for q in d.get("quests", [])],
        )


@dataclass
class AssetNtf:
    assets: List[Asset] = field(default_factory=list)
    isInit: bool = False
    noPopUps: bool = False

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> AssetNtf:
        if not d:
            return cls()
        return cls(
            assets=[Asset.from_dict(a) for a in d.get("assets", [])],
            isInit=d.get("isInit", False),
            noPopUps=d.get("noPopUps", False),
        )


@dataclass
class EntitiesNtf:
    entities: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> EntitiesNtf:
        if not d:
            return cls()
        return cls(
            entities=d.get("entities", []),
            timestamp=d.get("timestamp", 0),
        )


@dataclass
class IntelligencesNtf:
    intelligences: List[Intelligence] = field(default_factory=list)
    playerID: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> IntelligencesNtf:
        if not d:
            return cls()
        return cls(
            intelligences=[
                Intelligence.from_dict(i) for i in d.get("intelligences", [])
            ],
            playerID=d.get("playerID", 0),
        )


@dataclass
class WoundedSoldierInfoNtf:
    waiting: List[Dict[str, Any]] = field(default_factory=list)
    healing: List[Dict[str, Any]] = field(default_factory=list)
    queueID: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> WoundedSoldierInfoNtf:
        if not d:
            return cls()
        return cls(
            waiting=d.get("waiting", []),
            healing=d.get("healing", []),
            queueID=d.get("queueID", 0),
        )


@dataclass
class BuffNtf:
    buffs: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> BuffNtf:
        if not d:
            return cls()
        return cls(
            buffs=d.get("buffs", []),
        )


@dataclass
class CombustionStateNtf:
    isCombustion: bool = False

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> CombustionStateNtf:
        if not d:
            return cls()
        return cls(
            isCombustion=d.get("isCombustion", False),
        )


@dataclass
class BroadcastGameNtf:
    cgfId: int = 0
    contexts: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> BroadcastGameNtf:
        if not d:
            return cls()
        return cls(
            cgfId=d.get("cgfId", 0),
            contexts=d.get("contexts", []),
        )


@dataclass
class BattleResultNtf:
    atkID: int = 0
    defID: int = 0
    atkResult: int = 0
    defResult: int = 0
    timestamp: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> BattleResultNtf:
        if not d:
            return cls()
        return cls(
            atkID=d.get("atkID", 0),
            defID=d.get("defID", 0),
            atkResult=d.get("atkResult", 0),
            defResult=d.get("defResult", 0),
            timestamp=d.get("timestamp", 0),
        )


# ------------------------------------------------------------------ #
#  Position / Entity tracking
# ------------------------------------------------------------------ #

@dataclass
class PositionNtf:
    """Entity position updates (server push).

    Note: field name is ``postions`` (misspelled) in the original proto.
    """
    postions: List[PosInfo] = field(default_factory=list)
    timestamp: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> PositionNtf:
        if not d:
            return cls()
        raw = d.get("postions") or d.get("positions", [])
        return cls(
            postions=[PosInfo.from_dict(p) for p in raw],
            timestamp=d.get("timestamp", 0),
        )


@dataclass
class DelEntitiesNtf:
    """Entity removal notification."""
    ids: List[int] = field(default_factory=list)
    timestamp: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> DelEntitiesNtf:
        if not d:
            return cls()
        return cls(
            ids=d.get("ids", []),
            timestamp=d.get("timestamp", 0),
        )


# ------------------------------------------------------------------ #
#  Lineup / Troop management
# ------------------------------------------------------------------ #

@dataclass
class LineupsNtf:
    """Full lineup data push (server → client)."""
    lineups: List[Lineup] = field(default_factory=list)
    defender: Optional[Lineup] = None
    locked: int = 0
    updateHero: bool = False

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> LineupsNtf:
        if not d:
            return cls()
        lineups_raw = d.get("lineups", [])
        defender_raw = d.get("defender")
        return cls(
            lineups=[Lineup.from_dict(lu) for lu in lineups_raw],
            defender=Lineup.from_dict(defender_raw) if defender_raw else None,
            locked=d.get("locked", 0),
            updateHero=d.get("updateHero", False),
        )


@dataclass
class NewLineupStateNtf:
    """Lineup state changes (troop deploy/recall/return)."""
    lineups: List[NewLineupStateInfo] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> NewLineupStateNtf:
        if not d:
            return cls()
        return cls(
            lineups=[NewLineupStateInfo.from_dict(lu) for lu in d.get("lineups", [])],
        )


@dataclass
class NewTroopAck:
    """March dispatch confirmation."""
    errCode: int = 0
    action: int = 0
    StatReqUuid: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> NewTroopAck:
        if not d:
            return cls()
        return cls(
            errCode=d.get("errCode", 0),
            action=d.get("action", 0),
            StatReqUuid=d.get("StatReqUuid", 0),
        )


@dataclass
class RedPointNtf:
    """Red dot notification data (UI badge indicators)."""
    Data: Dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> RedPointNtf:
        if not d:
            return cls()
        raw = d.get("Data", {})
        # Keys may come as strings from JSON decode — normalize to int
        data = {}
        for k, v in raw.items():
            try:
                data[int(k)] = int(v)
            except (ValueError, TypeError):
                pass
        return cls(Data=data)


@dataclass
class RallyPowerLimitAck:
    """Rally power limit settings response."""
    errCode: int = 0
    info: List[RallyPowerLimit] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> RallyPowerLimitAck:
        if not d:
            return cls()
        return cls(
            errCode=d.get("errCode", 0),
            info=[RallyPowerLimit.from_dict(i) for i in d.get("info", [])],
        )


# ------------------------------------------------------------------ #
#  Heartbeat
# ------------------------------------------------------------------ #

@dataclass
class HeartBeatReq:
    clientTS: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> HeartBeatReq:
        if not d:
            return cls()
        return cls(clientTS=d.get("clientTS", 0))


@dataclass
class HeartBeatAck:
    clientTS: int = 0
    serverTS: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> HeartBeatAck:
        if not d:
            return cls()
        return cls(
            clientTS=d.get("clientTS", 0),
            serverTS=d.get("serverTS", 0),
        )


# ------------------------------------------------------------------ #
#  Chat request / response
# ------------------------------------------------------------------ #

@dataclass
class ChatSendMsgReq:
    channelType: int = 0
    payload: Optional[ChatPayload] = None
    toPlayerId: int = 0
    clientUuid: str = ""
    sendTime: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> ChatSendMsgReq:
        if not d:
            return cls()
        payload_raw = d.get("payload")
        return cls(
            channelType=d.get("channelType", 0),
            payload=ChatPayload.from_dict(payload_raw) if payload_raw else None,
            toPlayerId=d.get("toPlayerId", 0),
            clientUuid=d.get("clientUuid", ""),
            sendTime=d.get("sendTime", 0),
        )


@dataclass
class ChatPullMsgReq:
    channelType: int = 0
    timeStamp: int = 0
    afterTimeStamp: bool = False
    count: int = 0
    toPlayerId: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> ChatPullMsgReq:
        if not d:
            return cls()
        return cls(
            channelType=d.get("channelType", 0),
            timeStamp=d.get("timeStamp", 0),
            afterTimeStamp=d.get("afterTimeStamp", False),
            count=d.get("count", 0),
            toPlayerId=d.get("toPlayerId", 0),
        )


@dataclass
class ChatPullMsgAck:
    errCode: int = 0
    channelType: int = 0
    msgList: List[ChatOneMsg] = field(default_factory=list)
    toPlayerId: int = 0

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> ChatPullMsgAck:
        if not d:
            return cls()
        return cls(
            errCode=d.get("errCode", 0),
            channelType=d.get("channelType", 0),
            msgList=[ChatOneMsg.from_dict(m) for m in d.get("msgList", [])],
            toPlayerId=d.get("toPlayerId", 0),
        )


@dataclass
class GetPlayerHeadInfoAck:
    errCode: int = 0
    heads: Dict[int, PlayerHeadInfo] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> GetPlayerHeadInfoAck:
        if not d:
            return cls()
        heads_raw = d.get("heads", {})
        heads: Dict[int, PlayerHeadInfo] = {}
        if isinstance(heads_raw, dict):
            for k, v in heads_raw.items():
                try:
                    pid = int(k)
                except (ValueError, TypeError):
                    continue
                heads[pid] = PlayerHeadInfo.from_dict(v) if isinstance(v, dict) else v
        return cls(
            errCode=d.get("errCode", 0),
            heads=heads,
        )


# ------------------------------------------------------------------ #
#  Lookup: class name -> dataclass type
# ------------------------------------------------------------------ #

MESSAGE_CLASSES: Dict[str, Type] = {
    # Leaf types
    "Coord": Coord,
    "Power": Power,
    "Quest": Quest,
    "Asset": Asset,
    "PosInfo": PosInfo,
    "LineupHero": LineupHero,
    "Lineup": Lineup,
    "NewLineupStateInfo": NewLineupStateInfo,
    "RallyPowerLimit": RallyPowerLimit,
    # Chat types
    "ChatPayload": ChatPayload,
    "UnifyPlayerHead": UnifyPlayerHead,
    "PlayerHeadInfo": PlayerHeadInfo,
    "ChatOneMsg": ChatOneMsg,
    # Rally types
    "RallyTroopDetail": RallyTroopDetail,
    "Rally": Rally,
    # Intelligence
    "Intelligence": Intelligence,
    # Notifications
    "ChatOneMsgNtf": ChatOneMsgNtf,
    "PowerNtf": PowerNtf,
    "RallyNtf": RallyNtf,
    "RallyDelNtf": RallyDelNtf,
    "QuestChangeNtf": QuestChangeNtf,
    "QuestsNtf": QuestsNtf,
    "AssetNtf": AssetNtf,
    "EntitiesNtf": EntitiesNtf,
    "PositionNtf": PositionNtf,
    "DelEntitiesNtf": DelEntitiesNtf,
    "LineupsNtf": LineupsNtf,
    "NewLineupStateNtf": NewLineupStateNtf,
    "NewTroopAck": NewTroopAck,
    "RedPointNtf": RedPointNtf,
    "RallyPowerLimitAck": RallyPowerLimitAck,
    "IntelligencesNtf": IntelligencesNtf,
    "WoundedSoldierInfoNtf": WoundedSoldierInfoNtf,
    "BuffNtf": BuffNtf,
    "CombustionStateNtf": CombustionStateNtf,
    "BroadcastGameNtf": BroadcastGameNtf,
    "BattleResultNtf": BattleResultNtf,
    # Heartbeat
    "HeartBeatReq": HeartBeatReq,
    "HeartBeatAck": HeartBeatAck,
    # Chat request / response
    "ChatSendMsgReq": ChatSendMsgReq,
    "ChatPullMsgReq": ChatPullMsgReq,
    "ChatPullMsgAck": ChatPullMsgAck,
    "GetPlayerHeadInfoAck": GetPlayerHeadInfoAck,
}
