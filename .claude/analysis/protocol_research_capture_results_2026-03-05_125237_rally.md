# Protocol Research Capture Results (Rally Focus) - 2026-03-05 12:52:37

## Run Metadata
- Session dir: `stats/protocol_baseline/session_20260305_125237_research_capture`
- Device: `127.0.0.1:5565`
- Duration: 12 minutes
- Interval: 5 seconds
- Samples: 144
- Scenario: rally lifecycle loops (open/join/start/disband/quit) + final idle

## Rally-Correlated Deltas
Strongest rally-family outcomes from this focused run:

- `RallyNtf`: +304
- `RallyAutoPanelReq/Ack`: +44/+44
- `RallyListReq/Ack`: +21/+42
- `RallyJoinCountReq/Ack`: +21/+21
- `RallyAutoUpdateReq/Ack`: +17/+17
- `RallyPowerLimitReq/Ack`: +12/+12
- `RallyDelNtf`: +27
- `RallyKickOutReq/Ack`: +4/+4
- `DisbandRallyReq/Ack`: +3/+3
- `RallyQuitReq/Ack`: +2/+2
- `RallyJoinCountNtf`: +2

Interpretation: rally lifecycle is now cleanly mapped with multiple request/ack pairs and high-volume notifications.

## Related Movement/Combat Deltas
- `WildMapViewReq/Ack`: +63/+109
- `CancelMapViewReq/Ack`: +12/+12
- `NewTroopReq/Ack`: +17/+17
- `NewMarchReq/Ack`: +4/+4
- `RecallMarchReq/Ack`: +1/+1

Interpretation: map/rally loops naturally co-trigger troop/march and map-view families.

## Noise Observations
- `RankListReq/Ack`: +34/+61
- `PlayerGetCrossRankListReq/Ack`: +12/+24

These appear as background/menu side effects and can be filtered in action attribution.

## Signals Snapshot
Latest normalized signals at end of run:
- Domains:
  - `redpoint`: 430
  - `mail2nd`: 70
- Actions:
  - `redpoint:update`: 166
  - `redpoint:query_ack`: 133
  - `redpoint:query`: 131
  - `mail2nd:head_list_req/ack`: 19/19
  - `mail2nd:content_req/ack`: 5/5
  - `mail2nd:new_mail`: 22

## Actionable Outcome
This run validates that we can infer rally lifecycle state transitions from protocol-only features:
- Panel open/refresh: `RallyAutoPanel*`, `RallyList*`, `RallyJoinCount*`
- Join/start path: `NewTroop*`, `NewMarch*`, `RallyNtf`
- Exit/terminate path: `RallyQuit*`, `DisbandRally*`, `RallyDelNtf`

Next recommended focused capture: one short `teleport + map-entity` scenario to isolate navigation-family packets without rally overlap.
