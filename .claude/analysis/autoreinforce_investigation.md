# Autoreinforce Investigation — Protocol Entity Detection

## Overview

First session on the `Autoreinforce` branch with live protocol monitoring.
Session: 2026-03-03 ~18:53 - ~21:03 (Windows, 2 devices: 127.0.0.1:5575, 127.0.0.1:5585)
Auto Reinforce Ally started on 5585 at 20:16:59.

---

## MONITOR Log Evidence — Full Analysis

Total [MONITOR] lines in logs/9bot.log: **3,997**

### Message type breakdown

| Message Type | Count | Notes |
|-------------|-------|-------|
| EntitiesNtf (standard) | 2,515 | type=1,3,4,6,30,42 — non-city entities |
| EntitiesNtf PLAYER_CITY | 27 | type=2 — player castles detected |
| PositionNtf | 1,500 | Entity position updates |
| UnionEntitiesNtf | 12 | Alliance entity messages |

---

### EntitiesNtf — Entity Type Distribution

Type 2 (PLAYER_CITY) is the relevant type for ally detection.

| type | count | interpretation |
|------|-------|----------------|
| 1 | 1,717 | Marching troops / army units |
| 2 | 29 | PLAYER_CITY (castles) |
| 3 | 4 | Unknown |
| 4 | 152 | Unknown |
| 6 | 596 | Unknown (appears frequently during reinforce probe) |
| 30 | 24 | Unknown |
| 42 | 3 | Unknown |

Unique entity IDs seen across all EntitiesNtf: **612**

---

### EntitiesNtf PLAYER_CITY — Key Finding: unionID=MISSING

All 27 PLAYER_CITY detections share the same structure:

```
[MONITOR] EntitiesNtf PLAYER_CITY id=<N> owner_keys=['1','2','3','4','6','7','9','10','11','12'] unionID=MISSING own_uid=0
```

**Critical observations:**
- `unionID=MISSING` on every single detection — the union/alliance field is not present in the
  protobuf message for PLAYER_CITY entities, or the field name/number mapping is wrong
- `own_uid=0` — the bot's own UID is 0, meaning the "am I in this player's alliance?" check
  cannot be performed
- Unique PLAYER_CITY entity IDs seen: 10 distinct IDs across the session
- PLAYER_CITY detections started at 20:27:31 (11 minutes after auto_reinforce_ally start)

**First detection (L20714):**
```
2026-03-03 20:27:31.034 [system] INFO  protocol.game_state: [MONITOR] EntitiesNtf PLAYER_CITY id=7613233153785700962 owner_keys=['1', '2', '3', '4', '6', '7', '9', '10', '11', '12'] unionID=MISSING own_uid=0
```

Context: Device was on ALLIANCE_QUEST screen at the time of detection.

**PLAYER_CITY IDs observed:**
- 7613233153785700962 (1 detection, 20:27)
- 7611719658789265506 (11 detections, 20:34 — repeated scroll over same castle)
- 7612056311141163954 (5 detections, 20:40-20:55)
- 7613229838070661717 (1 detection, 20:53)
- 7611866602404455035 (3 detections, 20:54-20:57)
- 7410282711412493577 (1 detection, 20:56)
- 7612002813028058076 (2 detections, 20:57)
- 7611774333615899794 (1 detection, 21:04)
- 7611960799625026402 (1 detection, 20:57 via UnionEntitiesNtf)
- 7613229833775694331 (1 detection via UnionEntitiesNtf, 20:23)

---

### UnionEntitiesNtf — Alliance Entity Messages

Only **12 lines** total, appearing at 2 distinct times:

**First occurrence (20:23:50, L19494-19499):**
```
[MONITOR] UnionEntitiesNtf received count=1
[MONITOR] UnionEntitiesNtf sample keys=['field_1', 'field_2', 'field_3', 'field_4', 'field_5'] type_field=2
[MONITOR] UnionEntitiesNtf entity id=7613229833775694331 type=2 is_ally=False
[MONITOR] UnionEntitiesNtf received count=1
[MONITOR] UnionEntitiesNtf sample keys=['field_1', 'field_2', 'field_3', 'field_4', 'field_5'] type_field=2
[MONITOR] UnionEntitiesNtf entity id=7613229833775694331 type=2 is_ally=False
```

**Second occurrence (20:39:26, L25100-25105):**
```
[MONITOR] UnionEntitiesNtf received count=1
[MONITOR] UnionEntitiesNtf sample keys=['field_1', 'field_2', 'field_3', 'field_4', 'field_5'] type_field=2
[MONITOR] UnionEntitiesNtf entity id=7611960799625026402 type=2 is_ally=False
```

**Key observations:**
- UnionEntitiesNtf IS being received (decoded correctly)
- Only 2 distinct entities seen in this message type
- Both report `is_ally=False` — neither is in the player's alliance
- The `type_field=2` is consistent with PLAYER_CITY type
- `keys=['field_1'...'field_5']` — only 5 fields, vs 6 in PLAYER_CITY via EntitiesNtf
- The message structure is decoded but the ally determination returns False for both

---

### PositionNtf — Entity Position Updates

Total: **1,500 lines** (825 `count=` lines + 707 `not in _entities` + some in-entities)

**All PositionNtf updates are "not in _entities":**
```
[MONITOR] PositionNtf count=1 ids=[7613247112433239704]
[MONITOR] PositionNtf id=7613247112433239704 not in _entities (coord=(2354396,3420907))
```

- 129 unique entity IDs appear in PositionNtf
- **All 707 coordinate-bearing lines report "not in _entities"**
- Entity `7613247112433239704` appears repeatedly at coordinates that drift over time:
  - (2354396, 3420907) at 20:17:28
  - (2357163, 3425321) at 20:18:34
  - (2354174, 3418908) at 20:19:55
  - (2354243, 3418940) at 20:20:18
  This looks like a marching unit being tracked

- World coordinate range observed: X 1,980,362–2,518,001, Y 3,142,268–3,713,921
  (large world map area — coordinates appear valid)

**Root cause of "not in _entities":** PositionNtf arrives for entities that were never
received via EntitiesNtf. The `_entities` dict only stores entities seen in EntitiesNtf.
Some entity types (particularly moving troops) are sent via PositionNtf without a prior
EntitiesNtf message, or they were evicted from the entity store.

---

## auto_reinforce_ally Session Activity

Started: `20:16:59 [127.0.0.1:5585] INFO runner: Started auto_reinforce_ally`
Subscription: `20:16:59 [127.0.0.1:5585] INFO runner: Subscribed to EVT_ALLY_CITY_SPOTTED`

**Observed behavior:** The runner subscribed and waited for EVT_ALLY_CITY_SPOTTED events.
No EVT_ALLY_CITY_SPOTTED was ever fired during the session (no reinforce_ally_castle calls
observed in the log). The runner was idle the entire session.

**Reason EVT_ALLY_CITY_SPOTTED was never fired:**
- PLAYER_CITY detections fired from EntitiesNtf, but `unionID=MISSING` and `own_uid=0`
  prevented ally identification
- UnionEntitiesNtf detections fired (2 entities), but both returned `is_ally=False`
- The EVT_ALLY_CITY_SPOTTED event requires a PLAYER_CITY entity to be confirmed as an ally

---

## Conclusions

### What works
1. **Protocol interception is running** — Frida hooks are active on both devices
2. **EntitiesNtf is decoded** — type=2 (PLAYER_CITY) entities are correctly identified
3. **UnionEntitiesNtf is decoded** — messages arrive and are processed
4. **PositionNtf coordinates are valid** — world coordinates are in the expected range
5. **Entity ID tracking is working** — unique IDs being assigned and tracked

### What is broken / missing

**Problem 1: unionID is always MISSING in EntitiesNtf PLAYER_CITY**

The protobuf field for alliance/union ID is not being extracted from PLAYER_CITY entities.
This means we cannot determine if a player city belongs to an ally. The `own_uid=0` compounds
this — the player's own union ID was never set, so the comparison always fails.

**Possible causes:**
- The union ID field in PLAYER_CITY's protobuf schema is not in `proto_field_map.json`
- The field number is different from what the decoder expects
- The field is nested in a sub-message that is not being decoded

**Problem 2: UnionEntitiesNtf only fires rarely (2 times in ~40 min session)**

UnionEntitiesNtf appears to be sent when alliance members scroll into view. Only 2 triggers
in 40 minutes suggests either: (a) the game rarely sends this message, or (b) we need to be
on a specific screen for it to fire.

**Problem 3: All PositionNtf entities are "not in _entities"**

129 unique entity IDs appear in PositionNtf that have no matching EntitiesNtf entry.
These may be ally troop movements that were sent via a different initial message path.

### Recommended Next Steps

1. **Inspect proto_field_map.json for PLAYER_CITY / entity type 2** — find which field
   number holds the union ID and verify it is mapped. If absent, add it.

2. **Capture own_uid from login/profile data** — HeartBeatReq or PlayerInfoNtf likely
   contains the player's own union ID. Set `own_uid` from this source.

3. **Log raw field values for type=2 entities** — print the actual decoded dict for a
   PLAYER_CITY entity to see all available fields including unmapped ones.

4. **Consider alternative: scan UnionEntitiesNtf entities** — when UnionEntitiesNtf fires
   with `is_ally=True`, those entities are confirmed allies. The bot could target those
   castles instead of relying on PLAYER_CITY type detection.

5. **Test with MAP screen open** — UnionEntitiesNtf may fire more frequently when the game
   is on the map scrolling view vs quest/alliance screens.

---

## Raw Log References

- First MONITOR line: L1 would be session start, first MONITOR at L17508 (20:16:44)
- First UnionEntitiesNtf: L19494 (20:23:50)
- First PLAYER_CITY: L20714 (20:27:31)
- auto_reinforce_ally start: L17573 (20:16:59)
- Session end: ~21:03 (last log entry)
