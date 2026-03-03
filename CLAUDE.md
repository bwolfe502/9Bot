# 9Bot — AI Technical Reference

Android game automation bot: ADB screenshots + OpenCV template matching + EasyOCR.
Runs on Windows with BlueStacks or MuMu Player emulators. GUI built with tkinter.

## Module Map

| File | Purpose | Key exports |
|------|---------|-------------|
| `run_web.py` | Web-only entry point (primary) | `main` (pywebview + browser fallback) |
| `startup.py` | Shared initialization & shutdown | `initialize`, `shutdown`, `apply_settings`, `create_bug_report_zip`, `get_relay_config`, `device_hash`, `generate_device_token`, `generate_device_ro_token`, `validate_device_token`, `upload_bug_report`, `start_manual_upload`, `get_upload_progress`, `start_auto_upload`, `stop_auto_upload`, `upload_status`, `get_protocol_ap`, `get_protocol_rallies`, `get_protocol_troops_home`, `get_protocol_troop_snapshot`, `start_protocol_for_device`, `stop_protocol_for_device`, `get_protocol_chat_messages`, `get_protocol_stats` |
| `main.py` | Legacy GUI entry point (deprecated) | Tkinter app, `create_gui()` |
| `runners.py` | Shared task runners | `run_auto_quest`, `run_auto_titan`, `run_auto_groot`, `run_auto_pass`, `run_auto_occupy`, `run_auto_reinforce`, `run_auto_mithril`, `run_auto_gold`, `run_repeat`, `run_once`, `launch_task`, `stop_task`, `force_stop_all`, `stop_all_tasks_matching` |
| `settings.py` | Settings persistence | `DEFAULTS`, `load_settings`, `save_settings`, `SETTINGS_FILE` |
| `actions/` | Game actions package (7 submodules) | Re-exports all public functions via `__init__.py` |
| `actions/quests.py` | Quest system + tower quest + PVP attack | `check_quests`, `get_quest_tracking_state`, `get_quest_last_checked`, `reset_quest_tracking`, `occupy_tower`, `recall_tower_troop` |
| `actions/rallies.py` | Rally joining + blacklist | `join_rally`, `join_war_rallies`, `reset_rally_blacklist` |
| `actions/combat.py` | Attacks, targeting, teleport | `attack`, `phantom_clash_attack`, `reinforce_throne`, `target`, `teleport`, `teleport_benchmark` |
| `actions/titans.py` | Titan rally + AP restore | `rally_titan`, `restore_ap`, `_restore_ap_from_open_menu`, `_close_ap_menu`, `_MAX_TITAN_SEARCH_ATTEMPTS` |
| `actions/evil_guard.py` | Evil Guard attack sequence | `rally_eg`, `search_eg_reset`, `test_eg_positions`, `_handle_ap_popup` |
| `actions/farming.py` | Gold + mithril gathering | `mine_mithril`, `mine_mithril_if_due`, `gather_gold`, `gather_gold_loop` |
| `actions/_helpers.py` | Shared state + utilities | `_interruptible_sleep`, `_last_depart_slot` |
| `vision.py` | Screenshots, template matching, OCR, ADB input | `load_screenshot`, `find_image`, `find_all_matches`, `tap_image`, `wait_for_image_and_tap`, `read_text`, `read_number`, `read_ap`, `adb_tap`, `adb_swipe`, `adb_keyevent`, `timed_wait`, `tap`, `logged_tap`, `get_last_best`, `save_failure_screenshot`, `tap_tower_until_attack_menu`, `warmup_ocr` |
| `navigation.py` | Screen detection + state-machine navigation | `check_screen`, `navigate` |
| `troops.py` | Troop counting (pixel/protocol), status model, healing | `troops_avail`, `all_troops_home`, `heal_all`, `read_panel_statuses`, `get_troop_status`, `detect_selected_troop`, `capture_portrait`, `store_portrait`, `identify_troop`, `TroopAction`, `TroopStatus`, `DeviceTroopSnapshot` |
| `training.py` | Training data collector (JSONL + images) | `configure`, `log_template`, `log_ocr`, `log_screen`, `save_training_image`, `get_training_stats`, `shutdown` |
| `territory.py` | Territory grid analysis + auto-occupy | `attack_territory`, `scan_targets`, `_pick_target`, `auto_occupy_loop`, `open_territory_manager`, `diagnose_grid`, `scan_territory_coordinates`, `scan_test_squares` |
| `protocol/` | Frida Gadget protocol interception (package) | Re-exports all public classes via `__init__.py` |
| `protocol/interceptor.py` | Frida connection + hook loading | `ProtocolInterceptor` (start/stop/stats) |
| `protocol/frida_hook.js` | Frida Gadget hook script (loaded into game) | Hooks `TFW.NetMsgData.FromByte/MakeByte` via IL2CPP runtime API |
| `protocol/decoder.py` | Protobuf wire-format decoder | `decode_frame`, `decode_protobuf_raw`, `ProtobufDecoder`, `MessageStream` |
| `protocol/messages.py` | Typed message dataclasses | `Rally`, `EntitiesNtf`, `HeartBeatReq`, `MESSAGE_CLASSES`, etc. |
| `protocol/events.py` | Thread-safe event bus + message router | `EventBus`, `MessageRouter`, `EVT_*` constants |
| `protocol/game_state.py` | Per-device reactive game state | `GameState`, `GameStateRegistry`, `get_game_state` |
| `protocol/registry.py` | BKDR hash ↔ message name mapping | `bkdr_hash`, `msg_id`, `wire_id`, `get_wire_registry` |
| `protocol/patch_apk.py` | APK patching + pure Python signing | Pull splits from device, LIEF inject, v1 sign, install |
| `config.py` | Global mutable state, enums, constants | `QuestType`, `RallyType`, `Screen`, ADB path, thresholds, team colors, `alert_queue` |
| `devices.py` | ADB device detection + emulator window mapping | `auto_connect_emulators`, `get_devices`, `get_emulator_instances` |
| `botlog.py` | Logging, metrics, timing | `setup_logging`, `get_logger`, `set_console_verbose`, `StatsTracker`, `timed_action`, `stats`, `BOT_VERSION` |
| `web/dashboard.py` | Flask web dashboard (mobile remote control) | `create_app`, routes, auto-mode toggles |
| `license.py` | Machine-bound license keys | `get_license_key`, `activate_key`, `validate_license` |
| `tunnel.py` | WebSocket relay tunnel | `start_tunnel`, `stop_tunnel`, `tunnel_status` |
| `updater.py` | Auto-update from GitHub releases | `check_and_update`, `get_latest_release`, `get_current_version` |

## Dependency Graph

```
run_web.py (primary entry point)
  ├─ startup (initialize, shutdown)
  ├─ web/dashboard (create_app)
  └─ tunnel (optional relay)

main.py (legacy GUI — deprecated)
  ├─ config, settings, runners
  ├─ devices
  ├─ navigation ──┬─ vision ── config, botlog
  ├─ vision       │
  ├─ troops ──────┤
  ├─ actions ─────┘
  ├─ territory ── actions (teleport)
  └─ botlog (standalone)

runners.py (shared task runners)
  ├─ config, settings
  ├─ actions (public API)
  └─ troops, navigation, territory

actions/ package (internal deps — no cycles)
  _helpers   → (leaf — no deps)
  farming    → (leaf — no action deps)
  combat     → _helpers
  titans     → _helpers
  rallies    → _helpers
  evil_guard → titans, combat, _helpers
  quests     → (lazy) rallies, combat, titans, evil_guard, farming, _helpers

web/dashboard.py (Flask)
  ├─ config, devices, navigation, vision, troops, actions, territory, botlog
  ├─ runners (shared task runners — no duplication)
  └─ settings (shared persistence — no duplication)

startup.py (protocol integration)
  └─ protocol/ (lazy import — only when protocol_enabled=True)
      ├─ events (EventBus)
      ├─ interceptor (InterceptorThread → Frida)
      └─ game_state (GameState — reactive store)

vision.py → startup.get_protocol_ap() (lazy, when device in PROTOCOL_ACTIVE_DEVICES)
troops.py → startup.get_protocol_troops_home/troop_snapshot() (lazy, when device in PROTOCOL_ACTIVE_DEVICES)
```

`botlog.py` and `config.py` have no internal dependencies (safe to import anywhere).

## Enums (config.py)

All enums inherit from `_StrEnum(str, Enum)` with a `__format__` override for Python 3.14 f-string compatibility.

```python
QuestType: TITAN, EVIL_GUARD, PVP, GATHER, FORTRESS, TOWER
RallyType: CASTLE, PASS, TOWER, GROOT
Screen:    MAP, BATTLE_LIST, ALLIANCE_QUEST, TROOP_DETAIL, TERRITORY,
           WAR, PROFILE, ALLIANCE, KINGDOM, UNKNOWN, LOGGED_OUT
```

## Key Constants (config.py)

| Constant | Value | Purpose |
|----------|-------|---------|
| `ADB_COMMAND_TIMEOUT` | 10 | Timeout (seconds) for all ADB shell calls |
| `SCREEN_MATCH_THRESHOLD` | 0.8 | Template confidence for screen detection |
| `MAX_RALLY_ATTEMPTS` | 15 | Max iterations in rally join loop |
| `MAX_HEAL_ITERATIONS` | 20 | Max heal_all cycles (5 troops + safety buffer) |
| `QUEST_PENDING_TIMEOUT` | 360 | Seconds before pending rally expires (6 min) |
| `RALLY_PANEL_WAIT_ENABLED` | True | Use troop panel to wait for rallies |
| `RALLY_WAIT_POLL_INTERVAL` | 5 | Seconds between panel status polls |
| `DEBUG_SCREENSHOT_MAX` | 50 | Rolling cap on debug screenshots |
| `CLICK_TRAIL_MAX` | 50 | Rolling cap on click trail images |
| `FAILURE_SCREENSHOT_MAX` | 200 | Cap on persistent failure screenshots |
| `SQUARE_SIZE` | 42.5 | Territory grid square dimension (px) |
| `GRID_WIDTH`, `GRID_HEIGHT` | 24, 24 | Territory grid dimensions |
| `THRONE_SQUARES` | (11,11), (11,12), (12,11), (12,12) | Untouchable throne cells |
| `AP_COST_RALLY_TITAN` | 20 | AP cost per titan rally |
| `AP_COST_EVIL_GUARD` | 70 | AP cost per evil guard rally |
| `ALL_TEAMS` | ["yellow", "green", "red", "blue"] | All territory team colors |

## Mutable Global State (config.py)

All session-scoped, reset on restart:
- `DEVICE_TOTAL_TROOPS[device]` — Total troops per device (default 5)
- `LAST_ATTACKED_SQUARE[device]` — Last territory attack target
- `MANUAL_ATTACK_SQUARES` / `MANUAL_IGNORE_SQUARES` — Territory overrides (set of (row, col))
- `MIN_TROOPS_AVAILABLE` — Minimum troop threshold
- `AUTO_HEAL_ENABLED`, `AUTO_RESTORE_AP_ENABLED` — Feature toggles
- `DEVICE_STATUS[device]` — Current status message shown in GUI
- `MY_TEAM_COLOR`, `ENEMY_TEAMS` — Territory team config (`set_territory_config(my_team)` auto-derives enemies from `ALL_TEAMS`)
- `running_tasks` — Dict of active task_key → threading.Event (stop signals)
- `auto_occupy_running`, `auto_occupy_thread` — Territory auto-occupy state
- `MITHRIL_ENABLED_DEVICES` — Set of device IDs with mithril mining active (per-device toggle)
- `MITHRIL_INTERVAL`, `LAST_MITHRIL_TIME`, `MITHRIL_DEPLOY_TIME` — Mithril mining timing state
- `EG_RALLY_OWN_ENABLED`, `TITAN_RALLY_OWN_ENABLED` — If False, only join rallies — never start own
- `GATHER_ENABLED`, `GATHER_MINE_LEVEL`, `GATHER_MAX_TROOPS` — Gold gathering config
- `TOWER_QUEST_ENABLED` — Occupy tower for alliance quest
- `PROTOCOL_ENABLED` — Frida Gadget protocol interception (global toggle, not per-device)
- `PROTOCOL_ACTIVE_DEVICES` — Set of device IDs with active protocol interceptors (per-device)
- `CLICK_TRAIL_ENABLED` — Save click trail screenshots
- `BUTTONS` — Dict mapping button names to `{"x": int, "y": int}` coordinates (used by `vision.tap()`)

## Architecture Patterns

### Threading & Task Launching (runners.py + main.py)
- `run_web.py` uses werkzeug `make_server` in a daemon thread, with pywebview blocking the main thread (or browser fallback with infinite sleep loop)
- Legacy `main.py`: Main thread runs Tkinter event loop (GUI)
- Worker threads: Daemon threads per action, launched on button click
- `launch_task(device, task_name, target_func, stop_event, args)` — Spawns daemon thread (in `runners.py`)
- `stop_task(task_key)` — Sets the stop event and immediately sets device status to `"Stopping {label}..."` (in `runners.py`). `_MODE_LABELS` dict maps mode keys to human-readable names (e.g. `"auto_quest"` → `"Auto Quest"`). `stop_all_tasks_matching(suffix)` for bulk stop.
- `force_stop_all()` — Force-kills every running task thread immediately using `ctypes.pythonapi.PyThreadState_SetAsyncExc` to inject `SystemExit` into each thread at the next Python bytecode instruction. Sets stop events first (cooperative), then force-kills, then clears `running_tasks` and `DEVICE_STATUS`. Used by `stop_all()` in `web/dashboard.py` for the Stop All button.
- Per-device lock: `config.get_device_lock(device)` prevents concurrent tasks on same device
- Stop signals: `threading.Event()` stored in `config.running_tasks[task_key]`
- `TASK_FUNCTIONS` dict maps GUI labels → callable functions
- Looping is managed by `runners.py` task runners (`run_once` / `run_repeat`), not by actions. Actions accept a `stop_check` callback for cooperative cancellation
- `runners.py` is shared by both `main.py` (GUI) and `web/dashboard.py` (Flask) — no duplication
- Thread-local storage in vision.py for `get_last_best()` template scores
- **Error recovery**: Auto runners wrap their main loop in try/except, logging errors and continuing. Navigation failures retry after a short delay.
- **Smart idle status**: `_deployed_status(device)` in `run_auto_quest` reads the troop snapshot and shows "Gathering/Defending..." instead of generic "Waiting for Troops..." when all troops are deployed.
- **Periodic quest check**: `run_auto_quest` calls `check_quests` every 300s (`_QUEST_CHECK_INTERVAL`) when all troops are deployed, to detect quest completion and recall troops. Kept infrequent to reduce ADB/OCR contention across devices. `_last_quest_check[device]` is cleared on auto quest start to ensure the first iteration always checks.
- **ADB auto-reconnect**: `_try_reconnect(device)` in vision.py runs `adb connect` on TCP devices when `load_screenshot`/`adb_tap`/`adb_swipe` timeout. One retry after reconnect.

### Screen Resolution
Fixed **1080x1920** (portrait). All pixel coordinates, template regions, and OCR crop zones are calibrated to this resolution. Emulator must be set to this before running.

### Device Convention
Every game action takes `device` (ADB device ID string) as its **first argument**.
Device IDs are either `"127.0.0.1:<port>"` (TCP) or `"emulator-<port>"` (local ADB).

### Template Matching (vision.py)
- Templates stored in `elements/` directory as PNG files
- Uses `cv2.TM_CCOEFF_NORMED`, default threshold 0.8
- `IMAGE_REGIONS` dict constrains search area per template (faster than full-screen)
- Fallback to full-screen search if region miss (logs warning — region needs widening)
- Dynamic region learning: `StatsTracker` accumulates hit positions, auto-narrows search after 3+ hits
- `TAP_OFFSETS` dict: some templates need offset taps (e.g. depart.png +75px x to dodge chat overlay)
- `get_last_best()` returns thread-local best score on miss (useful for confidence logging)
- **Preferred over blind taps**: `wait_for_image_and_tap` replaces `logged_tap` where button position
  varies (e.g. `gather.png` in gold mine popup, where depart y-position varies: 950, 1128, 1307)
- **Depart Anyway fallback**: When troops are low health, the game shows `depart_anyway.png` instead
  of `depart.png`. All depart flows (rallies, titans, evil guard) check for this after `depart.png`
  fails. If `auto_heal` is on, heals first and retries. If off, taps Depart Anyway as last resort.

### OCR (vision.py)
- Windows: EasyOCR (deep learning, ~500-2000ms/call on CPU)
- macOS: Apple Vision framework (native, ~30ms/call)
- `read_text(screen, region, allowlist)` — text from screen region
- `read_number(screen, region)` — integer, handles comma/period thousands separators
- `read_ap(device, retries=5)` — returns `(current_ap, max_ap)` tuple. Tries protocol fast path
  first when `PROTOCOL_ENABLED` (instant, ~0ms), falls through to OCR on any failure or stale data
- `warmup_ocr()` — pre-initializes OCR in background thread at startup (downloads EasyOCR models
  on first run, ~10-30s; macOS triggers Apple Vision framework warmup)

**Memory hardening** (Windows/EasyOCR): `_ocr_infer_lock` serializes `readtext()` across threads.
`ONEDNN_PRIMITIVE_CACHE_CAPACITY=8` + `torch.set_num_threads(2)` cap memory. `gc.collect()` in
StatsTracker auto-save timer (every 5 min).

### Screen Navigation (navigation.py)
State machine via `navigate(target_screen, device)`:
1. `check_screen(device)` identifies current screen (matches all `SCREEN_TEMPLATES`, picks highest confidence)
2. Auto-dismisses popups (critical popups before screen check, soft popups after)
3. Routes to target screen via intermediate screens (e.g. MAP → ALLIANCE → WAR)
4. Verifies arrival with `_verify_screen()` (retries twice)
5. Recursion guard: max depth 3

**Soft popup dismiss exclusions** — `close_x.png` auto-dismiss is skipped on MAP and WAR screens.
On MAP, close_x appears in rally dialogs, AP windows, and search overlays. On WAR, close_x is part
of the rally detail/search panel. Dismissing it on either screen would break normal game flows.

**Unknown screen recovery** — `_recover_to_known_screen(device)` uses 4-phase escalation:
1. Template-based dismiss: close X, cancel button, back arrow (x2)
2. Android BACK key (`adb_keyevent(device, 4)`) — OS-level dismiss for popups without X
3. Center screen tap (540, 960) — dismiss transparent/click-through overlays
4. Nuclear: 3x BACK + center tap + 5s wait

`_last_unknown_info[device]` tracks the best template match when UNKNOWN is returned, enabling
"likely MAP" detection (70-79% score) for smarter recovery decisions.

### Adaptive Timing (vision.py + botlog.py)
`timed_wait(device, condition_fn, budget_s, label)`:
- Polls condition_fn every ~300ms until met or budget expires
- `StatsTracker.get_adaptive_budget()` can shorten budget based on P90 of observed transition times
- Config: min 8 samples, 80% success rate gate, 1.3x headroom, never below 40% of original budget
- Persists across sessions (loads from previous session stats file)

### Timed Action Decorator (botlog.py)
`@timed_action(action_name)` wraps game actions:
- Logs entry/exit with timing
- Records success/failure/duration to StatsTracker
- Saves failure screenshot on exception
- Expects `device` as first positional arg

### Troop System (troops.py)
**Counting** — Protocol fast path first (when `PROTOCOL_ENABLED`): `get_protocol_troops_home()` returns
home count from `GameState.lineups` (instant, ≤30s freshness). Falls through to pixel-based counting
on `None` or any error. Pixel path: checks cyan color `[107, 247, 255]` at known Y positions on MAP
screen. Returns 0-5.

**Status model** — `TroopStatus` dataclass with `TroopAction` enum (HOME, DEFENDING, OCCUPYING, MARCHING, RETURNING, STATIONING, GATHERING, RALLYING, BATTLING, ADVENTURING). `DeviceTroopSnapshot` holds full troop state with helpers like `home_count`, `deployed_count`, `soonest_free()`.

**Panel statuses** — Protocol fast path first (when `PROTOCOL_ENABLED`): `get_protocol_troop_snapshot()`
builds a `DeviceTroopSnapshot` from `LineupsNtf`/`NewLineupStateNtf` data with `seconds_remaining`
from `stateEndTs` (instant, ≤30s freshness). Falls through to icon template matching on `None` or error.
LineupState enum (extracted from game binary via Frida IL2CPP API):
0=ERR(home), 1=DEFENDER(home), 2=OUT_CITY→MARCHING, 3=CAMP→STATIONING, 4=RALLY→RALLYING,
5=REINFORCE→DEFENDING, 6=GATHERING→GATHERING, 7=TROOP_FIGHT→BATTLING, 8=RALLY_FIGHT→BATTLING,
9=RETURN→RETURNING, 10=BUILDING_BUILD→MARCHING, 11=BUILDING_OCCUPY→DEFENDING,
12=BUILDING_DEFEND→DEFENDING, 13=MINE_EXPLORE→ADVENTURING, 14=PICKUP→MARCHING,
15=SCORE_GATHERING→GATHERING. Unknown states default to MARCHING.
`DeviceTroopSnapshot.source` field: `"protocol"` or `"vision"` — shown on dashboard as indicator.

**Healing** — `heal_all(device)`: finds heal.png, taps through heal dialogs in a loop until no more heal buttons.

### Territory System (territory.py)
- 24x24 grid, squares are 42.5px
- Border color detection: sample pixels, match to `BORDER_COLORS` (yellow/green/red/blue) with tolerance
- Flag detection: red pixel analysis in square
- Adjacency check: only attack squares bordering own territory
- `MANUAL_ATTACK_SQUARES` / `MANUAL_IGNORE_SQUARES` override auto-detection
- `open_territory_manager(device)`: Tkinter window for visual square selection (click to cycle: none → attack → ignore)
- `diagnose_grid(device)`: diagnostic tool — screenshots all 576 squares, classifies each using the same
  `_get_border_color` + `_classify_square_team` pipeline as `attack_territory`, logs a 24-row character grid
  (Y/G/R/B/?/T), team counts, unknown BGR values with nearest-color distances, and saves annotated debug
  image to `debug/territory_diag_{device}.png` and structured JSON to
  `data/territory_diag_{device}_{timestamp}.json`. `sample_specific_squares` is retained as an alias for
  backward compatibility.

### Auto Occupy System (territory.py + runners.py)

**Game mechanics**: Territory has a 24x24 grid of towers. Each tower has a border color
(yellow/green/red/blue) showing ownership. To capture: teleport near an enemy tower, tap it,
attack/reinforce, deploy troops. Capture timer: 4h total, swings 2h each way.

**Tower states and actions**:
- Empty enemy tower → reinforce (occupy and start capture timer)
- Enemy-occupied tower → attack to clear, then reinforce
- Friendly tower on frontline → reinforce to stack defense

**Architecture**: `auto_occupy_loop(device, stop_check)` is the main loop. Per-device cooperative
stop via `stop_check` callback (from `threading.Event.is_set`). No global flags — the old
`config.auto_occupy_running` global was removed. `run_auto_occupy` in `runners.py` passes
`stop_event.is_set` directly.

**Target selection** (`scan_targets` + `_pick_target`):
1. **Priority 1**: Unflagged enemy squares adjacent to own territory (best — no one marching there)
2. **Priority 2**: Flagged enemy squares adjacent to own territory (fallback — someone else attacking)
3. **Priority 3**: Friendly frontline squares (reinforce when no enemy targets)
`MANUAL_ATTACK_SQUARES` replaces all auto-detected targets. `MANUAL_IGNORE_SQUARES` filters them.

**`attack_territory(device)`** — Navigates to TERRITORY screen, calls `scan_targets` to classify
all 576 squares, picks target via `_pick_target`, taps target square on grid (camera trick —
centers camera on tower). Returns `(row, col, action_type)` or `None`. Side effect: stores
target in `config.LAST_ATTACKED_SQUARE[device]`.

**Cycle flow**:
1. Check dead → revive if needed (picks new target after revive)
2. Navigate to MAP, heal, check troops home
3. `attack_territory` — scan grid, pick target, tap grid square
4. `teleport(device)` — camera trick positions us near tower
5. Death check after teleport
6. Navigate back to TERRITORY, re-tap target square (re-centers camera)
7. `_tap_tower_and_detect_menu` — tap tower at (540, 900), detect attack/reinforce button
8. `_do_depart` — tap action button, wait for depart, handle `depart_anyway.png` fallback
9. Wait for troops, repeat

**Error recovery**:
- **Death**: `_check_and_revive()` detects `dead.png`, taps to revive, waits for MAP (30s timeout)
- **Teleport fail**: 3 consecutive fails → tries different target. Counter resets on success.
- **Menu fail**: `_tap_tower_and_detect_menu` returns None → saves failure screenshot, skips cycle
- **Nav fail**: BACK key recovery, retry once, skip cycle on second failure
- **Depart fail**: heal first if auto_heal on, then `depart_anyway.png` fallback
- **Exception**: caught, logged, failure screenshot saved, continues loop

**Status messages**: "Checking Troops...", "Scanning Territory...", "Targeting (R,C)...",
"Teleporting...", "Attacking Tower...", "Reinforcing Tower...", "Deploying...",
"Reviving...", "Waiting for Troops...", "No Targets..."

### Territory Coordinate Scanner (territory.py)
Maps grid squares to world coordinates via `scan_territory_coordinates(device)` (clicks each square,
OCR-reads coordinates, saves to `data/territory_coordinates.json`). `scan_test_squares(device)` scans
only 4 corners for calibration.

### Rally Owner Blacklist (actions/rallies.py)
- **UI-based OCR disabled** — owner name OCR was too slow (500-2000ms per rally candidate),
  causing multi-device contention. The blacklist infrastructure remains but is only populated
  via the protocol path when `PROTOCOL_ENABLED` (uses `rally.troops[0].name` from protocol data).
- `_ocr_rally_owner()` and `_ocr_error_banner()` still exist but are not called during join flow.
- 30-minute expiry, reset on auto-quest start. Per-device, session-scoped.

### Quest Dispatch (actions/quests.py)
**Quest classification**: `_classify_quest_text(text)` maps OCR quest names to `QuestType` using keyword
matching. Priority order: "titan" → TITAN, "evil"/"guard" → EVIL_GUARD, "pvp"/"attack"/"enemy" → PVP,
"gather" → GATHER, "occupy"/"fortress" → FORTRESS, "tower" → TOWER. Order matters — "Defeat Titans"
matches TITAN (not PVP) because "titan" is checked first. "defeat" was intentionally removed from PVP
keywords to prevent false positives from event quests (e.g. "Defeat Frost Giants").

**Dispatch priority chain**: PVP attack → Tower quest → EG/Titan rallies → pending rally wait → gather gold.
PVP and Tower run first because they're quick single-troop dispatches (no AP, no waiting).
PVP dispatches a troop then continues to other quests while it marches (non-blocking).
Gold gathering is blocked while titan/EG rallies are in-flight (pending). The bot waits for
rally completion instead of deploying gather troops, preserving troop availability for retries.

**Marker error suppression**: `_marker_errors = {}` (`{device: set of error strings}`) permanently
suppresses a quest type when a marker error is detected (duplicate markers, wrong tower type).
Checked at the top of `_attack_pvp_tower()` and `_run_tower_quest()` — if an error exists for the
device, the function skips silently (already warned once, don't spam). Cleared by
`reset_quest_tracking()` (called on auto quest start). Error statuses (e.g. `"ERROR: Duplicate
Enemy Markers!"`) remain visible until the user fixes markers and restarts auto quest.

**Gold mining gates**: Two guards prevent premature gold mining:
1. **PVP gate**: Skips gold if PVP quest available but not yet dispatched (not on cooldown).
2. **Pending rally gate**: Blocks gold while titan/EG rallies are in-flight.

**Stray troop recovery**: Runs at start of each `check_quests` cycle before quest OCR.
Recalls stray STATIONING troops (stuck EG rally). Stray DEFENDING troops are handled by
`_run_tower_quest` after quest OCR (needs to know if a tower quest is active).

**EG troop gate**: `_eg_troops_available(device)` requires 2 troops not gathering or defending.
Falls back to `troops_avail() >= 2` if no snapshot.

**Tower/fortress quest**: `_navigate_to_tower()` uses Friend tab + `find_all_matches("friend_marker.png")`
for marker counting. `occupy_tower()` detects wrong tower type (attack vs reinforce button) and
sets `_marker_errors`. `recall_tower_troop()` uses verified multi-step recall with panel-status
confirmation (2 approaches: panel icon → friend marker fallback). `_is_troop_defending_relaxed()`
extends snapshot freshness to 120s (vs 30s default) since quest OCR takes 60+ seconds.
**Fortress recall policy**: troop stays defending as long as fortress/tower quest rows are visible
on screen (even if completed). Only recalls when quests disappear entirely (quest reset/new day).
**Tower recall policy**: when no tower/fortress quest is on screen, any defending troop is recalled
unconditionally — `tower_quest_enabled` only controls deployment, not recall.
**Quest target overrides**: Titan ≥15, EG=3, Fortress=1800, PVP=500M, Gather=1M — OCR right-of-slash
is unreliable so these are hardcoded caps.

**PVP attack**: `_attack_pvp_tower()` uses `target()` (Enemy tab + marker counting) to navigate
to enemy tower. Checks button type (attack=correct, reinforce=wrong). Single march completes 500M
quest. 10-min cooldown (`_PVP_COOLDOWN_S`). Troop check runs **after** `target()` (needs MAP pixels).

### Titan Search Retry (actions/titans.py)
`rally_titan` searches for the titan, which centers the map on it, then blind-taps (540, 900)
to select it. If the titan walks off-center before the tap lands, the confirm popup never appears
and depart times out. The search → center-tap → depart-poll sequence is wrapped in a retry loop
(`_MAX_TITAN_SEARCH_ATTEMPTS = 3`). On miss, saves a debug screenshot, navigates back to MAP to
clear stale UI, then re-opens the rally menu and re-searches — re-centering the camera on the
titan's current position.

### AP Restoration (actions/titans.py + config.py)
Order: free restores → potions (small→large) → gems.
Controlled by `AP_USE_FREE`, `AP_USE_POTIONS`, `AP_ALLOW_LARGE_POTIONS`, `AP_USE_GEMS`, `AP_GEM_LIMIT`.

**Architecture**: The restoration logic is in `_restore_ap_from_open_menu(device, needed)` which assumes
the AP Recovery menu is already visible. Returns `(success, current_ap)`. `restore_ap()` wraps it with
menu navigation (MAP → search → AP button) and double-close. `_close_ap_menu(device, double_close=True)`
handles both cases: `True` for bot-opened menus (search menu behind), `False` for game-opened popups.

**Game-triggered AP popup**: When the game opens the AP Recovery popup (e.g. after tapping depart with
insufficient AP), `_handle_ap_popup(device, needed)` in `evil_guard.py` detects `apwindow.png`, restores
AP via `_restore_ap_from_open_menu`, and single-closes the popup. Used in `click_depart_with_fallback()`
(primary) and `poll_troop_ready()` (safety net).

### Settings Persistence (settings.py)
`settings.json` stores user preferences (auto-heal, AP options, intervals, territory teams,
`remote_access` toggle, `device_settings` per-device overrides). Loaded on startup, saved on
quit/restart. `DEFAULTS` dict provides fallback values. `DEVICE_OVERRIDABLE_KEYS` defines which
settings can be overridden per device (includes `protocol_enabled`). Shared by both `main.py` (GUI) and
`web/dashboard.py` (Flask). `updater.py` preserves `settings.json` across auto-updates
(`PRESERVE_FILES`).

### Web Dashboard (web/dashboard.py)
Mobile-friendly Flask app for remote control from any browser. `run_web.py` is now the primary
entry point — launches the Flask server in a daemon thread with pywebview providing a native
window (falls back to opening in the system browser if pywebview is unavailable). A phone access
banner displays the LAN URL for mobile remote control.

**Enable**: access `http://<your-ip>:8080` (started automatically by `run_web.py`).

**Architecture**:
- `create_app()` factory returns Flask app; started via werkzeug `make_server` in `run_web.py`
- Imports shared task runners from `runners.py` and settings from `settings.py` — no duplication
- `AUTO_RUNNERS` dict maps auto-mode keys → runner lambdas
- `TASK_FUNCTIONS` dict maps one-shot action names → callable functions
- Device list cached for 15s (`_DEVICE_CACHE_TTL`) to avoid spamming ADB on every poll
- CSS cache busting: `style.css?v=N` in `base.html` — bump on every CSS change
- Device ID validation: `/tasks/start` rejects device IDs not in `get_devices()` whitelist
- XSS prevention: dashboard JS uses `textContent` / DOM creation (no `innerHTML` for dynamic data)
- Relay auto-config: index route calls `get_relay_config()` to show remote URL when relay is active
- Thread safety: `_task_start_lock` prevents TOCTOU race on `running_tasks` during concurrent task starts
- Device ID validation: per-device settings routes (`/settings/device/<id>`) reject unknown device IDs

**Pages**: Dashboard (`/`), Settings (`/settings`), Guide (`/guide`), Debug (`/debug`), Logs (`/logs`), Territory Grid (`/territory`), Chat (`/chat`), Device View (`/d/<dhash>?token=...`)

**API**: See `web/dashboard.py` for full route list. Key endpoints: `/api/status` (polled 3s),
`/tasks/start|stop|stop-all`, `/api/stream` (MJPEG), `/api/chat` (chat messages),
`/api/protocol-status` (per-device protocol state), `/d/<dhash>` (per-device scoped routes).
Per-device settings: `GET|POST /settings/device/<id>`.

### Relay Tunnel (tunnel.py + relay/)
WebSocket relay for remote access — lets users control 9Bot from outside the LAN.

**Zero-config**: Relay auto-configures from the license key. Bot name is `SHA256(license_key)[:10]`.
`get_relay_config(settings)` in `startup.py` returns `(relay_url, relay_secret, bot_name)` or `None`.

**Architecture**: `tunnel.py` opens `wss://` to relay (`1453.life`). Browser hits
`https://1453.life/bot_name/` → nginx → relay forwards to `localhost:8080`. Reconnects with
exponential backoff (5s→60s cap). Supports MJPEG streaming via `stream_start/chunk/end` protocol.

**Settings**: `remote_access` boolean (default `True`). URL/secret are base64-obfuscated in `startup.py`.
**Status**: `tunnel_status()` returns `"disabled"` / `"connecting"` / `"connected"` / `"disconnected"`.

### Bug Report Auto-Upload (startup.py + relay/relay_server.py)
Opt-in periodic upload of bug report ZIPs via direct HTTPS POST to `https://1453.life/_upload`.
Settings: `auto_upload_logs` (bool), `upload_interval_hours` (int, 1-168). Key functions:
`upload_bug_report()`, `start_manual_upload()`, `get_upload_progress()` (phases: idle→zipping→uploading→done).
Server: `POST /_upload?bot={name}` (500MB limit, keeps last 10), `GET /_admin?secret=XXX` for admin.

### Training Data Collector (training.py)
Opt-in JSONL logger for template match, OCR, and screen detection decisions. Selective image
capture for near-misses (confidence 0.65-0.79), low-confidence OCR (avg < 0.7), unknown screens,
and region drift. Setting: `collect_training_data` (default `False`).

- JSONL: one file per session (`training_data/td_{timestamp}.jsonl`), capped at 10 files
- Images: JPEG to `training_data/images/`, rolling cap of 200
- Hooked into `find_image()`, `read_text()`, `check_screen()`
- Included in bug report ZIPs, cleared after export
- Stats shown on `/debug` page, toggle on `/settings` page
- `configure(enabled)`, `shutdown()` called from `startup.py`

### Protocol Interception (protocol/ + startup.py)
Opt-in Frida Gadget integration that intercepts the game's network protocol for instant data reads.
Per-device setting: `protocol_enabled` (default `False`), toggled from `/debug` page per device. Part of `DEVICE_OVERRIDABLE_KEYS` in `settings.py`.

**APK Patching** (`patch_apk.py`): Injects Frida Gadget into the game APK. Pulls split APKs from
a connected device (`--device`), patches the arm64 split via LIEF (`add_library("libfrida-gadget.so")`),
signs all 3 splits, and installs. Signing uses pure Python (v1/JAR signing via `cryptography` library)
— no Java/SDK required. Keystore cached as PEM files. Only needed once per game update; subsequent
reinstalls reuse the same keystore (no tutorial replay).

**Hook Script** (`frida_hook.js`): Runs inside the game process via Frida Gadget. Dynamically
resolves `TFW.NetMsgData.FromByte/MakeByte` addresses at runtime using IL2CPP's metadata API
(`il2cpp_class_from_name` + `il2cpp_class_get_methods`). No hardcoded RVAs or LIEF delta — works
across game versions and LIEF versions. Frida's `mod.findExportByName()` (instance method, not
`Module.findExportByName` static — the static version doesn't exist in this Frida gadget version).

**Data Pipeline**: Frida hooks → `send()` → Python `_on_frida_message()` → `decode_frame()` →
`ProtobufDecoder.decode()` (schema from `proto_field_map.json`) → `MESSAGE_CLASSES[name].from_dict()`
→ `MessageRouter.route()` → `EventBus.emit()` → `GameState` handlers. Wire-level msg_id resolved
via `wire_registry.json` (BKDR hash lookup, 1000+ message types).

**Data Files**: `wire_registry.json` (msg_id → class name), `proto_field_map.json` (per-message
field schemas with names, types, wire types), `registry.json` (internal cspb-prefixed registry).

**Lifecycle** (startup.py): Per-device model — each device gets its own EventBus + GameState +
InterceptorThread (daemon). `start_protocol_for_device(device)` creates the stack for one device,
`stop_protocol_for_device(device)` tears it down. Active devices tracked in
`config.PROTOCOL_ACTIVE_DEVICES` set. Called from `apply_settings()` and per-device settings routes.
Import wrapped in try/except ImportError — if protocol package or frida not installed, silently skips.
Port allocation: Frida Gadget listens on port 27042 inside each emulator. Each device gets a unique
host port via `adb forward` (base 27042, incrementing per device). Set up by `_setup_frida_forward()`
in startup.py.

**AP fast path** (vision.py): `read_ap()` calls `get_protocol_ap()` first when the device is in
`PROTOCOL_ACTIVE_DEVICES`. Returns
`(current, max)` from `GameState.ap` if fresh (≤10s), else `None` → falls through to OCR.
Entire block wrapped in try/except — any failure silently falls through.

**Troop fast paths** (troops.py): `troops_avail()` calls `get_protocol_troops_home()` first when
the device is in `PROTOCOL_ACTIVE_DEVICES` — returns home count from `GameState.lineups` if fresh (≤30s), else `None` → falls through
to pixel counting. `read_panel_statuses()` calls `get_protocol_troop_snapshot()` — builds a full
`DeviceTroopSnapshot` from lineup data with `seconds_remaining` timers, else `None` → falls through
to icon template matching. Both wrapped in try/except, same zero-risk pattern as AP.

**Rally fast path** (actions/rallies.py): `join_rally()` calls `get_protocol_rallies()` first when
the device is in `PROTOCOL_ACTIVE_DEVICES` — returns list of `Rally` objects if fresh (≤30s), `[]` for confirmed zero rallies, else
`None` → falls through to UI. When `[]` or all rallies are full/marching/blacklisted, bails out
immediately (saves 20-30s of war screen navigation + scrolling). **NPC/player type filtering**:
`_NPC_RALLY_TYPES` (titan, eg, groot) match `rally.npcCity`, `_PLAYER_RALLY_TYPES` (castle, pass,
tower) match `rally.playerCity`. Derives `want_npc`/`want_player` from the requested `rally_types`
so only relevant rallies are considered (e.g. castle rallies are ignored when requesting titan/eg).
`_rally_matches_target()` helper performs the check. NPC `cfgID` values are logged at debug level
for future fine-grained type mapping. Does NOT skip the UI join flow when joinable rallies exist —
only provides early bail-out. Same try/except pattern as AP/troops.

**Heartbeat keepalive** (game_state.py): `_on_heartbeat()` (every ~10s) touches "lineups" and
"rallies" freshness categories when data exists, preventing stale-data fallback when no
state-change messages arrive (e.g. troops sitting idle for minutes).

**Chat mirroring** (dashboard.py): Devices without protocol can view chat messages from a
protocol-active device (excludes PRIVATE channel for privacy). `chat_mirror` setting (default
True). Mini chat feed on device cards + full chat modal with channel tabs.

**Safety**: Zero-risk to existing users. Protocol off by default, one set-membership check when disabled.
InterceptorThread auto-reconnects (10s interval) if gadget not running. Stale data returns
None, triggering vision fallback. No existing code paths change when setting is off.

### Per-Device Access Control (startup.py + web/dashboard.py)
Token-based shareable URLs: `https://1453.life/{bot_name}/d/{device_hash}?token={token}`
- `device_hash` = `SHA256(device_id)[:8]`, tokens = `SHA256(license_key + ":" + device_id)[:16]`
- Access levels: `"full"` (start/stop tasks), `"readonly"` (view only), `None` (403)
- `validate_device_token(device_id, token)` returns level; `require_full_access` decorator on write routes
- Per-device settings: `config.get_device_config(device, key)` checks override, falls back to global
- `protocol_enabled` is a device-overridable setting — each device can independently enable/disable protocol interception

### Device Status System (config.py + all runners)
`config.DEVICE_STATUS[device]` — current status string. Set via `set_device_status()`, cleared via
`clear_device_status()`.

**Conventions**: Title Case, expanded abbreviations ("Evil Guard" not "EG"), trailing ellipsis for
active states, `"Idle"` as default.

**Status text colors** (dashboard JS): Cyan (active), Red ("Stopping"), Amber ("Waiting"),
Gray ("Navigating"), Default gray (idle).

**Stopping**: `stop_task()` immediately sets `"Stopping {label}..."`. `force_stop_all()` kills
threads and clears all statuses. Dashboard JS `_stoppingModes` prevents toggle flicker during shutdown.

## Tests

```bash
py -m pytest          # run all ~989 tests
py -m pytest -x       # stop on first failure
py -m pytest -k name  # filter by test name
```

No fixtures require a running emulator — all use mocked ADB/vision.

### Test Conventions
- Fixtures in `conftest.py`: `mock_device` ("127.0.0.1:9999"), `mock_device_b` ("127.0.0.1:8888")
- `reset_quest_state` autouse fixture calls `reset_quest_tracking()` + `reset_rally_blacklist()` before each test
- All ADB calls and screenshots are mocked via `unittest.mock.patch`
- Mock patches target the submodule where the function is used (e.g. `actions.farming.navigate`, not `actions.navigate`)
- Tests import directly from submodules (e.g. `from actions.quests import check_quests`)
- Test names: `test_<function>_<scenario>` (e.g. `test_find_image_returns_none_below_threshold`)
- Use `@pytest.mark.parametrize` for related test cases that vary only by input/expected values

## Diagnostic Analysis

Run `/analyze` to process all session data (stats, logs, debug screenshots), document
findings, and clean up. Findings are stored in `.claude/analysis/` (tracked in git).

See `.claude/analysis/MEMORY.md` for the index of known issues and patterns.
When investigating bugs or making changes, check the analysis files first for prior data.

## Git Workflow

Current version: see `version.txt`

### Branches

| Branch | Purpose | Rules |
|--------|---------|-------|
| `master` | Release branch — tagged releases only | Never commit directly. Only receives merges from `dev`. |
| `dev` | Integration branch — always working | Day-to-day commits go here. Must stay buildable. |
| `feature/*` | Experimental/risky work | Optional. Use when a change might be thrown away. Merge or delete when done. |

### Commits

- **One logical change per commit.** Don't bundle unrelated fixes.
- **Conventional prefix:** `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `revert:`
- **Meaningful messages** that explain *why*, not just *what*:
  - Good: `fix: tower recall failing due to stale detail_button position`
  - Bad: `fix: stuff`

### Releasing to master

```bash
# 1. On dev: bump version and commit
#    Edit version.txt → X.Y.Z
git add version.txt
git commit -m "chore: bump version to X.Y.Z"

# 2. Merge into master with a release message
git checkout master
git merge --no-ff dev -m "release: vX.Y.Z"

# 3. Tag and push
git tag vX.Y.Z
git push origin master --tags

# 4. Return to dev
git checkout dev
```

Master log should read like a changelog — one `release: vX.Y.Z` entry per release.

### Rules

- **No hotfixes on master.** Fix on `dev`, then release.
- **No `Merge branch 'dev'` messages.** Always use `--no-ff` with an explicit `release:` message.
- **Delete stale branches** after merging. Don't let old `feature/*` branches accumulate.
- **Don't rewrite published history.** No force-push to `master` or `dev`.

### Claude Enforcement (MANDATORY)

Claude MUST actively enforce this workflow. The user works on many things in parallel and
needs Claude to be the gatekeeper.

**Before any commit:**
- If the working tree has unrelated staged changes, STOP and ask the user to separate them.
- If a commit message bundles multiple unrelated changes, refuse and suggest splitting it.
- If on `master`, refuse the commit entirely — redirect to `dev`.

**When the user says "release", "push to master", "merge to master", or similar:**
1. Run `git status` and `git log --oneline dev` to audit what's on dev.
2. Present a clear list of ALL uncommitted changes AND all commits since the last release.
3. Ask the user to explicitly confirm which changes are release-ready vs. not ready.
4. If anything is WIP/experimental (e.g. protocol features, phantom clash), flag it and
   ask whether it should be included. Do NOT silently merge everything.
5. If WIP changes need to be excluded, help the user isolate them (stash, branch, revert)
   BEFORE merging to master.
6. Only proceed with the release steps after the user has approved the exact scope.

**During normal work:**
- If the user asks to commit a large batch of changes touching unrelated areas, push back
  and suggest breaking it into separate commits.
- If unstaged/uncommitted changes are piling up across many files, proactively remind the
  user to commit or stash before context is lost.
- When starting a new session, check `git status` — if there are uncommitted changes,
  surface them early so nothing gets forgotten.

