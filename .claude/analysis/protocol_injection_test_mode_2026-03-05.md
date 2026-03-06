# Protocol Injection Test Mode (Own Server Only)

## What was added
- Frida hook queued outbound patch support (`queuepatch`, `clearpatch`, injection state in `status`).
- Python interceptor wrappers:
  - `queue_outbound_patch(msg_id, payload_hex, once=True)`
  - `clear_outbound_patch()`
  - `script_status()`
- Startup helpers (gated):
  - `PROTO_INJECT_TEST_MODE=1` required
  - allowlist enforcement by `msg_name` (`PROTO_INJECT_ALLOWED` optional override)
- Web APIs:
  - `GET /api/protocol-inject-status?device_id=...`
  - `POST /api/protocol-inject-queue`
  - `POST /api/protocol-inject-clear`
- CLI tool:
  - `python3 -m protocol.send_test ...`

## Safety model
- No arbitrary always-on injector.
- Single queued patch by default (auto-clear after one match).
- Message-name allowlist enforced in startup layer.
- Intended for isolated test backend only.

## Usage
1. Restart 9bot with env:
   - `PROTO_INJECT_TEST_MODE=1`
2. Check status:
   - `python3 -m protocol.send_test --device 127.0.0.1:5565 --status`
3. Queue a patch (example):
   - `python3 -m protocol.send_test --device 127.0.0.1:5565 --msg-name TeleportCityReq --payload-hex <hex_payload>`
4. Trigger the matching action in game once.
5. Re-check status to confirm apply count increments.
6. Clear queued patch if needed:
   - `python3 -m protocol.send_test --device 127.0.0.1:5565 --clear`

## Notes
- If queued payload is larger than target raw buffer bounds, hook marks `inject_skipped`.
- `--repeat` keeps patch armed for repeated matches.

## Headless Force-Send (No UI)
New mode added: force-send substitution in test mode.

Example (rewrite next heartbeat into `WildMapViewReq`):
- `python3 -m protocol.send_test --device 127.0.0.1:5565 --force --target-msg-name WildMapViewReq --trigger-msg-name HeartBeatReq --payload-hex 00`

Notes:
- This does not require a UI click when trigger is `HeartBeatReq`.
- Keep `once=true` (default) for safety.
- Always run `--clear` after tests.
