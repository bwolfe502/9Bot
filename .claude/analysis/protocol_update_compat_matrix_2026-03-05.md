# Protocol Update Compatibility Matrix (2026-03-05)

## Scope
Compare protocol behavior across captures on **Thursday, March 5, 2026** before and after the game update/tutorial completion.

Data sources:
- `stats/protocol_baseline/session_20260305_1049_live` (inactive control)
- `stats/protocol_baseline/session_20260305_1052_active` (active baseline)
- `stats/protocol_baseline/session_20260305_1125_post_update` (post-update active)
- `/api/protocol-status` snapshots

## Compatibility Matrix
| Session | Timestamp window (PST) | Protocol active samples | Stats present | Recv delta | Send delta | Avg msg/s | Peak msg/s | Error delta | Unknown types (observed top10) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Inactive control | 10:49 | 0/10 | 0/10 | n/a | n/a | n/a | n/a | n/a | n/a |
| Pre-update active | 10:52 | 12/12 | 12/12 | +236 | +47 | 3.73 | 4.53 | 0 | none |
| Post-update active | 11:25 | 24/24 | 24/24 | +902 | +295 | 3.37 | 3.85 | 0 | none |

## Key Findings
1. **No protocol break from update once repatched**
- Post-update capture is fully live with stable counters and zero errors.

2. **Traffic composition shifted after update/tutorial flow**
- Pre-update end-of-window top types included:
  - `AssetNtf`, `CompressedMessage`, `RedPointNtf`, `ExploreAtlasRewardAck`, `RallyNtf`
- Post-update end-of-window top types included:
  - `CompressedMessage`, `EntitiesNtf`, `HeroSkillPropNtf`, `RedPointNtf`, `RallyNtf`, `AssetNtf`, `HeartBeatAck/Req`, `RedPointReq/Ack`

3. **Request-side activity increased post-update**
- `messages_sent` delta rose from `+47` to `+295` in sampled windows, consistent with more client interactions.

4. **Decoder health remained stable**
- Error deltas stayed at zero.
- No `UNKNOWN:0x...` entries appeared in observed top10 sets.

## Post-Update Message-Surface Notes
From top10-limited observations and schema map (`protocol/proto_field_map.json`):

- `RedPointNtf`: `Data` = `Dictionary<int, int>`
- `RedPointReq`: `Ids` = `List<int>`
- `RedPointAck`: `errCode`
- `UnionLandsNtf`: `lands` = `List<LandInfo>`
- `HeroSkillPropNtf`: `heroId`, `propList`
- `Mail2NdContentReq`: `mailId`
- `Mail2NdContentAck`: `errCode`
- `ExploreAtlasRewardAck`: rich payload (`atlasLv`, `taskLv`, `taskExp`, timers, tasks, rewards)

Implication: post-update gameplay is exposing additional high-value protocol domains (notification bus, mail, hero properties, territory lands, atlas/event progression).

## Research Confidence and Limits
- Confidence: high for liveness/stability conclusions.
- Limit: `top_message_types` only exposes top 10 per snapshot; long-tail message coverage is truncated.

## Recommended Next Research
1. Add full `msg_type_counts` export (debug-only) to remove top10 truncation bias.
2. Run a 30-minute mixed scenario capture and recompute candidate ranking.
3. Deep-dive semantic mapping for `RedPoint*` IDs (dictionary key taxonomy by gameplay area).
4. Deep-dive `UnionLandsNtf` + `LandInfo` change events for territory-aware automation opportunities.
