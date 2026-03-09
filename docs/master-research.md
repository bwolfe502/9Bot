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
17. [Lessons Learned](#lessons-learned--regression-post-mortems)
18. [Session History](#session-history)
19. [Training Data Patterns](#training-data-patterns)
20. [Operational Insights — Early Sessions](#operational-insights--early-sessions-v144-v150)
21. [Pre-Release Audit v2.0.0](#pre-release-audit--v200-mar-1-2026)
22. [Development Roadmap](#development-roadmap)
23. [Architecture Deep Dive](#architecture-deep-dive)
24. [Internet Research Findings](#internet-research-findings-2026-03-02)
25. [Improvement Ideas](#improvement-ideas--prioritized)
26. [SaaS & Business Strategy](#saas--business-strategy)
27. [Protocol Research Sessions](#protocol-research-sessions-2026-03-05)

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

---

## Lessons Learned — Regression Post-Mortems

### 001: rally_button.png titan regression (1502f45, reverted)

**Date**: 2026-03-02 | **Impact**: rally_titan dropped from 13% to 0% success

**What happened**: Analysis showed `titan_on_map_select` had 0% met rate — the `timed_wait(lambda: False)` was just a disguised sleep with no verification that the titan popup actually appeared. Claude proposed replacing the blind tap at (420,1400) with a retry loop polling for `rally_button.png`.

**Why it failed**:
1. **Wrong template**: `rally_button.png` was captured from a rally dialog context, not the titan on-map popup. Different button style/size — template scored 0/7 matches.
2. **Retry loop dismissed the popup**: The loop re-tapped (540,900) center-screen on each iteration, dismissing the popup before the next poll could find it.
3. **Shortened depart budget**: Code set `depart_budget = 2` when popup wasn't found (vs 8s original), giving the depart button almost no time to appear.

**Rules derived**:
- Never replace a working blind tap with template matching without first verifying the template matches the actual target UI. Capture a screenshot, crop the button, confirm >0.8 before committing.
- Test template changes against real screenshots from stats/debug data before deploying.
- Don't reduce timeouts as part of a "fix" — if existing timeout was 8s, keep it 8s.
- Retry loops that re-tap the trigger coordinate will dismiss the very UI they're trying to detect.

---

## Session History

### Sessions 8-12: 2026-03-02 (Windows, local)

Five sessions from a single day, version transition from v2.0.6 to v2.0.7.

#### Session 8: 15:07-15:12 (5 min, v2.0.6)
- **Device**: 127.0.0.1:5635
- **Actions**: 1 check_quests (100%), 1 rally_titan (fail), 1 join_rally (fail)
- **ADB**: 243 screenshots (avg 0.345s), 37 taps, 2 swipes

#### Session 9: 15:13-15:28 (15 min, v2.0.6)
- **Device**: 127.0.0.1:5635
- **Data**: Session metadata only, no action stats recorded

#### Session 10: 15:30-18:25 (175 min, v2.0.7) — MAIN SESSION
- **Devices**: 6 (127.0.0.1:5635, :5555, :5625, :5645, :5655, :5585)
- **Memory**: 6167 MB RSS, peak 6353 MB
- **ADB totals**: 29,852 screenshots, 5,732 taps, 533 swipes — zero failures
- **Key stats**:
  - join_rally: 0/172 (0%), rally_titan: 114/153 (74%)
  - rally_eg: 10/25 (40%), pvp_attack: 5/16 (31%), occupy_tower: 7/15 (47%)
  - recall_tower: 3/3 (100%), mine_mithril: 44/45 (98%)
  - heal_all: 78/78 (100%), gather_gold: 33/33 (100%)
  - check_quests: 136/134 (94%), restore_ap: 33/36 (92%)

#### Session 11: 18:29-18:34 (5 min, v2.0.7)
- **Memory**: 507 MB (fresh restart) → 28.9 MB peak (minimal activity)
- **ADB**: 3 screenshots only

#### Session 12: 19:05-19:55 (50 min, v2.0.7)
- **Devices**: 7 (added emulator-5554)
- **Memory**: 6098 MB RSS, peak 6205 MB
- **ADB**: 2,413 screenshots (emulator-5554 avg 0.270s — fastest), 593 taps, 25 swipes
- **Key stats**: join_rally: 0/42 (0%), rally_titan: 27/43 (63%), recall_tower: 0/11 (0%)
- **Bot restarts**: 3 in 43 min (19:58, 20:01, 20:09) — possibly OOM-triggered
- **Dominant noise**: ~477 LZ4 decompression warnings
- **Rally joins**: 3 successful (all EG), 38 depart-not-found failures
- **Unknown screens**: 25 (14 from diss1 startup, 5 WAR transitional, 4 AQ popup, 2 black)
- **Frida**: Detached at 19:43 (application-requested)
- **Tunnel**: 18+ "no data in 90s" reconnections

#### Sessions 1-7: 2026-02-28 through 2026-03-02 (mixed platforms)
4 macOS local + 2 Windows inbox + 1 Windows local. Key findings: rally_button.png regression (reverted), join_rally 0% cross-platform, stationed.png region miss, search.png low confidence, multiple timing budget fixes applied.

#### Cross-Session Log Analysis: 2026-03-01 17:32 - 2026-03-02 14:48
- **Duration**: ~21 hours, **Log volume**: 129,121 lines (15.4 MB), **Devices**: 5
- 60 LOGGED OUT events on device 5585
- 929 navigation warnings (unknown screens)
- 161 Frida connection errors (expected — gadget not installed)
- 77 tunnel disconnects
- 86 "could not join or start any rally" warnings
- 56 PVP attack menu failures, 47 titan depart misses

---

## Training Data Patterns

### Session 12: 2026-03-02 (2 JSONL files, 1440 entries)

| Metric | Value |
|--------|-------|
| File 1 (td_20260302_190541.jsonl) | 292 KB, 1309 entries, 52 min |
| File 2 (td_20260302_195819.jsonl) | 29 KB, 131 entries, 3 min |
| Entry rate | 25-49/min |

**Type Distribution**: screen 1,052 (73%), template 377 (26%), OCR **0** (0%)

**Device Distribution**: emulator-5554 1,166 (81%), 127.0.0.1:5625 274 (19%)

**Screen Detection** (1,052 entries):

| Screen | Detections | Hit Rate | Avg Score | Range |
|--------|-----------|----------|-----------|-------|
| map_screen | 586 | 100% | 97 | 95-99 |
| bl_screen | 132 | 100% | 97 | 91-97 |
| war_screen | 127 | 96.1% | 96 | 61-100 |
| aq_screen | 119 | 97.5% | 98 | 55-100 |
| alliance_screen | 48 | 100% | 100 | 100 |
| td_screen | 30 | 100% | 100 | 90-100 |
| kingdom_screen | 10 | 100% | 100 | 100 |

**8 UNKNOWN screens**: 3 aq_screen at 55-59% (popup overlay), 5 war_screen at 61% (transition frame)

**Template Analysis** (377 entries, 16 unique templates):

| Template | Hit Rate | Issue |
|----------|----------|-------|
| mithril_return.png | 26% (5/19) | 100% miss on emulator-5554, 100% hit on :5625 |
| statuses/defending.png | 8% (1/12) | 100% miss on emulator-5554, hit on :5625 |
| search.png | 100% (50/50) | All at exactly 66%, threshold 0.65, 1-point margin |
| back_arrow.png | 97% (84/87) | 3 near-misses at 65% (screen obstructed) |

**Notable gaps**: Zero OCR entries logged, 18 orphan training images without JSONL entries

---

## Operational Insights — Early Sessions (v1.4.4-v1.5.0)

Gathered from 23 sessions across v1.4.4 → v1.5.0, up to 4 devices (Feb 28 – Mar 1 2026).

### Devices
- `127.0.0.1:5555` — "Nine" (primary)
- `127.0.0.1:5585` — "Plippy"
- `127.0.0.1:5635` — "diss1" (had persistent ADB issues, returncode=4294967295)
- `emulator-5554` — "Nine" (legacy, used with main.py)

### Action Success Rates (aggregate)
- `pvp_attack`: Very poor — 0% in many sessions, ~10-25% at best
- `join_rally`: High failure rate — frequently 0/N
- `rally_titan`: Moderate — 60-80% when working. Early sessions worse (11/15 fail in session 2)
- `rally_eg`: ~50-75% success rate in later sessions
- `mine_mithril`: Generally good EXCEPT 50 consecutive failures on device 5635 (`mithril_return.png` at 0% confidence)
- `occupy_tower`: Started at 0/5, improved to 2/2 in later sessions
- `gather_gold`: Reliable (3/3, 5/5)
- `restore_ap`: Reliable (3/3, 2/2)

### Template Confidence Issues
| Template | Observed Confidence | Threshold | Issue |
|----------|-------------------|-----------|-------|
| depart.png | ~33% after slot selection | 80% | Not found in join_rally |
| rally_titan_select.png | 45-49% | 80% | Consistently below threshold |
| attack_button.png | 35-68% | 80% | Variable, often below for PVP |
| mithril_return.png | 69% then dropping to 0% | 80% | Mass mine_mithril failures on 5635 |
| PVP depart.png | ~74% | 80% (lowered to 70%) | Already addressed |

### Memory Usage
- Idle: 93-507 MB
- Active multi-device OCR: 4.5-9.9 GB
- Peak observed: 11.5 GB (4-device session with heavy OCR)

### Debug Assets Summary (cleared Mar 1 2026)
- `debug/owner_ocr/`: 114 images — rally owner OCR tuning at 38 y-positions (476-1629px)
- `debug/training_squares/`: 286 territory square images — right half of grid only (cols 12-23)
- `data/`: Empty — territory coordinate scan never completed

---

## Pre-Release Audit — v2.0.0 (Mar 1, 2026)

10+ parallel agents covering tests, imports, security, thread safety, templates, versions, settings, error handling, requirements, actions exports, dead code.

### Test Suite: 709 passed, 2 warnings (40.86s)

### Bugs Fixed (3 medium severity)
1. `actions/farming.py:165` — `load_screenshot()` returns None → crash in `_is_mine_occupied()`. Added None guard.
2. `main.py` cleanup_dead_tasks — `running_tasks[key]` KeyError. Changed to `.get(key)`.
3. `territory.py:884` — JSON write could crash on disk full/permission. Wrapped in try/except OSError.
4. `startup.py:190` — `os.execv` after auto-update could crash. Wrapped in try/except OSError.

### Thread Safety Fix (1 medium)
5. `web/dashboard.py` TOCTOU race — `start_task` checked `running_tasks` then launched, allowing duplicate tasks. Added `_task_start_lock = threading.Lock()`.

### Security Fix (1 low)
6. Device ID validation — 3 per-device settings routes accepted arbitrary device IDs. Added whitelist validation with `abort(404)`.

### Import Cleanup (28 unused imports across 8 files)
Files: main.py, web/dashboard.py, tunnel.py, actions/evil_guard.py, actions/titans.py, actions/farming.py, run_web.py

### Dependency Fix
7. `requirements.txt` — `werkzeug` was implicit. Pinned `werkzeug==3.1.6`.

### NOT Fixed (documented for future)
- CSRF protection, owner dashboard auth, MJPEG unbounded threads
- 10 settings keys without config globals, 4 dead template images

---

## Development Roadmap

### Phase 1: Housekeeping [DONE]
- Tag v1.1.0, delete dead code, remove unused imports, fix bare except, consolidate magic numbers
- Branch: `cleanup/phase1-housekeeping` (merged to dev)

### Phase 2: Code Quality [DONE]
- Add QuestType, RallyType, Screen enums. Migrate ~250 string literals. Break up check_quests. Recapture slot.png. 15 new tests (208 total).
- Branch: `cleanup/phase2-code-quality`

### Phase 3: Test Coverage [TODO]
- Tests for vision.py, territory.py, devices.py, task runner logic

### Phase 4: New Features [TODO]
- Complete troop status reading, user feature ideas TBD

---

## Architecture Deep Dive

### Codebase Overview (v2.0.6, 2026-03-02)
~15,400 LOC across 20+ modules, 801 tests, optional protocol interception layer.

### Key Strengths
- No circular dependencies — clean unidirectional import graph
- Shared task runners (`runners.py`) — no GUI/web duplication
- Per-device locking — prevents concurrent task races
- Graceful degradation — protocol → vision → blind taps fallback chain
- Adaptive timing — learns screen transition speeds across sessions
- Rich observability — StatsTracker, training data, session files, failure screenshots

### Key Weaknesses
- Unsynchronized quest/rally tracking state — module-level dicts without locks
- Global state explosion — 40+ mutable globals in config.py
- Test gaps — farming.py, titans.py, license.py have 0 tests
- join_rally 0% success rate — critical regression, cross-platform

### Threading Model

| Thread Type | Count | Purpose | Lifecycle |
|-------------|-------|---------|-----------|
| Main | 1 | pywebview event loop or sleep | Blocks until window close |
| Flask server | 1 (threaded) | HTTP request handling | Daemon, starts at init |
| Task workers | 1 per active task | Game automation loops | Daemon, launched by runners |
| OCR warmup | 1 | Background model loading | One-shot at startup |
| Tunnel | 1 | WebSocket relay | Daemon, auto-reconnect |
| Protocol interceptor | 1 | Frida Gadget listener | Daemon, auto-reconnect |
| Stats auto-save | 1 | 5-minute periodic save | Timer-based daemon |
| Dead task cleanup | 1 | 3-second periodic check | Timer-based daemon |

### Synchronization Audit

| State | Location | Lock | Risk |
|-------|----------|------|------|
| `running_tasks` | config.py | `_task_start_lock` | LOW |
| `DEVICE_STATUS` | config.py | None (GIL atomic) | LOW |
| `_device_locks` | config.py | `_device_locks_guard` | LOW |
| `_quest_*` tracking | actions/quests.py | **NONE** | MODERATE |
| `_rally_owner_*` | actions/rallies.py | **NONE** | MODERATE |
| `MITHRIL_ENABLED_DEVICES` | config.py | **NONE** | LOW |
| `_cached_snapshot` | troops.py | `_troop_status_lock` | LOW |
| `_thread_local` | vision.py | Per-thread | LOW |
| OCR reader | vision.py | `_ocr_infer_lock` | LOW |
| GameState | protocol/game_state.py | `RLock` | LOW |
| EventBus handlers | protocol/events.py | `Lock` | LOW |

### Protocol Architecture Layers (Bottom-Up)

```
1. Wire Format (decoder.py, 917 lines) — raw protobuf without .proto files
2. Registry (registry.py, 281 lines) — BKDR hash ↔ message name mapping
3. Messages (messages.py, 1187 lines) — hand-crafted dataclasses with from_dict()
4. Event Bus (events.py, 328 lines) — thread-safe pub/sub
5. Game State (game_state.py, 563 lines) — per-device reactive store with RLock
6. Interceptor (interceptor.py, 746 lines) — Frida connection + decode pipeline
7. Hook Script (frida_hook.js, 502 lines) — dynamic IL2CPP resolution
```

### Protocol Limitations
1. Schema drift risk — proto_field_map.json extracted from game binary, breaks on updates
2. Rate limiting — 200 msg/s with 1-in-10 sampling above that (may lose data in battles)
3. No protocol tests — only fast-path accessor tests exist (18 tests)

### Test Suite: 801 Tests Across 28 Files

| Module | Tests | Coverage Quality |
|--------|-------|-----------------|
| troops.py | 105 | Excellent — synthetic screenshots, status model, timers |
| territory.py | 81 | Excellent — color classification, grid operations |
| web_dashboard.py | 79 | Good — Flask routes, task launching, device validation |
| check_quests_helpers.py | 60 | Good — quest dispatch logic |
| combat.py | 52 | Good — targeting, teleport, dead detection |
| vision.py | 49 | Good — template matching, OCR, ADB pipeline |
| settings_validation.py | 41 | Excellent — type coercion, range clamping |
| tower_quest.py | 35 | Good — recall, occupation, marker errors |
| rallies.py | 24 | Moderate — protocol bail-out, blacklist basics |
| navigation.py | 20 | Moderate — screen detection, recovery phases |
| botlog.py | 19 | Good — StatsTracker, timed_action decorator |
| training.py | 15 | Moderate — JSONL logging, image capture |
| runners.py | 14 | Basic — task lifecycle only |
| tunnel.py | 12 | Basic — connection logic |
| protocol_integration.py | 18 | Basic — accessor tests only |
| evil_guard.py | 7 | Weak — only marching-troop guard |

### Critical Test Gaps (0 Tests)

| Module | LOC | Why It Matters |
|--------|-----|----------------|
| actions/farming.py | ~300 | Daily automation, interval logic, depart detection |
| actions/titans.py | ~425 | AP restore flow, search retry, gem limits |
| license.py | ~150 | Key validation, device binding |
| updater.py | ~200 | Version checking, asset download, file safety |

### Testing Gaps
- No integration tests with real screenshots
- No timeout/retry path tests (timed_wait backoff, ADB reconnect)
- No race condition tests for quest/rally state
- No performance regression tests using StatsTracker data
- Protocol decoder/hook pipeline untested

### Web Dashboard & API

**Routes**: `/` (dashboard), `/settings`, `/settings/device/<id>`, `/guide`, `/debug`, `/logs`, `/territory`, `/calibrate`, `/d/<dhash>` (friend view). 25+ JSON API endpoints.

**Security Model**:
- Token auth: SHA256(license_key + device_id)[:16] — full vs read-only
- Constant-time comparison, XSS prevention (textContent only)
- Device ID whitelist validation, path traversal protection
- `_task_start_lock` serializes task starts

**Real-Time**: Status polling 3s, client-side troop timer countdown, MJPEG streaming (1-10 fps)

### Vision System Performance

| Operation | Windows | macOS |
|-----------|---------|-------|
| ADB screencap | ~500ms | ~200ms |
| Template match (region) | ~5ms | ~5ms |
| Template match (full) | ~50ms | ~30ms |
| EasyOCR inference | 500-2000ms | N/A |
| Apple Vision OCR | N/A | ~30ms |
| Protocol AP read | ~0ms | ~0ms |

### Data Freshness Guarantees

| Source | Max Age | Fallback |
|--------|---------|----------|
| Protocol AP | 10s | OCR (500-2000ms) |
| Protocol troops_home | 30s | Pixel counting |
| Protocol troop_snapshot | 30s | Icon template matching |
| Protocol rallies | 30s | War screen UI scan |
| Panel status (quests) | 120s | Icon template matching |

### Settings & Persistence
- 26 options (13 booleans, 11 integers, 2 strings) + per-device overrides
- Strict validation: type checking, range enforcement, enum validation
- 16 keys overridable per device
- Atomic save: temp file + `os.replace()`

### StatsTracker: 5 Categories Per Device
1. **Actions**: attempts/successes/failures, total & avg duration, last 50 errors
2. **Template Matching**: miss counts + best scores, hit regions (auto-narrowing)
3. **Navigation**: failure counts per route
4. **ADB Timing**: per-command metrics, slow/failed counts
5. **Transition Times**: actual vs budgeted, samples for adaptive tuning

### Data Lifecycle

| Data | Location | Retention | Auto-cleanup |
|------|----------|-----------|--------------|
| Settings | settings.json | Permanent | N/A |
| Logs | logs/ | 5MB x 4 files | Rotating |
| Session stats | stats/ | 30 files | Oldest deleted |
| Training data | training_data/ | 10 JSONL + 200 images | Rolling cap |
| Debug screenshots | debug/ | 50 click + 200 failure | Rolling cap |
| Bug reports | uploaded | Server keeps last 10 | Manual |

### Most Fragile Subsystems
1. Rally Owner OCR — apostrophe handling via regex, silent fallback to visual hash
2. Titan Walk Detection — 8s timeout for blind tap at (540,900), 3 retries
3. Territory Color Classification — Euclidean distance thresholds (55-95), brittle to rendering
4. Marker Error Suppression — permanent until restart, no auto-recovery
5. Navigation Recursion — max depth 3, certain chains can exhaust it

---

## Internet Research Findings (2026-03-02)

### Frida & IL2CPP Hooking
- Current approach is near-optimal. Dynamic IL2CPP resolution is recommended pattern.
- **Version pinning**: Stay on Frida 16.7.x. Frida 17.x removes Java bridge entirely.
- **LIEF**: Pin `lief>=0.14,<0.18`. `add_library()` is stable.
- **APK signing**: v1 (JAR) sufficient for sideloaded APKs on emulators.
- **Anti-cheat**: Rename Gadget `.so` to avoid `/proc/self/maps` string detection.

### Computer Vision & OCR
- **PaddleOCR**: 3-10x faster than EasyOCR on CPU, ~300MB vs ~800MB. Migration path: same API shape.
- **Faster screenshots**: `py-scrcpy-client` — 35-70ms per frame vs 200-500ms ADB screencap. Alternative: `minicap` at ~80-150ms.
- **Template matching**: OpenCV `TM_CCOEFF_NORMED` remains optimal for fixed-resolution. No benefit from SIFT/ORB.
- **YOLO/ML**: Not recommended — overkill for fixed-resolution templates.

### Web Frameworks & Real-Time
- **SSE**: Highest-impact, lowest-effort upgrade. Replace 3s polling with push. Flask supports via generators.
- **HTMX**: Recommended for gradual frontend simplification. ~14KB gzipped, no build step.
- **PWA**: Low effort — manifest.json + service worker for "Add to Home Screen".
- **Framework migration NOT recommended**: Flask is adequate. Bottleneck is polling, not framework.
- **WebRTC**: Overkill. MJPEG streaming sufficient.

### Protobuf Reverse Engineering
- **Cpp2IL / Il2CppDumper**: Extract full type metadata from `global-metadata.dat`. Automate proto_field_map.json updates.
- **Code generator recommended**: Read proto_field_map.json → auto-generate Python dataclasses.
- **Schema diff tooling**: On game update, diff output, flag changes, auto-regenerate unchanged messages.
- Keep custom wire decoder — standard protobuf libraries need `.proto` files.

### Python Threading & Concurrency
- **Stay with threading** — asyncio migration not justified (all I/O is subprocess-based).
- **Free-threaded Python (3.13+)**: Not relevant yet — bot is I/O-bound.
- **Improvements**: Add RLock to quest/rally state, add `faulthandler.enable()` at startup.
- **Force-kill safety**: `PyThreadState_SetAsyncExc(SystemExit)` inherently unsafe but works in practice.

### ADB & Emulator Automation
- **py-scrcpy-client**: Most impactful upgrade (3-10x faster).
- **Emulators**: BlueStacks 5 (primary), MuMu 12, LDPlayer 9 (worth adding detection), MEmu.
- **Cloud/headless**: ReDroid (Docker-based), Genymotion Cloud, Google Android Emulator.
- **ADB optimization**: Verify `exec-out` used (vs `shell`). Consider `adbutils` for connection pooling.

### Game Bot Architecture
- **Behavior Trees**: Recommended for quest dispatch refactoring. `py_trees` library or custom 200-line BT.
- **Utility AI scoring**: Enhancement on top of behavior trees — each quest gets a utility score.
- **Session replay testing**: Record screenshot + action sequences, replay against mocked vision layer.
- **Config-driven template registry**: Move `IMAGE_REGIONS`/`TAP_OFFSETS` to JSON/YAML, hot-reloadable.
- **Anti-detection**: Add gaussian jitter to tap coordinates (±3-5px) and delays (±50-200ms).

---

## Improvement Ideas — Prioritized

### Top 10 Most Impactful Changes

| # | Change | Effort | Impact | Risk |
|---|--------|--------|--------|------|
| 1 | Fix join_rally 0% success rate | Medium | Critical | Low |
| 2 | Widen stationed.png region | 5 min | 103 perf wins/session | None |
| 3 | Add thread locks to quest/rally state | 2 hours | Prevents future races | None |
| 4 | Add titans.py + farming.py tests (36 tests) | 4 hours | Coverage P0 gap | None |
| 5 | Tune EG transition budgets (6 values) | 30 min | Reliability improvement | Low |
| 6 | Replace polling with SSE on dashboard | 3 hours | Better UX, less bandwidth | Low |
| 7 | PaddleOCR migration (Windows) | 4 hours | 2-5x faster OCR | Medium |
| 8 | Faster screenshots (scrcpy/minicap) | 6 hours | 5-10x faster captures | Medium |
| 9 | Protocol multi-device support | 2 hours | Unlock multi-device proto | Low |
| 10 | Behavior tree for quest dispatch | 8 hours | Cleaner, extensible logic | Medium |

### Architecture Improvements
- **P1**: Thread safety hardening — add RLock to quest/rally state (2-3 hours)
- **P1**: Protocol multi-device — refactor GameState from singleton to per-device keyed store (2 hours)
- **P2**: Type safety — replace task info dicts with dataclasses, TypedDict for settings (4 hours)
- **P2**: Settings hot-reload — running tasks see old settings until restart (2 hours)
- **P3**: State machine formalization — explicit transition table or enum-based FSM

### Vision & Performance
- Faster screenshots via scrcpy/minicap (5-10x improvement)
- PaddleOCR migration (2-5x faster OCR)
- ML-based element detection (YOLO for variable UI)
- Region learning bootstrap from prior session stats

### Web Dashboard
- SSE instead of 3s polling
- HTMX for simpler frontend
- PWA features (service worker, push notifications)

### Testing (~120 tests total needed)
1. titans.py: AP restore, search retry, potion selection (20 tests)
2. evil_guard.py: Full rally flow, AP popup handling (30 tests)
3. farming.py: Mithril interval, gather loop (16 tests)
4. rallies.py: join_rally UI flow, blacklist expiry (20 tests)
5. protocol/decoder.py: Protobuf parsing, compressed messages (15 tests)
6. Concurrency: Race condition tests (10 tests)
7. Integration: Screenshot-based template matching (10 tests)

### Operational
- Behavior trees for decision making
- Discord/Push notifications (Discord webhook, ntfy.sh, Pushover API)
- Config-driven actions (define game actions in YAML/JSON)

---

## SaaS & Business Strategy

### Strategic Options

| Option | Revenue Ceiling | Time to First Revenue | Risk | Tech Overlap |
|--------|----------------|----------------------|------|-------------|
| A. SaaS Game Bot | $3-5M/year | 1-3 months | Low | 95% |
| B. Game SDK + Marketplace | $1-3M/year | 6-12 months | Medium | 85% |
| C. RPA/Business Automation | $10M+/year | 3-6 months | Medium-High | 90% |

**Key insight**: 9Bot is already an RPA tool that happens to automate a game. The vision pipeline, state machine, adaptive timing, error recovery, and web dashboard are domain-agnostic.

### Option A: SaaS Game Bot — Cloud Architecture

**Recommended: Hybrid (Windows dedicated servers, multiple emulators per box)**

| Server Tier | Monthly Cost | Emulators | Cost/User |
|------------|-------------|-----------|-----------|
| GTX 1650 (64GB) | $60/mo | 6 | $10.00 |
| RTX 2060 (128GB) | $68-160/mo | 12 | $5.67-13.33 |

Providers: GPU-Mart, CloudClusters, DatabaseMart.

**Scaling**: Phase 1 (0-6mo): 3-6 users/server, manual provisioning. Phase 2 (6-18mo): Automated provisioning. Phase 3 (18mo+): Evaluate Anbox/Cuttlefish for 10x density.

### Pricing Tiers

| Tier | Price | Accounts | Margin |
|------|-------|----------|--------|
| Starter (self-hosted) | $15/mo | Unlimited | 100% |
| Cloud Basic | $29/mo | 1 | 40-65% |
| Cloud Pro | $59/mo | 3 | 66-75% |
| Cloud Premium | $99/mo | 10 | 70%+ |
| Enterprise | $199+/mo | 20+ | Custom |

### Competitor Pricing

| Competitor | Software License | Cloud Server | Managed |
|-----------|-----------------|-------------|---------|
| GnBots | $49/mo, $249 lifetime | N/A | N/A |
| BoostBot | $15-25/mo | $79-99/mo | $110-130/mo |
| BotSauce | $16/mo VIP | N/A | N/A |

### 9Bot Competitive Advantages
1. Protocol interception via Frida — no competitor does this (10-100x faster reads)
2. Web dashboard + relay — remote control from any device
3. Training data collection — building ML dataset for future vision
4. Architecture quality — clean modules, 852+ tests, adaptive timing

### Multi-Tenant Architecture
- Shared database with `tenant_id` (Pool Model)
- Tables: users, devices, settings, bot_sessions, bot_logs
- Runtime isolation: each user gets own process (maps to existing per-device threading)

### Authentication
- **Recommended**: Discord OAuth primary, Google secondary, using Authlib
- Server-side sessions with Flask-Session (JWT unnecessary for server-rendered app)
- Migration path: Dual-mode (license keys + accounts) → link keys → accounts required

### Payment Processing (Layered for Risk)
1. **Paddle** (primary) — Merchant of Record, handles tax/compliance. 5% + $0.50.
2. **Crypto** (secondary) — NOWPayments, 0.5% fees. Offer 10-15% discount.
3. **Stripe** (Starter tier only) — lower risk for software licensing.
4. **BTCPay Server** (backup) — self-hosted, zero fees, failsafe.

**Stripe risk**: Can freeze funds 90+ days. Do NOT use as sole processor.

### Option B: Game Automation Framework/SDK

**Framework extraction boundary**:
- Reusable (SDK): vision.py, navigation.py, devices.py, runners.py, dashboard skeleton, protocol/, botlog.py, config.py patterns, settings.py
- Game-specific (Game Pack): template images, screen definitions, IMAGE_REGIONS/TAP_OFFSETS, actions/, territory.py, game constants, protocol schemas

**Extraction process**: Build a second game bot (Lords Mobile / Whiteout Survival) using the 9Bot codebase. Every copy-paste reveals the framework boundary. Pattern: Rails from Basecamp.

**Plugin architecture** (3 layers):
1. Declarative (YAML/JSON) — screen defs, templates, nav graphs
2. Python API (GamePack subclass) — complex multi-step sequences
3. Raw Access — direct OpenCV, ADB, Frida for edge cases

**Marketplace**: First $10K: 0% commission. Above $10K: 15%. Steady state: 20 packs x 500 users x $12/mo = $120K/mo GMV.

**Licensing**: Apache 2.0 for SDK core + proprietary premium features.

### Option C: RPA/Business Automation Pivot

**Market**: $8-35B (2026), 20-44% CAGR. Gap: Power Automate ($40/mo) → UiPath ($420/mo) = 28x price jump.

**Technology overlap**: 90-95%. Replace ADB with pyautogui/mss (~200-400 lines). Add Playwright for browser (~500-800 lines). Everything else works as-is.

**Pricing**: Free (open source core) → $49/mo Starter → $149/mo Pro → $499/mo Business.

**Best starting vertical**: Accounting/bookkeeping (largest RPA use case, universal need, clear ROI).

### Financial Projections

**Option A — 100 Cloud Users**:
- Revenue: 60 x $29 + 30 x $59 + 10 x $99 = $4,500/mo
- Infrastructure: 9 RTX servers + DB = $1,590/mo
- Gross margin: 64.7%
- Breakeven: ~16 cloud users

**Revenue Roadmap ($0 → $50K MRR)**:

| Month | What | MRR Target |
|-------|------|-----------|
| 1-3 | Ship, sell, personal onboarding | $500 |
| 3-6 | Community, content, first affiliates | $3K |
| 6-12 | SEO compounds, word of mouth | $10K |
| 12-18 | First hire, systemize, partnerships | $25K |
| 18-24 | Second hire, enterprise tier | $50K |

### Growth Playbook
- Community-led growth (Discord): personal invites → partnerships → moderators/champions
- Content: 2 YouTube videos/month, 1 blog post/week, daily social posts
- Pricing psychology: three tiers (20-35% better conversion), $29 > $30 (left-digit effect), annual discount
- Reverse trial: 7-day Premium on signup → downgrade → upgrade prompts (15-25% conversion)
- First hire at $10-15K MRR (community/support), second at $20-30K MRR (developer)

### Churn & Retention
- Expected: 10% monthly → 10-month avg lifetime
- Top churn reasons: game burnout (35-40%), bot detection (15-20%), doesn't work well enough (15-20%)
- Key strategies: instant game-update response, visible value metrics, multi-account stickiness, annual pricing

### Legal Considerations
- Most games prohibit bots in ToS — users risk bans, not necessarily the maker
- Screen-based automation legally safer than code modification or DRM circumvention
- Structure: users responsible for game ToS compliance, generic "automation software" positioning, LLC entity
- Have backup payment processor ready

### Recommended Path
1. **Now**: Keep current model (local bot + relay). Validate demand.
2. **Phase 1**: Prove on spare PC / Hetzner server — 5-10 beta users.
3. **Phase 2**: Hetzner dedicated servers. Build orchestration API.
4. **Phase 3**: Launch SaaS alongside local option.
5. **Phase 4**: (Optional) Invest in protocol-only path if economics demand cheaper scaling.

### Cost Comparison Summary

| Option | Cost/user/mo | Code changes | Effort | Best for |
|--------|-------------|--------------|--------|----------|
| Home server | $1-2 | None | Low | Beta testing |
| Colocation | $4-8 | None | Low | Small-scale production |
| Cloud phone (Redfinger) | $3-5 | Low | Low | Quick validation |
| Hetzner dedicated | $7-11 | None | Medium | Production (10-100 users) |
| Genymotion Cloud | $36-72 | Medium | Medium | Managed fallback |
| Azure Windows VM | $50-65 | None | Medium | Enterprise / 100+ users |
| Protocol-only | $1-2 | Total rewrite | Very high | Long-term dream |

---

## Protocol Research Sessions (2026-03-05)

### APK Datamine
- Intent/deeplink surface: mostly auth/payment callbacks, no gameplay deeplinks
- 262 cleaned endpoint/domain artifacts from dex strings (smobgame.com, applovin, adjust, firebase)
- Top 50 outbound requests mapped to likely UI triggers

### Headless Session Notes
- Validated packet injection transport works
- Static payload replay fails with "parameter error 5076" — server validates context-dependent fields
- Conclusion: state-aware request construction required, not raw replay

### Protocol Research Dossier
- 4,169 wire IDs, 43 typed message classes, 15 routed semantic events
- Coverage gaps: defense engine, march orchestration, quest triggering ranked highest ROI
- Recommended research tracks documented

### Packet Test Matrix (War-Prep)
- 3 tiers: Read/Query, Navigation/State, Action-Changing
- **Validated packet set**: Rally (RallyAutoPanelReq, RallyJoinCountReq, DisbandRallyReq), Map (WildMapViewReq, ViewEntitiesReq, TeleportCityReq), March (NewTroopReq, RecallMarchReq)
- All succeeded with one-shot injection

### Protocol Action Plan — Top 10 Buildable Features
1. Red-dot task router
2. Mail monitor
3. Rally intelligence panel
4. Map activity tracker
5. Territory/union watcher
6. KVK event tracker
7. Shop/events notifier
8. Quest protocol mode
9. Outbound behavior profiler
10. Update drift guard

### Protocol Injection Test Mode
- Frida hook queued outbound patch support
- Python interceptor wrappers
- Startup helpers gated by `PROTO_INJECT_TEST_MODE=1`
- Web APIs for inject status/queue/clear, CLI tool

### Update Compatibility Matrix
- No protocol break after update + repatch
- Traffic composition shifted (more client interactions post-update)
- Zero errors, no unknown IDs in observed sets
- Post-update surfaced RedPoint, HeroSkillProp, Mail, UnionLands families

### Coverage Gap Analysis
- 4,169 wire IDs but only 43 typed classes and 15 routed events
- Highest ROI: RedPoint (9 messages, 0 routed), Mail2Nd (60 messages, 0 typed), HeroSkill (9 messages, 0 typed), UnionLands (1 message, 0 typed)

### Message Frequency Baselines

**Active baseline** (12 snapshots): 236 recv / 47 send delta, avg 3.73 msg/s, 0 errors.
Top 5: AssetNtf(38), CompressedMessage(34), RedPointNtf(24), ExploreAtlasRewardAck(18), RallyNtf(18).

**Full-count hunt** (96 snapshots, ~8 min): 4,058 total message delta, types grew from 214 to 303.
Top growth: CompressedMessage(478), EntitiesNtf(427), RallyNtf(230), BattleFrameV2Ntf(134).
Newly seen: QuestChangeNtf(26), ResourceMine family, TeleportCity family. 2 unknown IDs.

**Send-only hunt**: 171 unique outbound types. Top: WildMapViewReq(40), RankListReq(28), HeartBeatReq(20).

### Research Capture Sessions

**15-min general capture**: RedPointNtf(+348), AssetNtf(+271), CompressedMessage(+145). Newly observed rare packets across rally, teleport, scout, chat, economy families.

**12-min rally-focused capture**: Cleanly maps rally lifecycle — RallyNtf(+304), panel/list/count/update/limit pairs, RallyDelNtf(+27), plus quit/disband/kick. Validates protocol-only rally state inference.

**10-min teleport+map capture**: Maps navigation family — WildMapViewReq/Ack(+169/+283), ViewEntities(+12), TeleportCity(+7) with In/Out notifications, CoordsFavorites(+5). Rally and navigation lifecycle domains now well-covered.

### Top 20 Candidate Messages (2-min capture, ranked by increment)
CompressedMessage(143), EntitiesNtf(127), RedPointNtf(81), RallyNtf(74), AssetNtf(56).
Gaps identified: RedPoint, IntelligencesNtf, BuffNtf, CombustionStateNtf.
