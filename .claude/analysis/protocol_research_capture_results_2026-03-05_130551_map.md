# Protocol Research Capture Results (Teleport + Map Focus) - 2026-03-05 13:05:51

## Run Metadata
- Session dir: `stats/protocol_baseline/session_20260305_130551_research_capture`
- Device: `127.0.0.1:5565`
- Duration: 10 minutes
- Interval: 5 seconds
- Samples: 120
- Scenario: world map pan + entity taps + favorites/search/coords + teleport cycles + idle tail

## Map/Navigation-Correlated Deltas
High-confidence navigation family mapping from this focused run:

- `WildMapViewReq/Ack`: +169/+283
- `ViewEntitiesReq/Ack`: +12/+12
- `CoordsFavoritesReq/Ack`: +5/+10
- `TeleportCityReq/Ack`: +7/+7
- `TeleportCityInNtf/OutNtf`: +7/+7
- `PlayerMapUnitsReq/Ack`: +2/+2
- `EnterWorldReq/Ack`: +1/+1
- `CancelMapViewReq/Ack`: +1/+1

Interpretation: teleport and map navigation packets are now strongly isolated and reproducible.

## Practical Mapping
- Map browsing/panning: `WildMapView*`
- Entity detail open: `ViewEntities*`
- Coordinate/favorites panel interactions: `CoordsFavorites*`
- Teleport action lifecycle: `TeleportCityReq/Ack` + `TeleportCityInNtf/OutNtf`
- World context transitions: `EnterWorld*`, `CancelMapView*`

## Signal Stream Snapshot
Latest normalized signals at end of run:
- Domains:
  - `redpoint`: 423
  - `mail2nd`: 77
- Actions:
  - `redpoint:update`: 163
  - `redpoint:query_ack`: 131
  - `redpoint:query`: 129
  - `mail2nd:head_list_req/ack`: 21/21
  - `mail2nd:content_req/ack`: 6/6
  - `mail2nd:new_mail`: 23

## Combined Outcome (Rally + Map)
With this run plus the prior rally-focused run, two major domains are now well-covered:
1. Rally lifecycle (`Rally*`, `DisbandRally*`, `RallyDelNtf`)
2. Navigation/teleport lifecycle (`WildMapView*`, `TeleportCity*`, `ViewEntities*`)

Next recommended run: short `alliance + chat` focus to map social/coordination protocol families with similar clarity.
