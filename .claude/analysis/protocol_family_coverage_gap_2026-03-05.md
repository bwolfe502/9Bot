# Protocol Family Coverage Gap (2026-03-05)

## Purpose
Quantify protocol message-surface breadth versus what is currently typed/routed/operationalized in 9Bot.

## Snapshot
- Wire registry size: 4,169 message IDs (`protocol/wire_registry.json`)
- Typed classes in `MESSAGE_CLASSES`: 43 (`protocol/messages.py`)
- Default routed semantic events: 15 (`protocol/events.py`)

## Post-update hotspot families (from live traffic + schema)
| Family | Total message names in registry (pattern match) | Typed today | Routed today | Notes |
|---|---:|---:|---:|---|
| `RedPoint*` | 9 | 1 (`RedPointNtf`) | 0 | High-frequency after update; likely notification bus for many features. |
| `UnionLands*` | 1 | 0 | 0 | Seen in top traffic pre/post; tied to territory/alliance land state. |
| `HeroSkill*` | 9 | 0 | 0 | `HeroSkillPropNtf` appeared frequently post-update. |
| `Mail2Nd*` | 60 | 0 | 0 | Large domain; `Mail2NdContentReq/Ack` observed in top traffic. |
| `ExploreAtlas*` | 16 | 0 | 0 | `ExploreAtlasRewardAck/Req` observed pre-update active window. |
| `Intelligence*` | 10 | 1 (`IntelligencesNtf`) | 1 | High-value defense signal, currently underused operationally. |
| `Buff*` | 27 | 1 (`BuffNtf`) | 1 | Routed and tracked; low current automation use. |
| `Combustion*` | 1 | 1 (`CombustionStateNtf`) | 1 | Routed and tracked; low current automation use. |
| `NewLineupState*` | 4 | 2 (`NewLineupStateInfo/Ntf`) | 0 (raw msg handlers) | Core to troop timing/scheduling logic. |
| `Entities*` | 4 | 2 (`EntitiesNtf`,`DelEntitiesNtf`) | 1 (`EntitiesNtf`) | Strong map-state backbone; high observed volume. |

## Current routed semantic map (reference)
`RallyNtf`, `RallyDelNtf`, `QuestChangeNtf`, `PowerNtf`, `AssetNtf`, `ChatOneMsgNtf`, `IntelligencesNtf`, `EntitiesNtf`, `BattleResultNtf`, `CombustionStateNtf`, `BuffNtf`, `TroopBackNtf`, `TroopMarchNtf`, `TroopStateChangeNtf`, `BroadcastGameNtf`.

## Gap interpretation
1. Current protocol production usage is strong but narrow relative to available wire surface.
2. Post-update traffic surfaced multiple high-volume families with little/no typed/routed coverage (`RedPoint*`, `HeroSkill*`, `Mail2Nd*`, `ExploreAtlas*`, `UnionLandsNtf`).
3. Highest ROI research work is semantic decoding of these high-traffic families before expanding low-frequency long-tail messages.

## Recommended packet-hunt order
1. `RedPointNtf/Req/Ack` (high-frequency, cross-feature trigger potential)
2. `UnionLandsNtf` (+ `LandInfo` deltas)
3. `HeroSkillPropNtf`
4. `Mail2NdContentReq/Ack` (+ head/list events)
5. `ExploreAtlas*` event chain

## What to capture next
Run short targeted scenarios with protocol active and log packet appearance windows:
- Open/close red-dot UI surfaces (quests, events, mail, alliance)
- Enter territory/alliance land views
- Open hero skill screens
- Open mail lists and specific mail content
- Open atlas/event reward pages

This will maximize new packet discovery per minute while staying in normal gameplay.
