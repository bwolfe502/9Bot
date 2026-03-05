# Protocol Action Plan (2026-03-05)

## Goal
Turn raw datamine output into an execution roadmap for useful, low-risk protocol features.

## Top 10 Builds
| # | Feature | Packet families | Effort | Risk |
|---:|---|---|---|---|
| 1 | Red-dot task router | `RedPointNtf/Req/Ack` | M | Low |
| 2 | Mail monitor + classifier | `Mail2NdHeadListReq`, `Mail2NdContentReq`, `Mail2NdNewMailNtf` | M | Low |
| 3 | Rally intelligence panel | `RallyNtf`, `RallyDelNtf`, `RallyListReq/Ack`, `RallyJoinCount*` | M | Low |
| 4 | Map activity tracker | `WildMapViewReq/Ack`, `EntitiesNtf`, `PositionNtf`, `DelEntitiesNtf` | M | Low |
| 5 | Territory/union watcher | `UnionLandsNtf`, `CoordIsUnionAreaReq/Ack`, `Union*` | M | Low |
| 6 | KVK event tracker | `Kvk*Req/*Ack/*Ntf` families | M/H | Low |
| 7 | Shop/events notifier | `Shop*`, `BP*`, `Actv*` | M | Low |
| 8 | Quest protocol mode | `QuestChangeNtf`, `QuestInfoReq`, `SeasonChallenge*` | M | Low |
| 9 | Outbound behavior profiler | all `*Req` + `send` direction counters | S/M | Low |
| 10 | Update drift guard | `UNKNOWN:*`, per-family count deltas, schema diffs | S | Low |

## What each unlocks
1. Red-dot task router
- Auto-prioritize actionable menus (claims, events, mail) from protocol signals.

2. Mail monitor + classifier
- Alert when high-priority mail arrives and label by type (battle/reward/system).

3. Rally intelligence panel
- Show joinable rallies, churn, participation trends without extra UI polling.

4. Map activity tracker
- Near-real-time movement/activity heat from entity spawn/move/despawn streams.

5. Territory/union watcher
- Detect alliance area changes and produce territory-aware action hints.

6. KVK event tracker
- Turn noisy KVK packets into concise status board + reminders.

7. Shop/events notifier
- Detect limited offers/reset windows and highlight relevant actions.

8. Quest protocol mode
- Reduce OCR dependence for quest progression decisions.

9. Outbound behavior profiler
- Learn exact client request patterns from normal gameplay interactions.

10. Update drift guard
- Catch protocol changes quickly after game updates before features break.

## Build order (practical)
1. `RedPoint*` + `Mail2Nd*`
2. `WildMap*` + `Entities/Position`
3. `Rally*`
4. `UnionLands*` + `Union*`
5. `Kvk*` + `Shop/BP/Actv*`
6. Drift guard automation

## Minimal implementation plan
1. Add per-family parser modules (`protocol/research/parsers/*`).
2. Add normalized event model (`domain`, `action`, `entity`, `priority`, `ts`).
3. Add dashboard endpoint for normalized events.
4. Add scenario scripts for reproducible packet capture.
5. Add regression checks for top families and unknown-ID spikes.

## Safety posture
- Keep live account usage read-only / observational.
- Avoid crafted packet transmit flows on main account.
- Use throwaway account/environment for any active send experiments.

## Success criteria
1. 80%+ of frequent outbound `*Req` mapped to a menu/action context.
2. 0 protocol decode errors in normal 30-min run.
3. Post-update drift report generated within 15 minutes of first run.
4. At least 3 production-visible features shipped from protocol-only signals.
