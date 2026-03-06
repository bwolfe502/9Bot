# Protocol Message Frequency Baseline

Date: 2026-03-05  
Purpose: establish a repeatable baseline of protocol traffic by message type and device activity profile.

## Why this exists
The protocol stack already tracks message throughput and top message names in runtime stats (`ProtocolInterceptor.stats`). This document defines a standard way to collect and compare those metrics across sessions.

## Data sources
- Runtime stats: `protocol/interceptor.py` (`stats` property)
- API surface: `/api/protocol-status` in `web/dashboard.py`
- Device protocol lifecycle: `startup.py`

## Current observable metrics (no code changes)
Per active device, available now:
- `messages_received`
- `messages_sent`
- `bytes_received`
- `bytes_sent`
- `errors`
- `uptime_seconds`
- `messages_per_second`
- `top_message_types` (top 10 by count)

## Known limitation
- `top_message_types` is capped to top 10, so this baseline is a partial distribution.
- Full histogram by message name is not exposed yet.

## Important caveat
`/api/protocol-status` currently computes `connected` with `stats.get("uptime_s", 0) > 0`, but interceptor stats return `uptime_seconds`.  
For baseline work, trust `stats` presence and `uptime_seconds > 0` over the `connected` flag.

## Standard session profiles
Run each profile at least 15 minutes per device.

1. Idle city baseline
- No manual actions; just normal heartbeat/background traffic.

2. Rally activity baseline
- Create/join/end rallies repeatedly.

3. Troop movement baseline
- Deploy and return marches continuously.

4. Chat-heavy baseline
- Alliance/world chat send/pull activity.

5. Mixed gameplay baseline
- Typical bot operation with quests/rallies/chat.

## Collection procedure
1. Ensure protocol is enabled and active for target device(s).
2. Start session timer and mark profile + device list.
3. Poll `/api/protocol-status` every 30 seconds for the session duration.
4. Save raw JSON snapshots and derive aggregate summary.

## Example polling command
```bash
mkdir -p stats/protocol_baseline
for i in {1..30}; do
  ts=$(date +%Y%m%d_%H%M%S)
  curl -s http://127.0.0.1:5000/api/protocol-status > "stats/protocol_baseline/protocol_status_${ts}.json"
  sleep 30
done
```

## Extraction checklist from snapshots
For each device/session, compute:
- Start/end counters and deltas for recv/send bytes/messages
- Average and peak `messages_per_second`
- Error delta
- Frequency of message names appearing in `top_message_types`
- Share of unknown types (`UNKNOWN:0x...`) when present

## Baseline table template
| Date | Device | Profile | Duration (min) | Recv msgs delta | Send msgs delta | Avg msg/s | Peak msg/s | Errors delta | Top 5 message types | Unknown types seen |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| YYYY-MM-DD | emulator-5554 | idle | 15 | 0 | 0 | 0.00 | 0.00 | 0 | n/a | no |

## Interpretation guidance
- High `messages_per_second` with low errors and stable top types indicates healthy decode path.
- Growing unknown-ID presence after an app update is a likely schema drift signal.
- Large receive/send ratio shifts between runs can indicate behavior-mode differences (idle vs combat/chat) or instrumentation issues.

## Comparison rubric across sessions
- Stable: key rates and top types within +/-15% for same profile
- Watch: +/-15-35% variance or intermittent unknown IDs
- Drift: >35% variance, sustained unknown IDs, or rising error rate

## Recommended next increment (optional)
To upgrade from top-10 partial view to full histogram research quality:
1. Expose full `msg_type_counts` via debug-only API endpoint, or
2. Periodically export full counters to file from interceptor thread.

That would enable exact Zipf curves, long-tail detection, and domain-level traffic attribution.

## Deliverable status
This file defines the standardized baseline procedure and templates.  
Next deliverable to generate from actual runs: `protocol_candidate_messages_top20.md`.
