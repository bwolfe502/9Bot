# Protocol Message Frequency Baseline Run (2026-03-05)

## Run outcome
Baseline capture attempt was blocked by environment networking restrictions in the coding sandbox.

Observed error from `curl` to local dashboard API:
- `Failed to connect to 127.0.0.1 port 8080`
- `Immediate connect fail for 127.0.0.1: Operation not permitted`

## Artifacts generated
- Snapshot directory (failed samples):
  - `stats/protocol_baseline/session_20260305_1045/`
- Current samples contain:
  - `{"error":"curl_failed"}`

## Interpretation
- This is an environment constraint, not an application-level protocol failure.
- No valid `/api/protocol-status` payloads were captured in this run.

## Host-side command to run baseline successfully
Run this in your normal local shell (outside this restricted sandbox) while `run_web.py --headless` is active:

```bash
mkdir -p stats/protocol_baseline/session_$(date +%Y%m%d_%H%M)
SESSION_DIR=$(ls -dt stats/protocol_baseline/session_* | head -n1)

for i in {1..30}; do
  ts=$(date +%Y%m%d_%H%M%S)
  curl -sS -m 5 http://127.0.0.1:8080/api/protocol-status \
    > "$SESSION_DIR/protocol_status_${ts}.json"
  sleep 30
done
```

## Quick validation command
After capture:

```bash
rg -n '"devices"|"messages_received"|"top_message_types"|"errors"' "$SESSION_DIR" -S | head -n 40
```

## Next step after successful host run
Populate the baseline table in:
- `.claude/analysis/protocol_message_frequency_baseline.md`

using deltas from first/last snapshot per device/profile.
