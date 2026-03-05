# 9Bot Roadmap

Priority: stability, usability, monetization — then new features.

---

## Completed Work

<details>
<summary>Click to expand completed phases</summary>

**Reliability & Bug Fixes** — Hardened mithril mining, titan rally retry logic, AP recovery popup handling, join_rally success rate (blind tap + depart_anyway fallback), war screen speed optimizations, timed_wait budget tuning, multi-device contention reduction (poll 150→300ms, quest check 60→300s), error recovery with stuck-state detection, settings validation, image region audit, emulator start/stop control, game restart from dashboard, APK patch integration with streaming progress.

**Testing & Quality** — 1106-test suite covering all major subsystems: combat (49), evil guard (30), titans (47), territory (66), quests (42), rallies (17), farming (22), plus vision, navigation, config, troops, runners, settings, and protocol modules. Failure screenshots added to join_rally and rally_titan debug flows. Automatic log/stats/debug uploading with progress feedback.

**UI & Project Cleanup** — Web dashboard with mobile-friendly remote control, MJPEG live view (works through relay), per-device token-based sharing (full/read-only), per-device settings overrides, collapsible controls, mithril countdown timer, QR code for phone connection, clean quit flow. Refactored main.py → runners.py + settings.py, split actions.py (3600 lines) into actions/ package.

**Quest Expansion** — Auto quest handles all types: Titan, Evil Guard, PVP, Tower, Gather, Fortress. Classification via keyword matching, dispatch priority chain, per-quest target overrides.

**New Automations** — Phantom clash auto mode (5-min interval), auto reinforce ally castles (protocol-driven, 30-min cooldown), territory pass zone model (8 mountain passes, zone-aware targeting), multi-attack occupy loop with auto-reinforce.

**Chat & Translation** — Chat viewer with protocol mirroring across devices, Claude Haiku auto-translation with Unicode script detection and batched API calls.

**Security Audit (Feb 2026)** — No critical vulnerabilities found. Fixes: relay secret redaction, device ID whitelist, DOM XSS elimination (innerHTML → textContent), bug-report POST, pinned deps, zip-slip protection, wss:// relay tunnel, Bearer auth, non-root relay service, atomic settings writes, bot enumeration removed.

**Protocol Interception** — Frida Gadget integration for instant game data reads (AP, troops, rallies, chat, entities). Per-device model with EventBus + GameState + InterceptorThread. APK patching via LIEF injection with pure Python signing. Wire registry (4,169 message types), protobuf decoder, typed message classes. Vision/OCR fast paths with automatic fallback.

**Cloud SaaS Prep** — Docker-Android setup with deployment scripts, BlueStacks CLI management from dashboard, game login flow updated to one-time codes, relay portal with device list reporting, Stripe billing integration.

**IP Protection** — Machine-bound license keys (HMAC + hardware fingerprint), remote validation against Google Sheets, 3-attempt startup check, dev mode bypass for .git repos.

</details>

---

## Phase 1 — Polish & Hardening

Improve what already exists. Small-to-medium items that increase reliability and usability.

- [ ] Teleport system improvements — more reliable targeting and validation
- [ ] Multi-device performance — screenshot caching within timed_wait cycles, device-scoped OCR locks (Semaphore instead of global Lock), scrcpy/minicap for persistent screenshot streams
- [ ] CSRF protection on POST endpoints
- [ ] SHA-256 integrity verification for auto-updater downloads
- [ ] Session summary — recap card on dashboard: runtime, rally count, gathers, heals, errors. Data already in `StatsTracker`, needs `summarize()` + endpoint
- [ ] Reorganize "More Actions" section on dashboard
- [ ] Clean up file and folder structure — organize `elements/`, consolidate debug dirs
- [ ] Notification alerts — Discord webhook (POST to user-configured URL), ntfy.sh push notifications, settings UI for event selection and channel routing
- [ ] Live testing suite — integration tests against a real emulator
- [ ] Pre-release checklist — full test pass, live smoke test, version bump verification

## Phase 2 — Telemetry & Privacy

The upload/debug pipeline exists but needs user trust features before broader rollout.

- [ ] Telemetry consent prompt — explicit opt-in dialog on first run (never silent, never pre-checked)
- [ ] Data scrubbing — strip device IPs, file paths, player names before upload
- [ ] Screenshot masking — black out chat area and name regions before staging
- [ ] Clear submitted data from local machine after successful upload
- [ ] Settings UI for telemetry — tier selection, "View queued data" button, opt-out at any time

## Phase 3 — Monetization & Distribution

Transition from free/trusted-circle to paid product.

- [ ] Make GitHub repo private (prevent unlicensed source access)
- [ ] Private download server — license-key auth for release ZIPs
- [ ] Update `updater.py` to send license key as auth header (replace GitHub API)
- [ ] Replace Google Sheets license backend with server-side API (usage tracking, auto-provisioning)
- [ ] Nuitka compilation for releases — blocked by AV false positive risk; revisit when code-signing is affordable
- [ ] Periodic re-validation during runtime (when piracy becomes an actual problem)
- [ ] Auto-screenshot on license fail — console screenshot + timestamp after 3 invalid key attempts

## Phase 4 — Cloud SaaS

Host in the cloud — no local install, full IP protection. Detailed analysis in `memory/saas_planning.md`.

### Validate
- [ ] Run beta users, validate performance and stability
- [ ] Measure actual resource usage (RAM, CPU, disk) per instance
- [ ] Evaluate alternatives: Docker-Android, cloud phone services (Redfinger), Android-x86 in Hyper-V

### Orchestration
- [ ] API for user→server assignment, start/stop instances remotely
- [ ] Server provisioning script — automate BlueStacks + 9Bot setup on fresh Windows servers
- [ ] Health monitoring — detect crashed instances, stalled bots, resource exhaustion
- [ ] Capacity tracking — which servers have free slots

### Onboarding
- [ ] "Cloud mode" dashboard — hide local setup, show instance status
- [ ] Coordinated game update patching across all instances
- [ ] Data backup strategy for user game accounts

### Launch
- [ ] Offer SaaS alongside local option
- [ ] Pricing model (target ~$15-25/mo per user at ~$7-11 cost)

### Open Questions
- BlueStacks licensing for commercial/cloud use
- Game ToS implications of cloud hosting vs user's own machine

### Future: Protocol-Only Mode
Long-term aspiration — fully reverse-engineer the game protocol, eliminate the emulator entirely.
Each user becomes a lightweight Python process (~$1-2/mo). Existing Frida protocol interception
is the foundation.

---

## Security Hardening (ongoing)

<details>
<summary>Audit completed Feb 2026 — no critical vulnerabilities</summary>

No shell injection, no eval/exec/pickle, Jinja2 auto-escaping active, all subprocess calls use
list args without shell=True. Fixes applied: relay secret redaction, device ID whitelist, DOM XSS
fix, bug-report POST, pinned deps, zip-slip protection, wss:// tunnel, Bearer auth, non-root
relay, atomic settings writes, bot enumeration removed.

</details>

### Remaining
- [ ] CSRF protection on POST endpoints *(tracked in Phase 1)*
- [ ] SHA-256 integrity verification for auto-updater *(tracked in Phase 1)*

---

## Backlog — Future Automations

Not yet prioritized. Ideas for new game automations.

- [ ] Automatic frost giant function
- [ ] Automatic lava haka spawning
- [ ] EG theft prevention — detect other players' Evil Guards to avoid stealing. Needs training data: own vs others' EG markers/rallies for template or ML-based classification
