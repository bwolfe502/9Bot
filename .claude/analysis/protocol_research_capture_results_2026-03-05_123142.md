# Protocol Research Capture Results (2026-03-05 12:31:42)

## Run Metadata
- Session dir: `stats/protocol_baseline/session_20260305_123142_research_capture`
- Device: `127.0.0.1:5565`
- Duration: 15 minutes
- Interval: 5 seconds
- Samples: 180

## High-Signal Delta Summary
Top combined deltas indicate strongest UI/action-correlated protocol surfaces:

1. `RedPointNtf` (+348)
2. `RedPointReq` (+271), `RedPointAck` (+271)
3. `AssetNtf` (+271)
4. `CompressedMessage` (+145)
5. `RankListReq/Ack` (+56/+99)
6. `BountyGuildRefreshReq/Ack` (+56/+56)
7. `WildMapViewReq/Ack` (+46/+70)
8. `Mail2NdContentReq/Ack/Ntf` (+40/+40/+47)
9. `Mail2NdDelReq/Ack/Ntf` (+39/+39/+39)
10. `HeroLvlUpReq/Ack` (+11/+22), `WitchEquipUpLvReq/Ack` (+21/+21)

Interpretation: redpoint + mail + world view + guild systems are currently the most reproducible client-driven protocol families.

## Directional Findings
### Send-heavy (client-originating) message families
- `RedPointReq` (+271)
- `HeartBeatReq` (+81)
- `RankListReq` (+56)
- `BountyGuildRefreshReq` (+56)
- `WildMapViewReq` (+46)
- `Mail2NdContentReq` (+40)
- `Mail2NdDelReq` (+39)
- `WitchEquipUpLvReq` (+21)
- `PlayerGetCrossRankListReq` (+17)
- `Mail2NdHeadListReq` (+14)

### Receive-heavy (server-originating) message families
- `RedPointNtf` (+348)
- `AssetNtf` (+271)
- `CompressedMessage` (+145)
- `RankListAck` (+99)
- `QuestChangeNtf` (+82)
- `LineupsNtf` (+76)
- `WildMapViewAck` (+70)
- `PowerNtf` (+62)
- `UpdateGiftNtf` (+55)
- `HeroInfoNtf` (+53)

## Signal Stream (normalized)
Latest protocol signal counts from `/api/protocol-signals`:

- Domains:
  - `redpoint`: 490
  - `mail2nd`: 10

- Actions:
  - `redpoint:update`: 186
  - `redpoint:query_ack`: 153
  - `redpoint:query`: 151
  - `mail2nd:content_req`: 3
  - `mail2nd:content_ack`: 3
  - `mail2nd:head_list_req`: 2
  - `mail2nd:head_list_ack`: 2

Interpretation: redpoint instrumentation is stable and high-volume; mail2nd instrumentation is low-volume but clean and usable.

## Newly Observed Low-Frequency, High-Value Candidates
The run captured additional once-only/rare packets useful for targeted scenario testing:

- Rally: `DisbandRallyReq/Ack`, `RallyNtf`, `RallyDelNtf`
- Teleport: `TeleportCityReq/Ack`, `TeleportCityInNtf`, `TeleportCityOutNtf`
- Scout: `NewScoutReq/Ack`
- Chat: `ChatOneMsgNtf`, `ChatSendMsgNtf`
- Economy/shop: `VipOpenShopReq/Ack`, `BuyAdGoodReq/Ack`, `ItemStoreBuyReq/Ack`
- Event-specific: `DragonArenaQueryReq/Ack`, `SeasonAchievementReq/Ack`

## Recommended Next Capture Scenarios
1. Rally lifecycle run: create/join/cancel/disband rallies repeatedly.
2. Teleport + world map run: chained teleports, map pans, entity taps.
3. Mail stress run: read/delete/claim attachments in rapid sequence.
4. Alliance + chat run: help requests, donations, short chat bursts.

## Notes
- Two unknown IDs were observed (`UNKNOWN:0xA417B8B7`, `UNKNOWN:0xA41C2D5C`) at low count.
- These should be included in a focused decode attempt with exact action timestamps.
