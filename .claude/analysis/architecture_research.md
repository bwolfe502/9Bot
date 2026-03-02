# 9Bot Architecture Research & Brainstorm

Comprehensive codebase analysis from 7 parallel research agents + internet research.
Date: 2026-03-02 | Version: 2.0.6 | Branch: claude/research-architecture-planning-fMyOM

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Architecture Analysis](#architecture-analysis)
3. [Protocol System Deep Dive](#protocol-system)
4. [Test Suite Analysis](#test-suite)
5. [Web Dashboard & API](#web-dashboard)
6. [Game Actions & Vision](#game-actions)
7. [Settings & Persistence](#settings-persistence)
8. [Known Issues & Tech Debt](#known-issues)
9. [Internet Research Findings](#internet-research)
10. [Improvement Ideas & Brainstorm](#brainstorm)

---

## 1. Executive Summary <a id="executive-summary"></a>

**9Bot is a well-structured, production-quality game automation bot** with ~15,400 LOC across
20+ modules, 801 tests, and a sophisticated optional protocol interception layer. The codebase
demonstrates mature engineering: clean dependency graph, per-device locking, graceful degradation,
adaptive timing, and comprehensive logging/metrics.

### Key Strengths
- **No circular dependencies** — clean unidirectional import graph
- **Shared task runners** — `runners.py` eliminates GUI/web duplication
- **Per-device locking** — prevents concurrent task races
- **Graceful degradation** — protocol -> vision -> blind taps fallback chain
- **Adaptive timing** — learns screen transition speeds across sessions
- **Rich observability** — StatsTracker, training data, session files, failure screenshots

### Key Weaknesses
- **Unsynchronized quest/rally tracking state** — module-level dicts without locks
- **Global state explosion** — 40+ mutable globals in config.py
- **Protocol singleton** — GameState doesn't support multi-device
- **Test gaps** — farming.py, titans.py, license.py have 0 tests
- **join_rally 0% success rate** — critical regression, cross-platform

### Critical Bugs (Active)
1. `join_rally` — 0% success rate (both platforms) — `jr_detail_load` transition broken
2. `rally_titan` — ~13% success rate — titan walks off-center before blind tap
3. PVP attack menu — 58 failures on Windows — button position varies

---

## 2. Architecture Analysis <a id="architecture-analysis"></a>

### Module Dependency Graph (Verified — No Cycles)

```
run_web.py (primary entry)
  ├── startup.initialize() → settings, devices, OCR warmup, protocol
  ├── web/dashboard.create_app() → Flask routes
  ├── tunnel.start_tunnel() → WebSocket relay (optional)
  └── pywebview / browser fallback (blocking main thread)

web/dashboard.py (Flask)
  ├── runners.py (shared task runners — no duplication)
  ├── actions/ (game actions)
  ├── vision.py, navigation.py, troops.py, territory.py
  ├── devices.py, settings.py, config.py, botlog.py
  └── startup.py (protocol accessors, relay config)

runners.py (task coordination)
  ├── config.get_device_lock(device) → per-device serialization
  ├── actions/ (all game actions)
  ├── troops, navigation, territory, vision
  └── botlog (logging, metrics)

actions/ package (no cycles)
  _helpers   → (leaf)
  farming    → (leaf)
  combat     → _helpers
  titans     → _helpers
  rallies    → _helpers
  evil_guard → titans, combat, _helpers
  quests     → (lazy) rallies, combat, titans, evil_guard, farming, _helpers
```

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

**Verdict**: Quest/rally tracking state is the main synchronization gap. Currently mitigated
by per-device locking in runners.py, but fragile if code is refactored for multi-device
coordination.

### Global State in config.py (~45 mutable globals)

Per-device state:
- `DEVICE_TOTAL_TROOPS[device]`, `LAST_ATTACKED_SQUARE[device]`, `DEVICE_STATUS[device]`
- `LAST_MITHRIL_TIME[device]`, `MITHRIL_DEPLOY_TIME[device]`

Global feature toggles (set via apply_settings):
- `AUTO_HEAL_ENABLED`, `AUTO_RESTORE_AP_ENABLED`, `PROTOCOL_ENABLED`
- `EG_RALLY_OWN_ENABLED`, `TITAN_RALLY_OWN_ENABLED`, `GATHER_ENABLED`
- `TOWER_QUEST_ENABLED`, `MY_TEAM_COLOR`, `ENEMY_TEAMS`
- AP settings (6 bools), `MIN_TROOPS_AVAILABLE`, intervals

Task coordination:
- `running_tasks`, `auto_occupy_running`, `auto_occupy_thread`
- `MANUAL_ATTACK_SQUARES`, `MANUAL_IGNORE_SQUARES`

---

## 3. Protocol System Deep Dive <a id="protocol-system"></a>

### Architecture Layers (Bottom-Up)

```
1. Wire Format (decoder.py, 917 lines)
   └── Raw protobuf decoding without .proto files
   └── All 5 wire types + LZ4 compression + nested messages

2. Registry (registry.py, 281 lines)
   └── BKDR hash ↔ message name mapping
   └── Two registries: wire (bare names) + internal (cspb-prefixed)

3. Messages (messages.py, 1187 lines)
   └── Hand-crafted dataclasses with from_dict() constructors
   └── Lineup, Rally, Asset, ChatOneMsg, Intelligence, etc.

4. Event Bus (events.py, 328 lines)
   └── Thread-safe pub/sub with Lock + copy-before-iterate
   └── Dual emission: raw msg:* + semantic EVT_* events

5. Game State (game_state.py, 563 lines)
   └── Per-device reactive store with RLock
   └── Freshness tracking (10s AP, 30s troops/rallies)

6. Interceptor (interceptor.py, 746 lines)
   └── Frida connection + decode pipeline
   └── Rate limiting (200 msg/s), auto-reconnect

7. Hook Script (frida_hook.js, 502 lines)
   └── Dynamic IL2CPP resolution (no hardcoded RVAs)
   └── Hooks TFW.NetMsgData.FromByte/MakeByte
```

### Data Pipeline

```
Game Process (Frida Gadget)
  ↓ send({type: "recv", msgId, len}, payload)
Python _on_frida_message()
  ↓ wire_registry lookup (BKDR hash → name)
  ↓ CompressedMessage? → LZ4 decompress → re-dispatch
  ↓ ProtobufDecoder.decode() (schema from proto_field_map.json)
  ↓ MESSAGE_CLASSES[name].from_dict() (typed dataclass)
  ↓ MessageRouter.route()
  ├── bus.emit("msg:{name}", msg)        # raw
  └── bus.emit(EVT_*, payload)           # semantic
GameState handlers
  ↓ Update state buckets (RLock protected)
Fast-path accessors (startup.py)
  ↓ get_protocol_ap(), get_protocol_troops_home(), get_protocol_rallies()
Vision/Troops fallback
  ↓ Returns None if stale → OCR/pixel counting fallback
```

### Thread Safety Strengths
- EventBus: Lock protects handler registry, copy handlers before iterating
- GameState: RLock (reentrant), properties return dict copies
- No deadlock potential: locks held briefly, no nested acquisition
- Exceptions in handlers caught and logged, never propagate

### Protocol Limitations
1. **Singleton GameState** — only supports one device (multi-device falls back to vision)
2. **Schema drift risk** — proto_field_map.json extracted from game binary, breaks on updates
3. **Rate limiting** — 200 msg/s with 1-in-10 sampling above that (may lose data in battles)
4. **No protocol tests** — only fast-path accessor tests exist (18 tests)

---

## 4. Test Suite Analysis <a id="test-suite"></a>

### Overview: 801 Tests Across 28 Files

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
| **actions/farming.py** | ~300 | Daily automation, interval logic, depart detection |
| **actions/titans.py** | ~425 | AP restore flow, search retry, gem limits |
| **license.py** | ~150 | Key validation, device binding |
| **updater.py** | ~200 | Version checking, asset download, file safety |
| **main.py** | ~400 | Deprecated but still in use |
| **run_web.py** | ~100 | Primary entry point |

### Testing Patterns (Strengths)
- Autouse fixtures reset quest/rally/territory state before each test
- Mock patches target usage point, not definition (`actions.rallies.navigate` not `navigation.navigate`)
- Synthetic test data built on-the-fly (no fixture files)
- Multi-device isolation tested with `mock_device` / `mock_device_b`
- Thread safety tested with `threading.Barrier`
- Parametrize used extensively for related test cases

### Testing Gaps
- No integration tests with real screenshots
- No timeout/retry path tests (timed_wait backoff, ADB reconnect)
- No race condition tests for quest/rally state
- No performance regression tests using StatsTracker data
- Protocol decoder/hook pipeline untested

---

## 5. Web Dashboard & API <a id="web-dashboard"></a>

### Route Summary

**Pages**: `/` (dashboard), `/settings`, `/settings/device/<id>`, `/guide`, `/debug`,
`/logs`, `/territory`, `/calibrate`, `/d/<dhash>` (friend view)

**API Endpoints**: 25+ JSON endpoints for status polling, task control, screenshots,
streaming, calibration, settings, territory grid, protocol toggle, bug reports, uploads

### Security Model
- **Token auth**: SHA256(license_key + device_id)[:16] — full vs read-only access levels
- **Constant-time comparison**: HMAC-safe token validation
- **XSS prevention**: textContent + DOM creation (no innerHTML for dynamic data)
- **Device ID validation**: whitelist against get_devices() on every request
- **Path traversal protection**: rejects `/`, `\`, `..` in calibrate filenames
- **TOCTOU guard**: `_task_start_lock` serializes task starts

### Real-Time Features
- Status polling every 3s via fetch()
- Client-side troop timer countdown (decrements between polls)
- MJPEG streaming (1-10 fps, 10-95% quality)
- Polling fallback for browsers without MJPEG support

### Relay Tunnel Architecture
- WebSocket to `wss://1453.life/ws/tunnel`
- Proxies HTTP requests through WebSocket to localhost:8080
- Exponential backoff reconnect (5s → 60s cap)
- MJPEG streaming via stream_start/chunk/end protocol
- Bot name = SHA256(license_key)[:10]

### Improvement Opportunities
- No rate limiting on screenshot/stream/status endpoints
- No WebSocket for real-time updates (uses polling instead)
- Settings form fields not re-validated against enums server-side
- Device hash collision unhandled (SHA256[:8], unlikely but possible)
- No "unsaved changes" warning on territory grid page

---

## 6. Game Actions & Vision <a id="game-actions"></a>

### Quest Dispatch Priority Chain

```
1. PVP Attack    → Fast, no AP, 1 troop, 10-min cooldown
2. Tower Quest   → Fast, 1 troop, Friend tab markers
3. EG Rally      → 2 troops needed, 70 AP cost
4. Titan Rally   → 1 troop, 20 AP cost, 3-attempt retry
5. Pending Wait  → Blocks while titan/EG rallies in-flight (6-min timeout)
6. Gold Gather   → Uses remaining troops (guarded by PVP + pending gates)
```

### Most Fragile Subsystems

1. **Rally Owner OCR** — Apostrophe handling via regex, silent fallback to visual hash
2. **Titan Walk Detection** — 8s timeout for blind tap at (540,900), 3 retries
3. **Territory Color Classification** — Euclidean distance thresholds (55-95), brittle to rendering
4. **Marker Error Suppression** — Permanent until restart, no auto-recovery
5. **Navigation Recursion** — Max depth 3, certain chains can exhaust it

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

---

## 7. Settings & Persistence <a id="settings-persistence"></a>

### Settings Schema: 26 Options + Per-Device Overrides

- 13 booleans, 11 integers, 2 strings
- Strict validation: type checking, range enforcement, enum validation
- 16 keys overridable per-device via `get_device_config(device, key)`
- Atomic save: temp file + `os.replace()` (no partial writes)

### StatsTracker: 5 Categories Per Device

1. **Actions**: attempts/successes/failures, total & avg duration, last 50 errors
2. **Template Matching**: miss counts + best scores, hit regions (auto-narrowing)
3. **Navigation**: failure counts per route
4. **ADB Timing**: per-command metrics, slow/failed counts
5. **Transition Times**: actual vs budgeted, samples for adaptive tuning

### Adaptive Timing

- Loads P90 from prior session, adds 30% headroom
- Gate: 8+ samples at 80%+ success rate
- Minimum: 40% of original budget, floor 0.3s
- Result: Second+ sessions auto-tighten waits by ~20%

### Data Lifecycle

| Data | Location | Retention | Auto-cleanup |
|------|----------|-----------|--------------|
| Settings | settings.json | Permanent | N/A |
| Logs | logs/ | 5MB x 4 files | Rotating |
| Session stats | stats/ | 30 files | Oldest deleted |
| Training data | training_data/ | 10 JSONL + 200 images | Rolling cap |
| Debug screenshots | debug/ | 50 click + 200 failure | Rolling cap |
| Bug reports | uploaded | Server keeps last 10 | Manual |

---

## 8. Known Issues & Tech Debt <a id="known-issues"></a>

### Critical Active Issues

| Issue | Impact | Root Cause |
|-------|--------|------------|
| join_rally 0% success | Rally joining completely broken | jr_detail_load transition never met |
| rally_titan ~13% success | Titan rallies mostly fail | Titan walks off-center before blind tap |
| PVP attack menu 58 failures | PVP quests fail on Windows | Button position varies after target() |

### Template Issues

| Template | Problem | Impact |
|----------|---------|--------|
| stationed.png | Region too narrow (103 misses/session) | Full-screen fallback on every EG probe |
| rally_titan_select.png | Scores as low as 0.42 (12 misses vs 2 hits) | Titan search fails intermittently |
| search.png | 0.666 on macOS (barely above threshold) | Brittle titan/AP flows |
| Unknown popups | 89 instances on Windows | 5-15s wasted per occurrence |

### Timing Issues

| Transition | Budget | Met Rate | Needed |
|------------|--------|----------|--------|
| nav_kingdom_to_map | 1.0s | 0-9% | ~2.0s |
| nav_td_exit_to_map | 2.5s | 71-78% | ~3.5s |
| Evil Guard (6 transitions) | 1.0-2.5s | Low | 2.1-3.0s |

### Recent Development Trends (Last 50 Commits)

1. **Protocol Interception** (9 commits) — Frida Gadget, APK patching, fast paths
2. **Calibration Tooling** (2 commits) — /calibrate page for in-game template capture
3. **Infrastructure Fixes** (4 commits) — Tunnel reconnect, Python 3.14 compat, signing
4. **OCR Hardening** — macOS Vision quirks, training data collector

### Test Coverage Gaps (Priority Order)

| Priority | Module | Estimated Tests Needed |
|----------|--------|----------------------|
| P0 | actions/titans.py (0 tests, 425 LOC) | ~20 tests |
| P0 | actions/evil_guard.py (7 tests, 963 LOC) | ~30 tests |
| P1 | actions/farming.py (0 tests, ~300 LOC) | ~16 tests |
| P1 | actions/rallies.py join flow (24 tests, 649 LOC) | ~20 tests |
| P2 | protocol/decoder.py (0 tests) | ~15 tests |
| P2 | updater.py (0 tests) | ~10 tests |
| P3 | license.py (0 tests) | ~8 tests |

---

## 9. Internet Research Findings <a id="internet-research"></a>

### 9.1 Frida & IL2CPP Hooking

**Current approach is near-optimal.** Dynamic IL2CPP resolution via `il2cpp_class_from_name` +
`il2cpp_class_get_methods` is the recommended pattern — no hardcoded RVAs, survives game updates.

**Version pinning**: Stay on **Frida 16.7.x** (latest 16.x LTS). Frida 17.x removes the Java
bridge entirely (replaced by a new `jni` module), which would break any future Java-layer hooks.
The Gadget injection model (LIEF + config.json) is unchanged in 16.x and remains the recommended
approach for non-rooted emulators.

**LIEF compatibility**: LIEF 0.17+ re-scoped some enums (e.g. `lief.ELF.DYNAMIC_TAGS` →
`lief.ELF.DynamicEntry.TAG`). Current `patch_apk.py` uses `add_library()` which is stable.
Pin `lief>=0.14,<0.18` for safety.

**APK signing**: v1 (JAR) signing is sufficient for sideloaded APKs on emulators. Android 14/15
require v2+ signing only for Play Store installs and certain device policies. The pure-Python
signing approach (cryptography library) avoids Java/SDK dependencies — good tradeoff.

**Anti-cheat**: Frida Gadget is detectable by games using `maps` scanning (looks for
`frida-gadget.so` in `/proc/self/maps`). Mitigation: rename the `.so` to something innocuous
(e.g. `libutils-v2.so`) and update the Gadget config filename to match. Current code already
uses a generic name — verify it doesn't contain "frida" in the filename.

**Recommendations**:
- Pin Frida 16.7.x, do NOT upgrade to 17.x
- Pin LIEF <0.18 until `patch_apk.py` is tested against newer versions
- Consider renaming Gadget `.so` to avoid basic string detection
- Current IL2CPP hooking strategy is best-practice — no changes needed

### 9.2 Computer Vision & OCR

**PaddleOCR vs EasyOCR**: PaddleOCR (PaddlePaddle) is 3-10x faster than EasyOCR on CPU with
comparable accuracy for English text. Key advantages:
- PP-OCRv4 model: ~50-200ms per frame on CPU (vs EasyOCR's 500-2000ms)
- Smaller memory footprint (~300MB vs ~800MB for EasyOCR)
- Better structured text recognition (numbers, timestamps, game UI)
- Caveat: PaddlePaddle installation can be finicky on Windows; use `paddlepaddle` (CPU) not `-gpu`
- Migration path: same `readtext()` API shape, wrap in existing `read_text()` interface

**Faster screenshots**: `py-scrcpy-client` library provides h264-decoded frames at 35-70ms
(vs ADB screencap's 200-500ms). Architecture:
- Push scrcpy-server to device once, connect via TCP
- Receive h264 stream, decode to numpy frames with `av` (ffmpeg bindings)
- 3-10x faster than ADB screencap, same resolution
- Caveat: Adds scrcpy-server + av dependencies, needs ffmpeg codecs
- Alternative: `minicap` (deprecated but still works) — binary screenshot at ~80-150ms

**Template matching**: OpenCV `TM_CCOEFF_NORMED` remains optimal for fixed-resolution (1080x1920)
UI element detection. No benefit from feature-based matching (SIFT/ORB) for pixel-identical
templates. Current region-constrained approach with full-screen fallback is the right pattern.

**YOLO/ML not recommended**: Training a YOLO model for game UI detection is overkill for
fixed-resolution templates. Useful only if the game scales across resolutions or elements
are highly variable. The existing training data pipeline (JSONL + images) could support it
if needed later, but ROI is low now.

**Recommendations**:
- **High priority**: Evaluate PaddleOCR as EasyOCR replacement (3-10x speedup, biggest single perf win)
- **Medium priority**: Evaluate py-scrcpy-client for screenshot acceleration
- Template matching approach is correct — no changes needed
- Skip YOLO/ML unless resolution support becomes a requirement

### 9.3 Web Frameworks & Real-Time

**SSE (Server-Sent Events)** — Recommended as the highest-impact, lowest-effort upgrade:
- Replace 3-second polling with SSE push (`text/event-stream` response)
- Flask supports SSE via generator functions (no new dependencies)
- Client: `EventSource` API (native browser, auto-reconnect built in)
- Reduces latency from 3s average to <100ms, eliminates wasted requests
- Works through the relay tunnel (plain HTTP, no WebSocket needed)

**HTMX** — Recommended for gradual frontend simplification:
- Replace vanilla JS fetch() + DOM building with `hx-get`, `hx-swap` attributes
- Server returns HTML fragments instead of JSON (simpler server code too)
- Can migrate incrementally: one page/component at a time
- Pairs well with SSE (`hx-sse` extension for real-time updates)
- ~14KB gzipped, no build step, CDN-hostable

**PWA (Progressive Web App)** — Low effort, good mobile UX:
- Add `manifest.json` + service worker for "Add to Home Screen" prompt
- Enables fullscreen mode on mobile (no browser chrome)
- Push notifications via Web Push API (for alert_queue → browser)
- ~2 hours implementation effort

**Framework migration NOT recommended**: Switching from Flask to FastAPI/NiceGUI/Litestar would
require rewriting all routes, templates, and the relay tunnel integration. Flask is adequate
for the current needs. The bottleneck is the polling pattern, not the framework.

**WebRTC**: Overkill for this use case. MJPEG streaming is sufficient for remote viewing.
WebRTC would add significant complexity (STUN/TURN, codec negotiation) for marginal latency
improvement.

**Recommendations**:
- **High priority**: Add SSE endpoint for status updates (replaces 3s polling)
- **Medium priority**: Adopt HTMX for new pages, migrate existing pages incrementally
- **Low priority**: Add PWA manifest + service worker for mobile
- Do NOT migrate from Flask — optimize within it

### 9.4 Protobuf Reverse Engineering

**Schema extraction**: Current approach (manual `proto_field_map.json` + hand-coded dataclasses)
works but doesn't scale across game updates. Better tooling exists:

**Cpp2IL / Il2CppDumper**: Extract full type metadata from `global-metadata.dat` + `GameAssembly.dll`:
- Il2CppDumper: Outputs C# headers with field names, types, nested message structures
- Cpp2IL: Newer alternative, also generates C# stubs, better version coverage
- Both produce output that can be scripted into proto_field_map.json updates
- Run on each game update to detect schema changes automatically

**Custom code generator** — Recommended over hand-coded dataclasses:
- Read `proto_field_map.json` → generate Python dataclasses with `from_dict()` + type annotations
- Template: `messages_generated.py` auto-generated, `messages_custom.py` for manual overrides
- Eliminates manual maintenance of 1187-line `messages.py`
- Can validate field types at generation time (catch schema errors early)

**Schema diff tooling**: On game update:
1. Run Cpp2IL on new `GameAssembly.dll`
2. Diff output against previous `proto_field_map.json`
3. Flag added/removed/changed fields
4. Auto-regenerate dataclasses for unchanged messages
5. Human review for breaking changes

**Wire format**: The custom protobuf decoder (`decoder.py`) handles all 5 wire types correctly.
Standard `protobuf` or `betterproto` libraries could replace it, but they require `.proto` files
which aren't available. The custom decoder is the right choice for schema-less decoding.

**Recommendations**:
- **High priority**: Build a code generator from proto_field_map.json → Python dataclasses
- **Medium priority**: Set up Cpp2IL pipeline for automated schema extraction on game updates
- **Low priority**: Build schema diff tooling (alerts when game update changes message formats)
- Keep custom wire decoder — standard protobuf libraries need `.proto` files

### 9.5 Python Threading & Concurrency

**Stay with threading** — asyncio migration is not justified:
- All I/O is subprocess-based (ADB) or library calls (OpenCV, OCR) — not async-native
- Per-device locking model works correctly with threads
- asyncio would require rewriting every ADB call, sleep, and vision operation
- Zero benefit for CPU-bound template matching

**Free-threaded Python (3.13+)**: The `--disable-gil` build removes the GIL entirely. Not
relevant yet — 9Bot is I/O-bound (waiting for ADB, OCR), not CPU-bound. Template matching
already releases the GIL (numpy/OpenCV are C extensions). Monitor for Python 3.14 stability.

**Synchronization improvements**:
- Add `threading.RLock` to `_quest_rallies_pending`, `_pvp_cooldown_start`, `_marker_errors`
  in `actions/quests.py` (currently unprotected, mitigated by device lock)
- Add `threading.Lock` to `_rally_owner_blacklist` in `actions/rallies.py`
- Replace `time.sleep(n)` with `stop_event.wait(n)` throughout — enables faster cooperative
  cancellation (currently `_interruptible_sleep` does this, but not all callers use it)

**Debugging tools**:
- Add `faulthandler.enable()` at startup — dumps tracebacks on SIGSEGV/hang
- `py-spy` for production thread profiling (no code changes, attaches to running process)
- `threading.excepthook` (Python 3.8+) to catch unhandled thread exceptions globally

**Force-kill safety**: The current `PyThreadState_SetAsyncExc(SystemExit)` approach is inherently
unsafe — can leave locks held, files half-written. It works in practice because game actions are
stateless (no critical sections that corrupt data). Document this limitation clearly. A cleaner
alternative: use `stop_event.wait()` everywhere and give threads 5s to exit before force-killing.

**Recommendations**:
- Add explicit locks to quest/rally tracking state (2-3 hours, prevents future races)
- Add `faulthandler.enable()` to startup (1 line, huge debugging value)
- Audit all `time.sleep()` calls — replace with `stop_event.wait()` where possible
- Do NOT migrate to asyncio — threading model is correct for this workload

### 9.6 ADB & Emulator Automation

**py-scrcpy-client** — Most impactful upgrade:
- Python library wrapping scrcpy protocol (h264 stream + control)
- 35-70ms per frame vs 200-500ms for ADB screencap
- Also provides touch/key injection (alternative to `adb shell input`)
- Dependencies: `adbutils`, `av` (ffmpeg), scrcpy-server binary
- Integration: Replace `load_screenshot()` in vision.py, same numpy output
- Risk: scrcpy-server version must match library version

**Emulator support**:
- **BlueStacks 5**: Current primary. ADB over TCP, works well. Pie (Android 9).
- **MuMu Player 12**: ADB compatible, similar to BlueStacks. Android 12.
- **LDPlayer 9**: Growing user base. Supports Hyper-V coexistence (unlike BS5).
  ADB port: `emulator-5554` or `127.0.0.1:5555`. Worth adding to `devices.py` detection.
- **MEmu**: Less common, ADB compatible. No special handling needed.

**Cloud/headless emulators**:
- **ReDroid**: Docker-based Android emulator (redroid/redroid). Runs headless, GPU passthrough
  optional. ADB over TCP. Good for 24/7 cloud hosting on Linux VPS.
- **Genymotion Cloud**: Commercial, AWS/GCP/Azure. Full ADB access. $0.05-0.25/hr.
- **Google Android Emulator**: Headless mode via `-no-window`. Free but resource-heavy.
- All work via existing ADB-over-TCP — no code changes needed for `vision.py` / `devices.py`.

**ADB optimization**:
- `adb exec-out screencap -p` (binary stdout) is faster than `adb shell screencap -p` (shell encoding)
  — verify current code uses `exec-out`
- Connection pooling: `adbutils` library maintains persistent connections (vs subprocess per call)
- Batch operations: combine multiple `adb shell` calls into one (e.g. `input tap X Y && screencap`)

**Recommendations**:
- **High priority**: Evaluate py-scrcpy-client as screenshot accelerator (3-10x)
- **Medium priority**: Add LDPlayer 9 detection to `devices.py`
- **Low priority**: Document ReDroid setup for cloud/headless deployment
- Verify `exec-out` is used for screencap (vs `shell`)

### 9.7 Game Bot Architecture

**Behavior Trees** — Recommended for quest dispatch refactoring:
- Replace linear priority chain with composable tree nodes
- Standard node types: Sequence (and), Selector (or/fallback), Condition, Action
- `py_trees` library: mature, well-documented, has tick-based execution and blackboard state
- Lighter alternative: custom 200-line BT with `Selector` and `Sequence` nodes
- Benefit: Quest dispatch logic becomes visual and debuggable (py_trees has ASCII visualization)
- Example mapping:
  ```
  Root (Selector)
  ├── Sequence: PVP Attack
  │   ├── Condition: pvp_quest_available AND NOT on_cooldown
  │   ├── Condition: troops_avail >= 1
  │   └── Action: _attack_pvp_tower
  ├── Sequence: Tower Quest
  │   ├── Condition: tower_quest_enabled AND tower_quest_available
  │   ├── Condition: troops_avail >= 1
  │   └── Action: _run_tower_quest
  ├── Sequence: EG Rally
  │   ├── Condition: eg_quest_available
  │   ├── Condition: _eg_troops_available (>= 2)
  │   └── Action: rally_eg
  ...
  ```

**Utility AI scoring** — Enhancement on top of behavior trees:
- Each quest type gets a utility score (0-1) based on: AP available, troops free, time since
  last attempt, quest progress, priority weight
- Highest-scoring quest runs next (instead of fixed priority)
- More adaptive to dynamic game state (e.g. prioritize gold gather when AP is depleted)

**Session replay testing** (inspired by Viir/bots):
- Record screenshot + action sequences during real sessions
- Replay against mocked vision layer to test decision logic
- Catches regressions in quest dispatch without needing a running emulator
- Build on existing training data pipeline (JSONL already captures template/OCR decisions)

**Config-driven template registry**:
- Move `IMAGE_REGIONS` and `TAP_OFFSETS` from Python dicts to JSON/YAML
- Hot-reloadable: update regions without code changes or restart
- Pair with `/calibrate` page for in-browser template region editing
- Schema: `{ "template": "depart.png", "region": [x,y,w,h], "tap_offset": [dx,dy], "threshold": 0.8 }`

**Anti-detection considerations**:
- Humanize input timing: add gaussian jitter to tap coordinates (±3-5px) and delays (±50-200ms)
- Randomize action ordering where priority allows (e.g. shuffle equal-priority quests)
- Vary session lengths and break patterns
- Current code uses fixed coordinates and fixed delays — detectable by server-side analytics

**Recommendations**:
- **High priority**: Prototype behavior tree for quest dispatch (cleaner than current if/elif chain)
- **Medium priority**: Add config-driven template registry (JSON for IMAGE_REGIONS/TAP_OFFSETS)
- **Low priority**: Explore session replay testing using existing training data
- **Low priority**: Add input jitter for anti-detection (gaussian noise on coordinates/timing)

---

## 10. Improvement Ideas & Brainstorm <a id="brainstorm"></a>

### Architecture Improvements

#### P1: Thread Safety Hardening
- Add `RLock` to quest tracking state (`actions/quests.py`)
- Add `RLock` to rally blacklist state (`actions/rallies.py`)
- Add lock to `MITHRIL_ENABLED_DEVICES` (`config.py`)
- Estimated effort: 2-3 hours total

#### P1: Protocol Multi-Device Support
- Refactor `GameState` from singleton to per-device keyed store
- `GameStateRegistry` already exists but underused
- OR: Document single-device limitation clearly
- Estimated effort: 2 hours (refactor) or 15 min (doc)

#### P2: Type Safety
- Replace task info dicts with `@dataclass TaskInfo`
- Add type hints to config.py globals (document read-only vs mutable)
- Use `TypedDict` for settings schema
- Estimated effort: 4 hours

#### P2: Settings Hot-Reload
- Running tasks see old settings until restart
- Snapshot settings at task start, or use property accessors
- Estimated effort: 2 hours

#### P3: State Machine Formalization
- Navigation state machine is implicit (if/elif chains)
- Could use explicit transition table or enum-based FSM
- Would reduce recursion risks and make recovery more predictable

### Vision & Performance

#### Faster Screenshots
- Replace ADB screencap (~500ms) with scrcpy library or minicap (~50-100ms)
- 5-10x speed improvement for same resolution
- scrcpy-server provides h264 stream, decode individual frames

#### OCR Alternatives
- PaddleOCR: 2-5x faster than EasyOCR on CPU, comparable accuracy
- TrOCR (Microsoft): Transformer-based, excellent for structured text
- Tesseract 5 LSTM: Fastest option but lower accuracy on game fonts

#### ML-Based Element Detection
- YOLO for game UI element detection (buttons, icons, popups)
- Train on collected training data (already have JSONL + images pipeline)
- Would handle scale/position variance better than rigid template matching

#### Region Learning Bootstrap
- Load template hit regions from prior session stats at startup
- Currently recomputed fresh each session (wasted ~5-10 min of data)

### Web Dashboard

#### Server-Sent Events (SSE) Instead of Polling
- Replace 3s fetch() polling with SSE stream
- Lower latency, less bandwidth, simpler client code
- Flask supports SSE via generator functions

#### WebSocket for Bi-Directional Communication
- Would enable push notifications (task completed, troop returned)
- Better than polling for real-time status
- BUT: Adds complexity, relay tunnel would need WebSocket passthrough

#### HTMX for Simpler Frontend
- Replace much of the vanilla JS with HTMX attributes
- Server renders HTML fragments, client swaps DOM
- Dramatically simpler than current fetch() + DOM building

#### PWA Features
- Service worker for offline dashboard access
- Push notifications via Web Push API (alert_queue → browser)
- Install prompt for mobile home screen

### Testing

#### Priority Test Additions (~120 tests total)
1. titans.py: AP restore menu flow, search retry, potion selection (20 tests)
2. evil_guard.py: Full rally flow, AP popup handling (30 tests)
3. farming.py: Mithril interval logic, gather loop (16 tests)
4. rallies.py: join_rally UI flow, blacklist expiry (20 tests)
5. protocol/decoder.py: Protobuf parsing, compressed messages (15 tests)
6. Concurrency: Race condition tests for quest/rally state (10 tests)
7. Integration: Screenshot-based template matching tests (10 tests)

#### Test Infrastructure
- Add time-mocking for timeout/retry path testing
- Add concurrent multi-device test fixtures
- Add protocol mock that simulates Frida message pipeline

### Protocol System

#### Auto-Schema Update
- On game update, diff proto_field_map.json against new binary
- Flag changed fields, auto-migrate where possible
- Alert on breaking changes

#### Per-Device GameState
- Support 2+ devices with protocol enabled
- Key GameState by device_id, not singleton
- Interceptor per device (each has own Frida connection)

#### Protobuf Code Generation
- Use extracted schemas to auto-generate Python dataclasses
- Faster, type-safe, fewer manual from_dict() bugs
- Could use betterproto or custom generator

### Operational

#### Behavior Trees for Decision Making
- Replace priority chain with formal behavior tree
- Easier to visualize, debug, and extend
- Libraries: py_trees (Python), custom lightweight

#### Discord/Push Notifications
- Alert on troop return, quest complete, error states
- Via Discord webhook, ntfy.sh, or Pushover API
- Low effort, high user value

#### Cloud Emulator Support
- Run bots on Genymotion Cloud or AWS Device Farm
- 24/7 operation without local hardware
- Requires ADB-over-network (already supported)

#### Configuration-Driven Actions
- Define game actions in YAML/JSON configs
- Template: { screen, find_image, tap, wait, verify }
- Would allow non-programmers to add new game flows

---

## Summary: Top 10 Most Impactful Changes

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
