# Packet Test Matrix (War-Prep, Isolated Server)

Date: 2026-03-05
Device: `127.0.0.1:5565`
Mode: `PROTO_INJECT_TEST_MODE=1`

## Goals
- Validate outbound patch pipeline reliability.
- Measure anti-cheat verdicts per packet family.
- Build go/no-go list for war-day automation confidence.

## Test Tiers
### Tier 1: Read/Query (lowest risk)
- `RallyListReq`
- `RallyJoinCountReq`
- `ViewEntitiesReq`

### Tier 2: Navigation/State
- `WildMapViewReq`
- `TeleportCityReq`

### Tier 3: Action-Changing
- `NewMarchReq`
- `RecallMarchReq`
- `DisbandRallyReq`

## Per-Packet Test Sequence (3 runs)
1. Baseline (no patch)
2. Known-good replay payload
3. One-field mutation payload

## Record Per Run
- `applied` / `skipped`
- server verdict: `accepted` / `flagged` / `rejected`
- client result: `normal` / `desync` / `error`
- notes (timing, side effects)

## Stop Rules
- Stop packet lane after 2 repeated `flagged`/`rejected` outcomes.
- Immediate clear (`/api/protocol-inject-clear`) after every run.
- Keep one-shot mode (`once=true`) always.

## Current Session Result
- Heartbeat safety checks passed (pipeline proven).
- Next active lane: Tier 1 `ViewEntitiesReq`.

## Tier 1 Run Log
- `ViewEntitiesReq` one-shot queued with payload `00` at ~13:43 PST.
- Result: not applied during verification window (packet not emitted), then cleared for safety.
- Injection counters remained stable (`skipped=0`, `errors=0`).
- `RallyJoinCountReq` one-shot queued with payload `00` at ~13:43 PST.
- Result: not applied during verification window (packet not emitted), then cleared for safety.
- Injection counters stable (`skipped=0`, `errors=0`).
- `RallyJoinCountReq` one-shot re-test: SUCCESS.
- Applied after single rally panel open (`applied` increment observed), auto-disarmed, manual clear run.
## Map Lane Run Log
- `WildMapViewReq` one-shot: SUCCESS.
- Applied after single map pan (`applied` increment observed), auto-disarmed, manual clear run.
- `ViewEntitiesReq` one-shot: SUCCESS.
- Applied after single entity tap/detail open (`applied` increment observed), auto-disarmed, manual clear run.
- `TeleportCityReq` one-shot: SUCCESS.
- Applied after teleport action (`applied` increment observed), auto-disarmed, manual clear run.
## March/Rally Control Run Log
- `NewTroopReq` one-shot: SUCCESS (via msg_id, after packet discovery).
- `RecallMarchReq` one-shot: SUCCESS (applied on second recall attempt).
- `DisbandRallyReq` one-shot: SUCCESS.

## Current Validated Packet Set
- Rally: `RallyAutoPanelReq`, `RallyJoinCountReq`, `DisbandRallyReq`
- Map: `WildMapViewReq`, `ViewEntitiesReq`, `TeleportCityReq`
- March: `NewTroopReq`, `RecallMarchReq`
