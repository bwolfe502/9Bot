# Headless Session Notes (2026-03-05)

## Objective
Validate whether headless protocol actions can replace UI flow for titan rally join.

## What Was Confirmed
- `join_rally` was patched to remove protocol early-bail and now navigates via UI path.
- UI one-shot `Join Titan Rally` successfully joined a rally after patch.
- True headless force-send path was exercised:
  - target: `NewTroopReq`
  - mode: `force`
  - once: `true`
  - trigger: `HeartBeatReq` (test override)
  - injector `applied` incremented (`0 -> 1`) without UI interaction.

## What Failed
- Post-condition for real rally join did not validate under replay-only headless send.
- User observed: `parameter error 5076` and `network fluctuation detected`.
- This indicates the replayed packet was context-invalid for server/game state.

## Safety Actions Taken
- Immediate queue clear after error.
- Verified injector disarmed (`armed=false`) and healthy (`errors=0`).

## Technical Interpretation
- Packet injection transport works.
- Static payload replay for action-changing packets is insufficient.
- Server likely validates context-dependent fields (session/target/timing/lineup state).

## Feasibility Assessment
- Headless remains feasible.
- Blind replay is low reliability.
- State-aware headless construction is the required path.

## Recommended Path Forward
1. Re-enable strict guardrails:
   - `PROTO_FORCE_BASELINE_REQUIRED=1`
   - disable heartbeat override for normal testing.
2. Build state-aware request construction for `NewTroopReq` (and later teleport):
   - derive dynamic fields from current game state.
   - emit only with context-correct triggers.
3. Validate each attempt with hard post-conditions:
   - troop status delta,
   - `NewTroopAck`/relevant ntf delta,
   - objective-side effect observed.
4. Keep one-shot + clear between runs.

## Session Bottom Line
- Headless is still achievable, but requires state-aware generation, not raw replay.
