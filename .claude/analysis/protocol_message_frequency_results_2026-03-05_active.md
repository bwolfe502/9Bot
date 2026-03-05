# Protocol Message Frequency Results (2026-03-05 10:52 PST)

Session dir: `stats/protocol_baseline/session_20260305_1052_active`

## Capture summary
- Snapshots captured: 12
- Device rows with stats: 12
- Device: `127.0.0.1:5565`
- `enabled=true` samples: 12/12
- `active=true` samples: 12/12
- API `connected=true` samples: 0/12
- Interceptor `stats.connected=true` samples: 12/12

## Baseline table row
| Date | Device | Profile | Duration (min) | Recv msgs delta | Send msgs delta | Avg msg/s | Peak msg/s | Errors delta | Top 5 message types | Unknown types seen |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 2026-03-05 | 127.0.0.1:5565 | idle (protocol active) | 0.9 | 236 | 47 | 3.73 | 4.53 | 0 | AssetNtf(38), CompressedMessage(34), RedPointNtf(24), ExploreAtlasRewardAck(18), RallyNtf(18) | no |

## Counter deltas
- `bytes_received` delta: 134005
- `bytes_sent` delta: 294
- Start counters: recv=54, send=15, err=0
- End counters: recv=290, send=62, err=0

## Notes
- `/api/protocol-status` `connected` field may be false due key mismatch (`uptime_s` vs `uptime_seconds`).
- Use `stats.connected` and non-null `stats` as the reliable active indicator.
