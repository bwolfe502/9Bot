# 9Bot Master Research File

Comprehensive reference of all research, analysis, testing, and development findings.
Consolidates data from GitHub PRs/releases, analysis sessions, memory files, and live testing.

---

## Table of Contents
1. [Protocol System](#protocol-system)
2. [Message Coverage](#message-coverage)
3. [Template Matching](#template-matching)
4. [OCR System](#ocr-system)
5. [Action Success Rates](#action-success-rates)
6. [Timing & Performance](#timing--performance)
7. [Memory & Stability](#memory--stability)
8. [Territory System](#territory-system)
9. [Auto Reinforce Ally](#auto-reinforce-ally)
10. [Evil Guard Features](#evil-guard-features)
11. [Rally System](#rally-system)
12. [Architecture Findings](#architecture-findings)
13. [Release History](#release-history)
14. [PR History](#pr-history)
15. [Known Issues & Blockers](#known-issues--blockers)
16. [Undocumented Findings (2026-03-07)](#undocumented-findings-2026-03-07)

---

## Protocol System

### Overview
- Frida Gadget hooks into `TFW.NetMsgData.FromByte/MakeByte` via IL2CPP runtime API
- No hardcoded RVAs — works across game versions
- Pipeline: Frida hooks -> `decode_frame()` -> `ProtobufDecoder` -> `MESSAGE_CLASSES` -> `MessageRouter` -> `EventBus` -> `GameState`
- Per-device model: each device gets EventBus + GameState + InterceptorThread
- LZ4-block decompression handled for CompressedMessage

### APK Datamine (2026-03-05)
- **Source**: APK static analysis + runtime capture
- **4,169 wire IDs** mapped in `wire_registry.json`
- **3,770 protobuf field definitions** in `proto_field_map.json`
- **Runtime stats**: 7,003 messages/session, 1,874 sent, 303 unique types
- **Currently intercepted**: ~35 message types

### Fast Paths (protocol -> vision fallback)
- **AP**: `read_ap()` -> `get_protocol_ap(device)` (<=10s freshness) -> OCR fallback
- **Troops**: `troops_avail()` -> `get_protocol_troops_home(device)` (<=30s) -> pixel fallback
- **Panel**: `read_panel_statuses()` -> `get_protocol_troop_snapshot(device)` (<=30s) -> template fallback
- **Rally**: `join_rally()` -> `get_protocol_rallies(device)` (<=30s) -> early bail-out only
- **Territory Grid**: KvkTerritoryInfoAck (full grid on login) + KvkTerritoryInfoNtf (real-time)
- **Home Castle Position**: CityUpdateNtf -> `GameState._home_coord` (2026-03-07)

### Heartbeat Keepalive
- `_on_heartbeat()` every ~10s touches freshness to prevent stale fallback

### Headless Action Injection (2026-03-05)
- **Objective**: Validate headless protocol actions for rally joins
- **Tested**: NewTroopReq force-send via heartbeat trigger
- **Result**: Static payload replay insufficient — server validates context-dependent fields
- **Feasibility**: Still viable but requires state-aware generation, not replay
- **Recommended**: Re-enable guardrails, state-aware construction, hard post-condition validation

### Protocol Stability
- Frida exponential backoff on disconnect + version mismatch detection
- Watchdog auto-reconnect
- ADB forward auto-reconnect for port mapping
- Clear freshness on disconnect to force vision fallback

---

## Message Coverage

### Currently Handled (~35 types)
HeartBeat, Rally (create/update/end), Quest, AP/Assets, Lineups, Territory (Info/Ntf),
Shield, Chat, UnionEntities, DelUnionEntities, Entities, DelEntities, PositionNtf,
BattleResult, Buff, CityUpdate, MarchingLineList, NewLineupState, and more.

### High-ROI Unhandled Messages
| Message | Purpose | Impact |
|---------|---------|--------|
| WildMapViewAck | Titan/EG positions on map | Skip blind search, instant targeting |
| RedPointNtf | Quest/event completion flags | Skip OCR quest checking |
| IntelligencesNtf | Attack notifications | Ally defense automation |
| UnionMemberListAck | Full alliance roster | Complete member list without viewport |

### Message Family Statistics
- **Largest families**: Union (200+), Kvk (150+), Battle (100+)
- **Update compatibility**: Wire IDs stable across game versions (BKDR hash based)

---

## Template Matching

### Confidence Issues
| Template | Confidence | Issue |
|----------|-----------|-------|
| search.png | 66% | Extreme fragility, 1% headroom above 0.65 threshold |
| defending.png | 52-60% on emulator-5554 | Device-specific failure (100% on :5625) |
| mithril_return.png | 0% on emulator-5554 | Device-specific (100% on :5625) |
| stationed.png | N/A | Region too narrow (103 misses/session) |

### Screen Detection Stability
| Screen | Reliability |
|--------|------------|
| map_screen | 100% |
| alliance_screen | 100% |
| aq_screen | 97.5% |
| war_screen | 96.1% |
| aq vs war gap | 6-9 points (tight) |

### Template Rename (2026-03-07)
- `reinforce_button.png` -> `territory_reinforce.png` (blue, territory towers)
- `alliance_reinforce.png` added (yellow, ally castle panel)

### Key Patterns
- `IMAGE_REGIONS` dict constrains search area per template
- Fallback to full-screen if region miss
- `StatsTracker` auto-narrows regions after 3+ hits
- `TAP_OFFSETS` for templates needing offset taps
- `depart_anyway.png` fallback at 0.65 threshold across all depart flows

---

## OCR System

### PaddleOCR (replaced EasyOCR in v2.2.0)
- **Speed**: 15-60x faster than EasyOCR
- **Memory**: `_ocr_infer_lock` serializes readtext(), capped threads/cache
- **Gotcha**: `import paddle` resets root logger to WARNING — must restore after import

### OCR Reliability
- **AP reads**: 21 failures across sessions, graceful fallback to None
- **Quest OCR**: Working well, parser corrects errors ("o/1" -> 0/1)
- **Rally owner OCR**: Disabled (too slow at 500-2000ms), only via protocol now
- **Coordinate OCR**: Occasionally misreads (e.g. "738 3" with space), protocol fallback added

### macOS Apple Vision
- ~30ms/call (native)
- Known issue: paren drop on titan counters (fixed)

---

## Action Success Rates

### Aggregate Stats (Sessions 8-12, 2026-03-02)
| Action | Success Rate | Notes |
|--------|-------------|-------|
| join_rally | 0% (0/215) | CRITICAL — all failures |
| recall_tower | 21% (3/14) | defending.png device-specific |
| rally_eg | 40% (10/25) | P6 dialog timing |
| rally_titan | 72% (141/197) | Search retry loop helps |
| check_quests | 92% | Robust |
| heal_all | 98% | Near perfect |
| navigate | 99% | Reliable |
| Others | 95-100% | Stable |

### Failure Cascades
- Morteza rally joins (systematic opponent detection failure)
- WAR screen stuck (close_x exclusion prevents dismiss)
- Tower recall loop (defending.png template mismatch)
- P6 evil guard timing (dialog transitions)

---

## Timing & Performance

### ADB Performance
| Device | Screenshot Time |
|--------|----------------|
| emulator-5554 | 0.270s (fastest) |
| TCP devices | 0.299-0.341s |

### Problem Transitions (0% budget met)
- titan_on_map_select
- jr_detail_load
- eg_p6 dialogs

### Low-Met Transitions (<70%)
- titan_search_menu: 52-67%
- eg_search_menu: 12%
- Recovery flows: variable

### Budget Overruns
- verify_aq_screen: 2.3s vs 2.0s budget
- nav_map_to_alliance: 2.1s vs 2.0s budget

### Adaptive Timing
- `timed_wait` polls every ~300ms
- `StatsTracker.get_adaptive_budget()` shortens based on P90
- Min 8 samples, 80% success gate, 1.3x headroom, never below 40% of original

---

## Memory & Stability

### Memory Spikes
- **Peak**: +5658 MB during concurrent 3-device check_quests OCR
- **RSS**: 6+ GB with 3+ devices (no leak, high absolute)
- **Mitigations**: `gc.collect()` in StatsTracker timer (5 min), thread serialization

### Tunnel Stability
- 18+ "no data in 90s" reconnections per session
- Missing keepalive pings identified and fixed (v2.3.0)
- Exponential backoff: 5s -> 60s cap

### Bot Restarts
- Session 12: 3 restarts in 43 min (OOM)
- Auto-reconnect for ADB, Frida, tunnel all implemented

---

## Territory System

### Grid
- 24x24 grid, 42.5px squares
- Border color detection with tolerance matching
- Flag detection via red pixel analysis
- Pass zone model: 8 mountain passes gate access to map areas

### Frontline Occupy (replaced auto-occupy in v2.1.1)
- `frontline_occupy_loop` as main loop
- Post-teleport recenter via `navigate_to_coord` (key fix, 2026-03-05)
- Target priority: empty enemy > flagged enemy > friendly frontline
- Protocol grid data: KvkTerritoryInfoAck (login) + Ntf (real-time)
- Cache: `data/territory_grid_{device_hash}.json`
- Death recovery, teleport retry (3 fails -> new target)
- **Settings**: `frontline_occupy_action` (attack/reinforce), `frontline_enemy_teams`

### Territory Coordinate Scanner
- `scan_territory_coordinates`: clicks each square, OCR reads coords
- Saves to `data/territory_coordinates.json`

---

## Auto Reinforce Ally

### Architecture
- Protocol-driven: `EVT_ALLY_CITY_SPOTTED` + `EVT_ALLY_UNDER_ATTACK`
- Runner: `run_auto_reinforce_ally` with priority queue
- 30-minute cooldown per entity ID prevents repeat sends
- Troop reserve: keep 1 free for normal, use all for attacks

### Entity Tracking (2026-03-07 fixes)
- `UnionEntitiesNtf`: active/online alliance members anywhere on map
- `EntitiesNtf`: entities in viewport (including allies)
- **Fix**: removed `is_new` gate — every protocol update is fresh data, always extract coords and emit events
- **Fix**: `CityUpdateNtf` tracks own castle position separately from alliance entities
- `_home_coord` on GameState: (X, Z) raw coords from CityUpdateNtf
- `_check_home_moved`: detects teleports via CityUpdateNtf (not ally entities)

### Nearby Ally Scanning (2026-03-07)
- After 10s idle, `_scan_nearby_allies` queues unshielded allies within max_reinforce_distance
- Distance filter uses raw protocol coords (divide by 1000 for display units)
- Skips: shielded allies, own castle (dist < 2), already reinforced (on cooldown)
- Power-priority queue: higher power allies reinforced first

### Castle Tap Pattern (2026-03-07 fix)
- Single `adb_swipe(540, 960, 540, 960, duration_ms=150)` — same as shield click
- Wait 1.5s, check for `alliance_reinforce.png` (yellow button)
- 4 retries before failure
- Tap reinforce via `tap_image` at detected center
- Previous 9-point grid + `detail_button.png` check removed

### Shield System
- `_apply_shield_via_ui`: swipe-tap at (200, 965), check for `checked.png` + `succesful_shield.png`
- `ensure_shield`: verify/apply before reinforce dispatch
- `GetShieldInfoAck` handler: captures shield end timestamp from protocol
- Shield expiry persisted to device settings

### Persistence (2026-03-07 fixes)
- `shield_expiry`, `reinforced_allies`, `active_reinforce_coords` added to:
  - `DEVICE_OVERRIDABLE_KEYS` in settings.py
  - `_SETTINGS_TO_CONFIG` with `None` global (device-only state)
- `get_device_config` handles `None` global names (returns None instead of KeyError)
- **Rule**: Any new persisted device key MUST be added to both lists

### Home Coordinate Sources (priority order)
1. Protocol `CityUpdateNtf` (instant, auto-updates on teleport)
2. OCR coordinate capture (fallback on first run)
3. Saved device settings `home_x`, `home_z` (last resort)

### Distance Calculation
- Uses raw protocol coords / 1000 for display-unit distances
- `max_reinforce_distance` default: 80 (confirmed by user testing)
- Own castle filtered at dist < 2

---

## Evil Guard Features

### EG Skip (2026-03-06, untested)
- Entity-based claimed detection via EVIL entities (type 27)
- Search cycling: up to 5 attempts to find unclaimed EG
- Protocol messages: PLAYER_TROOP (type 1) with BattleInfo/AttackInfo

### Async March (2026-03-06, untested)
- Yields during long marches (>30s)
- `rally_eg` returns "marching", saves state in `_eg_rally_state[device]`
- `check_quests` resumes via `rally_eg_resume()` when arrival time passes
- Falls through to other tasks (PVP, tower, gather) while marching
- **Status**: Implemented, awaiting live test on Kodashon

---

## Rally System

### Rally Join (CRITICAL: 0% success)
- 215 consecutive failures across sessions 8-12
- Root cause investigation ongoing
- Protocol fast path provides early bail-out but doesn't skip UI
- Rally owner OCR disabled (too slow), only populated via protocol

### Rally Owner Blacklist
- 30-minute expiry, reset on auto-quest start
- Per-device, session-scoped
- Only populated via protocol path (`rally.troops[0].name`)

### Titan Rally
- 72% success rate
- Search retry loop: `_MAX_TITAN_SEARCH_ATTEMPTS = 3`
- Re-searches on blind-tap miss (titan walked off center)

---

## Architecture Findings

### Module Analysis (16,400+ lines of analysis)
- **Dependency graph**: Verified acyclic (clean unidirectional imports)
- **45+ mutable globals** in config.py (all session-scoped)
- **Threading model**: Main, Flask, task workers, OCR warmup, tunnel, protocol interceptor, stats auto-save
- **Synchronization**: Quest/rally tracking state unprotected (MODERATE risk)

### Test Suite
- **801-1106 tests** (varies by version)
- **Gaps**: farming.py, titans.py, license.py
- **Conventions**: fixtures in conftest.py, mock ADB/vision, `test_<function>_<scenario>` naming

### Web Dashboard
- 25+ JSON endpoints
- Token auth for per-device access
- MJPEG streaming for live view
- CSS cache busting, XSS prevention (textContent only)

### Key Patterns
- Protocol -> vision fallback on None or error (zero risk when disabled)
- Cooperative cancellation via `stop_check` callback
- Per-device lock prevents concurrent tasks
- Force-stop via `PyThreadState_SetAsyncExc` (SystemExit injection)
- Smart idle status reads troop snapshot
- ADB auto-reconnect on timeout

---

## Release History

| Version | Date | Key Changes |
|---------|------|-------------|
| v2.3.0 | 2026-03-07 | Consolidation from v2.1.1, protocol enhancements, tunnel fixes, 1095 tests |
| v2.2.0 | 2026-03-06 | PaddleOCR replacement (15-60x faster), Python 3.13 auto-install |
| v2.1.1 | 2026-03-05 | Territory rewrite, auto reinforce ally, cloud hosting, chat translation |
| v2.1.0 | 2026-03-03 | Emulator control, per-device protocol, chat mirroring, 989 tests |
| v2.0.10 | 2026-03-03 | Rally joining 30-50% faster (removed OCR overhead) |
| v2.0.9 | 2026-03-03 | Hotfix: rally join timing |
| v2.0.8 | 2026-03-03 | Protocol troop timers, depart anyway fallback, bug report upload |
| v2.0.7 | 2026-03-02 | Training data collector, protocol interception, pure Python APK signing |
| v2.0.6 | 2026-03-02 | Fix RECORD dialog coordinates |
| v2.0.5 | 2026-03-02 | Fix RECORD dialog tabs |
| v2.0.4 | 2026-03-02 | Hotfixes: tabs, navigation, EG, tower, mithril |
| v2.0.3 | 2026-03-02 | Draft (superset of v2.0.4) |
| v2.0.2 | 2026-03-02 | Duplicate marker detection, restart button, 756 tests |
| v2.0.1 | 2026-03-01 | Hotfix: relay URL prefix rewriting |
| v2.0.0 | 2026-03-01 | Complete web dashboard rewrite, remote access, auto-update |

---

## PR History

| PR# | Title | Status | Key Changes |
|-----|-------|--------|-------------|
| #1 | Auto reinforce ally castles via protocol | Merged | reinforce_ally.py, entity tracking, event bus |
| #2 | Autoreinforce | Merged | Home capture, distance filter, power priority |
| #3 | Occupy frontline | Closed | Superseded by #8 |
| #4 | Protocol research docs | Merged | 7 documentation files cherry-picked |
| #5 | Game improvements | Closed | Superseded by #6 |
| #6 | Game improvements | Merged | PaddleOCR, restart fix, Python 3.13 setup |
| #7 | Occupy frontline | Closed | Superseded by #8 |
| #8 | Occupy frontline | Merged | Territory capture, protocol grid, settings fix |
| #9 | Restore frontline occupy settings UI | Merged | Attack/Reinforce toggles, enemy team checkboxes |

---

## Known Issues & Blockers

### CRITICAL
1. **join_rally 0%** (215 failures) — all rally joins failing, root cause unknown
2. **search.png 66%** — 1% headroom, extremely fragile template

### HIGH
3. **recall_tower 21%** — defending.png device-specific template failure
4. **Memory spikes** — +5658 MB on concurrent OCR, 6+ GB RSS with 3+ devices
5. **rally_eg 40%** — P6 dialog timing issues

### MEDIUM
6. **stationed.png region** — too narrow, 103 misses/session
7. **aq_screen vs war_screen** — only 6-9 point confidence gap
8. **Quest/rally state unprotected** — no thread locks (MODERATE race condition risk)

### LOW / MONITORING
9. **Tunnel idle disconnects** — fixed in v2.3.0, monitor
10. **EG async march** — implemented but untested
11. **IntelligencesNtf** — attack detection path unverified (needs real attack)

---

## Undocumented Findings (2026-03-07)

### Protocol Logging Fix
- **Root cause**: `import paddle` (PaddleOCR) resets `logging.getLogger().level` from DEBUG(10) to WARNING(30)
- **Impact**: ALL protocol handler output silenced — no INFO/DEBUG logs from game_state.py handlers
- **Fix**: Added `import logging` to vision.py + restore root logger to DEBUG after PaddleOCR init
- **Also fixed**: `botlog.py` duplicate handler check matched by filename, not just type (PaddleOCR adds its own RotatingFileHandler)

### Alliance Entity Data Behavior
- `UnionEntitiesNtf` sends **active/online** alliance members anywhere on map (not viewport-limited)
- `EntitiesNtf` sends entities in viewport only (including nearby allies)
- Game restart provides a small initial batch, not full roster
- Scrolling/panning triggers additional entity updates
- 113 unique alliance members detected in one session (730 total events)
- Members detected: global active members via UnionEntitiesNtf, viewport members via EntitiesNtf

### Entity Update Processing Fix
- **Bug**: `is_new` gate on entity handlers meant only first-seen entities got coords extracted and events emitted
- **Impact**: Panning over known allies silently replaced entity dicts without updating X/Z coords, no events emitted
- **Fix**: Always extract coords, always emit events regardless of `is_new` — every protocol update is fresh data
- **Side effect**: Events fire much more frequently now; runner cooldown/dedup handles this

### Device Settings Persistence Pattern
- New device-persisted keys MUST be added to:
  1. `DEVICE_OVERRIDABLE_KEYS` in settings.py
  2. `_SETTINGS_TO_CONFIG` in config.py (with `None` for device-only, no global)
- `validate_settings()` silently strips unknown keys on every save
- `get_device_config()` updated to return `None` for keys with no global variable
- Affected keys: `shield_expiry`, `reinforced_allies`, `active_reinforce_coords`

### CityUpdateNtf (Own Castle Tracking)
- **Message**: `CityUpdateNtf` with `cityId` (long) + `coord` (Coord: X, Z as int32)
- **Wire ID**: 940694492
- **Purpose**: Tracks own castle position, fires on teleport
- **Separate from**: `UnionEntitiesNtf` (alliance members) — own castle not in own entity list
- **Handler**: `_on_city_update` stores in `_home_coord`, persists via `_save_home_coords`
- **Used by**: `_check_home_moved` in runner (detects teleports), startup fallback for home coords

### Alliance Reinforce Button
- Ally castle panel shows **yellow** REINFORCE button (not blue territory one)
- `reinforce_button.png` renamed to `territory_reinforce.png` (all territory/quest refs updated)
- `alliance_reinforce.png` created for ally castle panel detection
- Castle tap changed from 9-point grid + detail_button.png to single `adb_swipe` center tap

### Reinforce Failure Detection Gap
- When reinforcement fails (depart not found), `reinforced` dict and `active_coords` not updated
- Entity keeps getting re-queued by event emissions
- Successful reinforcements correctly tracked with 30-min cooldown
- **Open**: Should failed attempts also set a shorter cooldown?

### Distance Calculation
- Protocol coords are raw (multiply display coords by 1000)
- Distance uses `sqrt((x1/1000 - x2/1000)^2 + (z1/1000 - z2/1000)^2)` in display units
- max_reinforce_distance = 80 confirmed appropriate by user testing
- Own castle filtered at distance < 2

### Variation Setting
- `variation` setting exists (default 0) — adds +-N seconds to all interval timers
- Applied to: titan, groot, pass, reinforce, ESB runners via `sleep_interval()`
- Was missing from settings UI — added to Auto Quest card (0-60 seconds number input)

### Log Rotation Fix (botlog.py)
- `setup_logging` guard changed from `if root.handlers: return` to checking for `RotatingFileHandler`
- Third-party libs adding handlers before `setup_logging` prevented file handler creation
- maxBytes increased from 5MB to 20MB

---

## Protocol — Unhandled Message Opportunities

### New Handler Candidates
| Message | Purpose | Frequency |
|---------|---------|-----------|
| TeleportCityAck | Instant teleport pass/fail (errCode) | On teleport |
| ResourceMineLineNtf | Mithril/gold status (heroId, power, StartTs, mine type, beAttackedInfo) | On gather |
| HealingSoldierGetAck | Wound counts by soldier type (Map<int,int>) | On heal check |
| EvilInvasionQueryAck | playerPoint, unionPoint, maxPoint, beginSta, rewards | On EG query |
| UnionLandsNtf | Union territory (coord, type, unionId, FactionId) | 12/session |
| RallyAutoStatus | Game-native auto rally (12 fields: enable, targetTypes, monsterLevels) | On config |
| GvgDragonFreeTeleportNtf | Free teleport zone detection | On event |
| GarrisonHeroAck/Ntf/Req | Garrison hero management | On hero change |
| KvkBuildingOccupyNtf | Tower occupy events (buildingCfgId, occupyTime, unionName) | Real-time |
| UpdateGiftNtf | Gift shop updates (49 fields) | 104/session |

### Unused Data in Already-Captured Messages
- Rally target coords: `playerCity.coord` / `npcCity.coord` available but unused
- Rally power limit: `rallyPowerLimit` for pre-check before joining
- Rally state timers: `rallyStateEndTS` for expiry prediction
- Rally soldier composition: `troops[].soldiers`, `troops[].heros`
- City level: `playerCity.cityLevel` for smarter target selection

### EvilBtl Protocol Messages (future use)
| Message | Wire ID | Purpose |
|---------|---------|---------|
| EvilBtlApplyNtf | 748432728 | Alliance-wide when member attacks priest |
| EvilBtlBossKilledNtf | 1720894676 | Alliance-wide priest death |
| EvilBtlSceneNtf | 3060785838 | Real-time battle scene updates |
| EvilBtlActvNtf | 1002972674 | EG battle event start/update |

---

## Template Matching — Detailed Failure Data

| Template | Avg Best | Threshold | Misses/Session | Fix |
|----------|---------|-----------|----------------|-----|
| rally_titan_select.png | 42% | 65% | 12 | 0% early-session, titan walk-away |
| mithril_return.png | 60-69% | 70% | 14-15 | Lower to 65% or increase timeout 2s→3s |
| depart.png | 31% (some flows) | varies | 7 | Widen search region or add variant |
| heal.png | 45-49% | varies | 8-17 | Template quality |
| stationed.png | N/A | N/A | 103 | Widen IMAGE_REGIONS entry |

### Navigation Screen Detection
- TERRITORY screen: 64% confidence causes false-UNKNOWN, 5 consecutive misses, nav loops
- Unknown screen recovery: 6 UNKNOWN detections/session, 42-64% match from popups/transitions

---

## Timing Budget Fixes Needed

| Transition | Budget | Met | Fix |
|-----------|--------|-----|-----|
| gather_tab_load | 0.8s | 0/4 | Increase to 1.2s |
| gold_mine_select | 3s | 0/4 | Transition not detected |
| rally_confirm | varies | partial | Budget overruns |
| troop_panel_load | varies | inconsistent | Device-specific |
| territory_grid_load | varies | slow first open | After restart |

---

## Performance Optimization Opportunities

- **scrcpy screenshots**: 5-10x faster than ADB screencap (~20-40ms vs ~200ms)
- **SSE dashboard**: Replace 3s polling with Server-Sent Events
- **Behavior tree**: Replace linear quest dispatch priority chain
- **Multi-template registry**: Per-device or multi-variant matching for device-specific failures

---

## Test Coverage Gaps

| Module | Tests | Key Functions |
|--------|-------|---------------|
| titans.py | 0 | rally_titan, restore_ap |
| farming.py | 0 | mine_mithril, gather_gold |
| evil_guard.py | 0 | rally_eg, _handle_ap_popup |
| decoder.py | limited | CompressedMessage edge cases |
| territory.py | partial | attack_territory, frontline_occupy_loop untested |

### Frontline Occupy Tests (test_frontline_occupy.py)
1. `test_recenter_on_tower_after_teleport` — verifies navigate_to_coord called twice
2. `test_recenter_failure_skips_cycle` — menu/depart skipped if recenter fails
3. `test_depart_called_after_successful_recenter` — full happy path

---

## Frontline Occupy — Detailed Findings

### Key Fixes (2026-03-05)
- **Post-teleport recenter**: `navigate_to_coord(device, world_x, world_z)` after teleport (was blind tap at 540,900)
- **World coord formula**: `_grid_to_world(row, col)` = `col*300000+150000, row*300000+150000`
- **Ntf guard removed** (game_state.py): Old code blocked Ntf until Ack; now always updates
- **ProtocolDataPending**: Simplified to log warning (opening territory screen doesn't trigger Ack)
- **Green-before-red** (combat.py): Was checking red first, skipping valid positions
- **Entity tap recovery** (combat.py): Added BACK keyevent for entity dialogs at screen center

### Attack Mode Capture + Recall
- After deploying attack troop, polls protocol grid every 10s until `owner_team` flips to `my_team`
- Calls `recall_tower_troop` on capture. `_wait_for_capture()` helper
- Reinforce mode: troop stays defending (no recall)

### Auto Occupy Removal (commit ae7bcd9)
- Removed `auto_occupy_loop`, `open_territory_manager`, `run_auto_occupy`, `run_debug_occupy` (~1,261 lines)
- Frontline occupy is now sole territory mode

### Settings Persistence Bug (commit ef09e30)
- `frontline_enemy_teams` and `frontline_occupy_action` missing from `SETTINGS_RULES`
- Dashboard save API silently rejected them. Fixed.

### Territory Data Lifecycle
- `KvkTerritoryInfoAck` arrives on game login only (NOT territory screen open)
- `KvkTerritoryInfoNtf` streams real-time as towers change
- Cache: `data/territory_grid_{device_hash}.json` — hash changes if device ID format changes

### Live Test (2026-03-05, Kodashon)
- Tower (11,0), world (150000, 3450000)
- All 5 steps passed: navigate → teleport → recenter → menu (reinforce) → depart → recall

---

## IL2CPP Camera Control (2026-03-08)

- `MapCameraMgr.MoveCameraToTargetInstantly(float x, float z, Action callback)` — fully static class
- Takes display-scale floats (NOT protocol *1000). E.g., (1075.0, 4650.0)
- Produces natural WildMapViewReq — least detectable approach
- `il2cpp_method_get_flags & 0x10` checks static (MethodAttributes.Static = 0x0010)
- `moveCamera` RPC in frida_hook.js, `move_camera()` on InterceptorThread
- `move_camera_to()` in `actions/reinforce_ally.py` — IL2CPP fast path, navigate_to_coord fallback
- Used in: territory.py (frontline_occupy_loop), reinforce_ally.py

---

## Entity Pre-filter for Teleport (2026-03-08)

- After camera move, WildMapViewAck populates entity data
- `_entity_snapshot` in game_state.py: persists 60s, timestamped
- `get_entities_near(center_x, center_z, radius, max_age_s)` queries with freshness
- Teleport rings: 15 points/ring, 3 rings at radii (22, 46, 70) display coords
- Per-entity exclusion (display coords): Tower=10.5, EG=6.0, default=3.5
- Castle placement radius = 12 (from teleport_circle.png at 36.5 px/display-coord zoom)
- Pre-filter: `exclusion = castle_r + entity_r`, skip if `distance < exclusion`

---

## Protocol Tower Occupancy (2026-03-08)

- Replaced 3x `alliance_occupied.png` visual checks with `_kvk_tower_troops` data
- KvkBuilding entity has `troops` list — `len > 0` = occupied
- After camera move: poll up to 3s (200ms intervals) for entity data
- `troop_count > 0` → skip with 2.5-5s random delay (human pace)
- Skips ANY occupied tower (ally or enemy) — only targets empty towers

---

## Territory Building Types (2026-03-08, IN PROGRESS)

- Extended grid tuple to 6 elements: `(FactionId, curFactionId, legionId, curLegionId, type, cfgId)`
- Persistent `data/territory_buildings_{hash}.json` — only adds, never deletes
- API: `GET /api/territory-buildings?device_id=...`
- All 576 buildings type=6. cfgId differentiates:

| cfgId Range | Count | Category |
|-------------|-------|----------|
| 10015 | 297 | Regular tower |
| 10014 | 174 | Regular tower |
| 10001 | 73 | Regular tower |
| 10002-10013 | 8 | Rare tower variant |
| 20001-20015 | 18 | Special building (haunted castle, fortress, etc.) |

### Special Buildings (20xxx) Coordinates
```
( 2, 6) cfgId=20006   ( 2,18) cfgId=20014   ( 2,22) cfgId=20006
( 6, 2) cfgId=20010   ( 6,22) cfgId=20001   ( 8,13) cfgId=20002
(10, 4) cfgId=20015   (10, 8) cfgId=20007   (10,16) cfgId=20015
(14, 4) cfgId=20011   (14, 8) cfgId=20002   (14,16) cfgId=20011
(14,20) cfgId=20002   (16,14) cfgId=20007   (20,14) cfgId=20002
(22, 2) cfgId=20010   (22,18) cfgId=20010   (22,22) cfgId=20001
```

**PENDING**: User to identify which cfgId = haunted castle, then classify all and add exclusion radii.

---

## Auto Reinforce Ally — Additional Details

### Alliance Cache
- Populated from `UnionNtf` ONLY (full roster at game login), NOT from entities
- `UnionEntitiesNtf` caching was REMOVED — contains non-alliance entities (tested: "Szefcc IS ally but others weren't")
- Functions: `update_alliance_cache`, `get_cached_union_id`, `is_alliance_member`
- `_own_union_id` loaded from disk cache on startup

### Visual Shield Detection
- HSV color space: gold-hue pixel density in elliptical ring zone (r=120-220)
- Calibration: Shielded ~88% gold, Unshielded ~47% gold, threshold 70%
- `shield.png` template NOT used (scale mismatch)

### Shield UI Timing
- `_apply_shield_via_ui`: shield button wait 2.5s (was 2.0s)
- `_query_shield_via_ui`: shield button wait 2.5s (was 1.0s — was firing too fast)
- 8hr shield button: `adb_swipe` 200ms hold (was `adb_tap`)
- Post-8hr: 1.5s wait, post-confirm: 1.0s wait

### Open Issues
- **Castle grid tap failing**: Taps at y=906-1025 miss castle at y=700-800. Need recenter around (540, 800)
- **Debug code to remove**: PLAYER_CITY debug block ~line 1245 in game_state.py
- **UnionEntitiesNtf bootstrap**: First entity used for `_own_union_id` may not be alliance member

---

## Evil Guard — Additional Details

### EG Skip Detection Logic
1. Find all EVIL entities (type=27)
2. Find PLAYER_TROOP (type=1) with matching unionID
3. Check BattleInfo.target, AttackInfo.ID, AttackInfo.Coord proximity (500k threshold)
- After each search: wait 1s for entity data, call `get_protocol_eg_claimed()`. True→cycle, None→no protocol, False→attack

### Async March Flow (6-step)
1. After `_search_eg_center`, capture EG boss coords via `get_evil_entity_centroid()`
2. After `click_depart_with_fallback`, check `get_protocol_march_eta(device)`
3. `_EG_YIELD_THRESHOLD_S = 30s`
4. State: `{eg_coords, march_arrival, attacks_completed, priests_dead, ...}`
5. Resume: `rally_eg_resume` → `rally_eg(..., _eg_coords_override=coords)`
6. Override uses EG boss centroid (not priest) for camera centering; re-probes all priests

### Entity Field Paths
PLAYER_TROOP (type=1) → PropertyUnion.field_1 (TroopInfo) → field_2 = BattleInfo (target, inBattle) → field_17 = AttackInfo (typ=27 for EVIL, ID, Coord)

### March Timing Source
`NewLineupStateInfo: state=2 (OUT_CITY), stateEndTs (epoch ms) = arrival time`

### Open Issues
- Coord capture: Neither EB nor LB SearchMonsterAck received (listening to both, UNTESTED)
- Stationed troop bug: Fixed by gating on `attacks_completed > 0` (UNTESTED)
- P6 dialog: Attack dialog not opening after 3 attempts (separate issue)
- Coordinate proximity threshold (500k) may need tuning

---

## Protocol Infrastructure Details

### Frida Stability
- 35 consecutive connection errors on one startup (10s backoff retry)
- 5 disconnects per session (every 1-5 min), auto-reconnect works
- Exponential backoff, version mismatch detection, watchdog auto-reconnect

### Headless Intent Types
- TeleportIntent, RallyIntent, ObjectiveIntent
- 6 message families confirmed injectable (send path works)
- Requires state-aware generation, not static replay

---

## Source Data & Analysis Index

- **APK datamine**: `C:\Project\datamine_full_2026-03-05\` — 4,169 wire entries, 3,770 proto fields
- **Runtime logs**: `C:\Project\analyze\` — 23,348 log lines, 856 files
- **Analysis docs**: `C:\Project\analyze\.claude\analysis\` — 22 research documents
- **No deeplinks**: `tfwk1://` scheme is launcher only, no gameplay navigation

### Message Frequency (25-min baseline)
Top: CompressedMessage(1330), EntitiesNtf(1028), RallyNtf(730), RedPointNtf(269), WildMapViewAck(200)

---

## Miscellaneous

### Help Badges (settings page)
- `?` badges next to every setting label, centered popup with description
- Inline CSS + JS in settings.html
- Cleaned up dead `saveFrontlineEnemyTeams`/`updateFrontlineEnemyCheckboxes` JS

### Coordinate Convention
- Protocol raw coords: e.g. 1050000
- Display coords: raw / 1000 (e.g. 1050)
- `navigate_to_coord` divides by 1000 internally
- `move_camera_to` (IL2CPP) takes display floats directly
