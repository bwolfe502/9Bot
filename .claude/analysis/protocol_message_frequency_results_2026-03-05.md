# Protocol Message Frequency Results (2026-03-05 10:49 PST)

Session dir: `stats/protocol_baseline/session_20260305_1049_live`

## Capture summary
- Snapshots captured: 10
- Device rows parsed: 10
- Device: `127.0.0.1:5565`
- `enabled=true` in all snapshots: true
- `active=true` samples: 0/10
- `connected=true` samples: 0/10
- `stats!=null` samples: 0/10

## Baseline table row
| Date | Device | Profile | Duration (min) | Recv msgs delta | Send msgs delta | Avg msg/s | Peak msg/s | Errors delta | Top 5 message types | Unknown types seen |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 2026-03-05 | 127.0.0.1:5565 | idle (protocol inactive) | 0.8 | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

## Interpretation
- API capture worked.
- Protocol was configured (`enabled=true`) but not running (`active=false`, `stats=null`) for the entire window.
- This yields a valid control datapoint (inactive baseline), not a traffic baseline.

## To capture real traffic baseline
1. Ensure protocol is active for the device (`active=true` in `/api/protocol-status`).
2. Keep game session open and generating events.
3. Re-run the same polling loop for 15+ minutes.
