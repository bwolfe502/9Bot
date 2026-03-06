# Protocol Candidate Messages — Top 20 (Post-Update)

Date: 2026-03-05  
Capture window: ~2 minutes (24 snapshots @ 5s)  
Session: `stats/protocol_baseline/session_20260305_1125_post_update`

## Dataset summary
- Device: `127.0.0.1:5565`
- Protocol state: active throughout capture
- `messages_received` delta: `+902`
- `messages_sent` delta: `+295`

Method notes:
- Source: `/api/protocol-status` -> interceptor `stats.top_message_types`.
- `top_message_types` only exposes top 10 per snapshot.
- Observed increments below are **lower bounds** (`max_seen - min_seen`) while visible in top 10.

## Top 20 candidate list
Legend:
- Observed: seen in post-update top-10 traffic window
- Current usage: `prod` (already used directly), `state` (tracked in GameState), `gap` (high value but underused)

| Rank | Message | Observed | Lower-bound increment | Current usage | Why it matters next |
|---:|---|---|---:|---|---|
| 1 | `EntitiesNtf` | yes | 127 | state | World-entity stream backbone for map intelligence and event-driven automations. |
| 2 | `RallyNtf` | yes | 74 | prod | Already impacts rally flow; good expansion point for richer rally scoring/selection. |
| 3 | `AssetNtf` | yes | 56 | prod/state | Drives AP/resources; high traffic and stable signal for economy state/policy. |
| 4 | `DelEntitiesNtf` | yes | 33 | state | Entity lifecycle completion signal; useful for cleaner state transitions. |
| 5 | `CompressedMessage` | yes | 143 | infra | High-volume envelope; useful for decoder health/version drift detection. |
| 6 | `RedPointNtf` | yes | 81 | gap | High UI-notification activity; likely strong trigger source for event/task automation. |
| 7 | `RedPointReq` | yes | 25 | gap | Indicates client polling/interaction patterns; candidate for interaction intent modeling. |
| 8 | `HeartBeatAck` | yes | 7 | state | Server time/freshness anchor; critical for timer-accurate scheduling logic. |
| 9 | `HeartBeatReq` | yes | 7 | state | Pair with ack for protocol liveness/latency monitoring. |
| 10 | `HeroSkillPropNtf` | yes | 0 | gap | Frequent in top traffic; candidate for hero/progression intelligence features. |
| 11 | `Mail2NdContentReq` | yes | 0 | gap | Strong indicator of mail interactions; useful for alerting/ops workflows. |
| 12 | `Mail2NdContentAck` | yes | 0 | gap | Mail payload response path; enables message-level classification research. |
| 13 | `RankListAck` | yes | 0 | gap | Ranking/event context signal; could feed strategic target selection. |
| 14 | `UnionLandsNtf` | yes | 0 | gap | Alliance-territory updates; directly relevant to territory-aware automation. |
| 15 | `QuestChangeNtf` | yes | 0 | state | Already tracked; promising for protocol-native quest dispatch improvements. |
| 16 | `IntelligencesNtf` | no* | n/a | state/gap | High-value incoming attack intel; prime defense automation trigger. |
| 17 | `BattleResultNtf` | no* | n/a | state/gap | Enables closed-loop adaptive behavior based on outcomes. |
| 18 | `BuffNtf` | no* | n/a | state/gap | Direct visibility into shield/combat buffs for policy and safety gating. |
| 19 | `CombustionStateNtf` | no* | n/a | state/gap | Burning-state trigger for defense/escalation workflows. |
| 20 | `NewLineupStateNtf` | no* | n/a | state/gap | Precise troop state timing for march orchestration and slot scheduling. |

`*` Not observed in this 2-minute top10-limited window; message is still part of current routed/tracked protocol model.

## Immediate research priorities
1. `EntitiesNtf` + `DelEntitiesNtf` + `PositionNtf` correlation for robust entity lifecycle modeling.
2. `RedPointNtf/Req/Ack` semantic decoding to map UI-notification causes to actionable domains.
3. `IntelligencesNtf` + `BuffNtf` + `CombustionStateNtf` scenario captures for defense automation feasibility.
4. `NewLineupStateNtf` timer accuracy validation against real march completion times.

## Next capture recommendation
To improve confidence and reduce top10 truncation bias:
- Capture 15-30 minutes mixed gameplay.
- Export full `msg_type_counts` (not just top10) via debug endpoint or periodic file dump.
