# 9Bot SaaS & Business Strategy — Full Reference

Detailed research findings from 5 parallel agents. AI-readable reference for future sessions.
Date: 2026-03-02

---

## Cloud Hosting Architecture Options

### Option A: Windows GPU VPS Per User (Simplest)
Each user gets a Windows VPS with emulator. BoostBot does this ("Cloud Bot Server" tier).
- Architecture: User browser → web dashboard → WebSocket tunnel → dedicated VPS (Windows + emulator + bot + ADB)
- Pros: Minimal code changes, existing architecture works as-is
- Cons: Expensive per user ($50-160/mo/user), hard to scale beyond hundreds

### Option B: Containerized Android (Scalable, Hard)
Anbox Cloud or Android Cuttlefish + Kubernetes. Each container runs game natively.
- Anbox Cloud claims 100-128 Android instances per machine with NVIDIA A16 GPUs
- Pros: 10-50x better density, true multi-tenant, auto-scaling
- Cons: Major rewrite, game may not run without GPU passthrough, Frida integration harder

### Option C: Hybrid (Recommended Starting Point)
Windows dedicated servers running multiple emulator instances per box.
- 6-12 users per server depending on GPU
- Cost: $5.67-17.38/user/month
- Minimal code changes, orchestration layer on top

### Server Pricing (Verified March 2026)
- GPU-Mart GTX 1650 64GB: $60/mo → 6 emulators → $10/user
- GPU-Mart RTX 2060 128GB: $68-160/mo → 12 emulators → $5.67-13.33/user
- DatabaseMart: Android emulator hosting packages
- CloudClusters: VPS emulator packages available

### Architecture Progression
Phase 1 (0-6mo): Option C, 3-6 users/server, manual provisioning
Phase 2 (6-18mo): Automate provisioning with control plane, API-driven allocation
Phase 3 (18mo+): Evaluate Anbox/Cuttlefish for 10x density if unit economics demand it

---

## Multi-Tenant Architecture

### Database Model: Shared DB with tenant_id (Pool Model)
Standard recommendation for SaaS at this scale (100-10,000 users).

Core tables:
- users (id, email, password_hash, stripe_customer_id, plan, created_at)
- devices (id, user_id, device_alias, emulator_instance, server_id, status)
- settings (device_id, key, value) — replaces settings.json
- bot_sessions (id, device_id, started_at, ended_at, task_type, stats_json)
- bot_logs (id, device_id, timestamp, level, message)

Runtime isolation: each user gets own process. Maps to 9Bot's existing per-device threading.

### What Changes From Current Codebase
- settings.json → database-backed (per-user, per-device)
- config.DEVICE_STATUS → shared Redis key (visible to web dashboard)
- botlog.py → logs to both file and database
- web/dashboard.py → multi-user with auth and tenant scoping

### Why NOT Database-Per-Tenant
At game bot scale (100-10,000 users), managing thousands of PostgreSQL databases is overkill.
Row-level isolation with tenant_id is sufficient. Game bot data not sensitive enough for
cryptographic isolation. AWS SaaS whitepaper recommends pool isolation for this tier.

---

## Authentication & Session Management

### Recommended: Discord OAuth Primary
- Game bot users live on Discord — native SSO
- Google OAuth as secondary/fallback
- Use Authlib library (lightweight, Flask-compatible)
- No passwords to manage, no custom auth system

### Sessions: Server-Side with Flask-Session
- Filesystem or SQLite storage initially
- JWT is unnecessary overhead for server-rendered Flask app
- Relay tunnel preserves cookies transparently
- Session expiry: 30 days, refresh on activity

### Why NOT JWT
- 9Bot's web dashboard is server-rendered HTML (Jinja templates)
- No SPA, no API-first architecture, no cross-domain requirements
- JWT adds complexity (token refresh, revocation, storage) with no benefit
- Server-side sessions are simpler, revocable, and sufficient

### Account Migration Path (From License Keys)
Phase 1: Dual-mode — both license keys and accounts work
Phase 2: Link existing keys to accounts
Phase 3: Deprecation warnings on key-only access
Phase 4: Accounts required (grandfathered users get appropriate tier)

---

## Payment Processing

### Layered Strategy (Risk Mitigation)

Layer 1: Paddle (Primary for Cloud Tiers)
- Merchant of Record — THEY are legally the seller, not you
- Handles VAT, sales tax, compliance in 100+ jurisdictions
- Fees: 5% + $0.50 per transaction
- Legal buffer for gray-area product

Layer 2: Crypto (Secondary, Always Available)
- NOWPayments: 0.5% fees, 350+ cryptocurrencies
- No chargeback risk, no account freezes
- Offer 10-15% discount for crypto (cost savings justifies it)

Layer 3: Stripe (Starter Tier Only)
- Software licensing is lower risk than game services
- Position as "automation software" not "game bot"
- Risk: Stripe can freeze funds for 90+ days, close accounts without warning

Layer 4: BTCPay Server (Backup)
- Self-hosted, free, zero fees
- Bitcoin/Lightning payment processing
- Failsafe if all other processors fail

### Stripe Specific Risks
- Explicitly restricts "sale of in-game currency unless merchant is game operator"
- Game bots themselves not explicitly prohibited but gray area
- Can freeze funds for 90+ days and close accounts without warning
- DO NOT rely on Stripe as sole processor

### Usage-Based Billing (Stripe Meters API)
```python
stripe.billing.MeterEvent.create(
    event_name="game_account_hours",
    payload={"stripe_customer_id": user.stripe_id, "value": "1"}
)
```
Supports sum, count, last-value aggregation. Hybrid pricing recommended:
base subscription ($29/mo for 1 account) + $10/account overage.

---

## Feature Gating

### FEATURE_GATES Dict Pattern
```python
FEATURE_GATES = {
    "multi_device": "pro",
    "protocol_interception": "pro",
    "relay_tunnel": "basic",
    "api_access": "premium",
    "webhooks": "premium",
    "training_data": "pro",
    "territory_manager": "basic",
}
```

### @require_tier() Decorator
Gates Flask routes. Returns 403 with upgrade prompt for insufficient tier.
Task launching checks tier before spawning threads.

### Monetization: Reverse Trial
- 7-day Premium trial on signup (no credit card)
- Downgrade to free tier after trial
- Upgrade prompts highlight what they lose
- Industry data: 15-25% conversion rate
- Loss aversion effect after experiencing premium features

---

## Framework Extraction (SDK)

### The Abstraction Boundary

Reusable infrastructure (SDK):
- vision.py: screenshot pipeline, find_image, find_all_matches, read_text, read_number, timed_wait, OCR warmup, adaptive region learning
- navigation.py: check_screen engine, navigate state machine, popup dismissal, unknown screen recovery
- devices.py: everything (game-agnostic ADB management)
- runners.py: launch_task, stop_task, force_stop_all, run_once, run_repeat
- web/dashboard.py: dashboard skeleton, MJPEG streaming, task API, settings UI
- protocol/: Frida injection, protobuf decoder, event bus, message router
- botlog.py: everything (pure infrastructure)
- config.py: pattern of mutable state, get_device_lock, running_tasks, DEVICE_STATUS
- settings.py: load_settings, save_settings, DEFAULTS pattern

Game-specific layer (Game Pack):
- All template images (elements/*.png)
- Screen definitions and navigation graph
- IMAGE_REGIONS and TAP_OFFSETS values
- All actions/ modules
- territory.py
- Game constants (AP costs, grid dimensions, team colors)
- Protocol message schemas and typed message classes
- wire_registry.json, proto_field_map.json

### Extraction Process
1. Build a second game bot using the 9Bot codebase
2. Every copy-paste reveals framework boundary
3. Extract after second bot works (boundary proven, not speculated)
Pattern from: Rails/Basecamp, Shopify/snowboard store, React/Facebook

### Plugin Architecture (3 Layers)
Layer 1 — Declarative (YAML/JSON): Screen defs, templates, nav graphs, popups
Layer 2 — Python API (GamePack subclass): Complex multi-step sequences
Layer 3 — Raw Access: Direct OpenCV, ADB, Frida for edge cases

### SDK CLI
```bash
pip install gamebot-sdk
gamebot init my-game-pack
gamebot capture --device ...
gamebot test-match element.png
gamebot run my-game-pack
gamebot pack my-game-pack
```

### SDK Architecture
```
gamebot/
  core/vision.py, navigation.py, devices.py, threading.py, timing.py, protocol.py
  web/dashboard.py, streaming.py, tunnel.py
  pack/base.py, loader.py, schema.py
  cli/__main__.py, init.py, capture.py, run.py, pack.py
  testing/mock_device.py, recorder.py, replayer.py
```

### Critical DX Features (Ranked)
1. Template Capture Tool — interactive screenshot + crop + threshold test
2. Offline Replay/Testing — record sessions, replay without emulator
3. Visual Debugger — real-time overlay with matches, scores, state
4. Hot Reload — change template, bot picks up without restart
5. Structured Logging — StatsTracker + timed_action generalized

---

## Marketplace Model

### Revenue Split
- First $10K: 0% commission (attract developers, build catalog)
- Above $10K: 15% commission
- Listing fee: $0 (remove friction)

### Comparable Splits
- Shopify: 85% developer (100% on first $1M)
- Apple: 70-85% developer
- Steam: 70-80% developer
- Unity Asset Store: 70% developer
- Epic: 88% developer

### Revenue at Steady State
20 game packs x 500 users x $12/mo = $120K/mo GMV
Platform revenue at 15%: $18K/mo ($216K/year)
Developer earnings: $102K/mo total ($5.1K/mo per developer)

---

## Licensing for Open Source Core

### Recommended: Apache 2.0 for SDK Core + Proprietary Premium

Why Apache 2.0:
- Maximum adoption, no corporate fear
- Patent grant protects contributors
- Compatible with every other license
- Encourages corporate use

Why NOT AGPL:
- Game bot developers build desktop tools, not SaaS
- AGPL's network clause doesn't create right pressure
- Would scare developers away without generating license revenue

Why NOT MIT:
- Apache 2.0 has patent grant (MIT doesn't)
- Otherwise similar in permissiveness

### Commercial Revenue Comes From
- Multi-device management (value multiplier)
- WebSocket relay tunnel (remote access)
- Protocol interception (Frida Gadget integration)
- Marketplace access (game pack distribution)
- Cloud hosting (no local setup)
- Advanced features: adaptive timing, training data, auto-updater
- Priority support

### Open Core Pricing
| Tier | Price | What |
|------|-------|------|
| Free | $0 | Open source core, 1 device, local dashboard |
| Pro | $15/mo | Multi-device (5), relay, advanced features |
| Team | $30/mo | Unlimited devices, protocol interception, support |
| Enterprise | Custom | Self-hosted, integrations, SLA |

---

## RPA Market Data

### Market Size (2026)
- Precedence Research: $35.27B → $247.34B by 2035 (24.2% CAGR)
- Fortune Business Insights: $27.22B → $110.06B by 2034 (19.1% CAGR)
- Mordor Intelligence: $8.12B → $28.6B by 2031 (~28% CAGR)
- Grand View Research: $3.79B (2024) → $30.85B by 2030 (43.9% CAGR)
Variance reflects scope differences. All sources agree: double-digit annual growth.

### Market Leaders
- UiPath: ~29% share, $1.3B revenue, Gartner leader 6 consecutive years
- Automation Anywhere: ~18% share, $290M Series B from Salesforce
- SS&C Blue Prism: ~10% share, acquired for $1.6B in 2022
- Microsoft Power Automate: Rising fast, now #3

### UiPath Weaknesses
- Requires .NET runtime (Windows-only for attended robots)
- Heavy Studio IDE (~2GB install)
- Per-robot licensing expensive and confusing
- Lock-in: proprietary XAML workflow format
- Overkill for simple automations

### The Gap
$40/mo (Power Automate desktop) → $420/mo (UiPath Pro) = 28x price jump.
A Python-based tool at $49-149/mo fills this massive gap.

### Technology Overlap: 9Bot → RPA (90-95%)
- vision.py screenshot + find_image → screen capture + template matching (100%)
- vision.py OCR → OCR/IDP document processing (100%)
- vision.py adb_tap → pyautogui.click (85%, different input method)
- vision.py timed_wait → smart waits for UI transitions (100%)
- navigation.py check_screen → screen/state recognition (100%)
- navigation.py navigate → workflow orchestration (100%)
- navigation.py _recover_to_known → exception recovery (100%)

### What Changes for RPA
Replace ADB with pyautogui/mss (~200-400 lines new code)
Add Playwright for browser automation (~500-800 lines)
Everything else works as-is.

### Desktop Automation Layer
| ADB Function | Windows Equivalent | Library |
|-------------|-------------------|---------|
| adb screencap | pyautogui.screenshot() / mss | pyautogui, mss |
| adb input tap | pyautogui.click(x, y) | pyautogui |
| adb input swipe | pyautogui.moveTo() + drag | pyautogui |
| adb input text | pyautogui.typewrite() | pyautogui |
| adb input keyevent | pyautogui.hotkey() | pyautogui |

---

## Competitor Analysis

### Game Bot Competitors
| Provider | Model | Games | Price | Strength | Weakness |
|----------|-------|-------|-------|----------|----------|
| GnBots | Software + cloud | 25+ | $10-15/mo | Largest (1M+ users), 7+ years | Closed source, no extensibility |
| BoostBot | Full spectrum | 15+ | $10-130/mo | Service tiers, partnerships | Mixed reviews, closed source |
| BotSauce | Software + sub | 10+ | $16/mo VIP | All-in-one, clean platform | Smaller catalog |
| Graor | Bot Maker (no-code) | 4+ | $50-75 lifetime | DIY creation, no-code | Basic image matching |
| MuBots | Bot + cloud | 8+ | Monthly | Server pricing, trials | Niche (strategy only) |
| Chimpeon | Generic macro | "Almost any" | Sub | Broadest compatibility | No game-specific intelligence |

### 9Bot Competitive Advantages
1. Protocol interception via Frida — no competitor does this (10-100x faster reads)
2. Web dashboard + relay — remote control from any device
3. Training data collection — building ML dataset for future vision
4. Architecture quality — clean modules, 852 tests, adaptive timing

### RPA Open Source Competitors
| Tool | Stars | Model | Strength | Weakness |
|------|-------|-------|----------|----------|
| Airtest + Poco | 9.2K + 1.9K | Free, NetEase | Best CV for games, IDE | QA testing, not automation |
| Appium | 18K+ | Free | Industry standard | Not designed for games |
| Robot Framework | Huge | Apache 2.0 | Ecosystem | No commercial offering |
| Robocorp | N/A | Apache 2.0 core | Python-native, CI/CD | Small ecosystem |

### Gap in Market
Nobody provides: developer-grade SDK + template matching + OCR + nav state machines +
multi-device threading + protocol interception + web dashboard. GnBots/BoostBot sell
finished products. Airtest is for QA testing. Graor is basic macro recorder.

---

## Growth & Marketing

### Product-Led Growth Benchmarks (2025-2026)
- Free → Paid conversion: 9% baseline, 15% good, 24%+ excellent
- PQL conversion: 25% baseline, 30% good, 39% excellent
- Time to value: 30min baseline, 5min good, <1min excellent
- Activation rate: 25% baseline, 40% good, 64% excellent
- Net Revenue Retention: 100% baseline, 110% good, 120%+ excellent
- Average SaaS CAC: $702 (much lower for PLG/community-driven)
- Average monthly churn: 3.5% (SMB churn 8.2x higher than enterprise)
- LTV:CAC median: 3.6:1
- NRR >= 100% companies grow 2x faster

### Discord Community Building
Phase 1 (0-100): Personal invites, extreme activity, respond within hours
Phase 2 (100-500): Server partnerships, user-generated content, weekly events
Phase 3 (500-1000+): Moderators, Champions role, community-driven roadmap
259M Discord MAU. 94 min daily engagement. 30%+ in tech servers.

### Content Strategy (1-Person Team)
- 2 YouTube videos/month (highest ROI for technical tools)
- 1 blog post/week (SEO compounds, repurpose video content)
- Daily social posts (X/Twitter, building in public)
- Weekly Discord update (dev progress)

### Pricing Psychology
- Three tiers convert 20-35% better than single tier
- Show expensive tier first (anchoring)
- $29/mo converts 8% better than $30/mo (left-digit effect)
- Annual discount: 2 months free (17% off)
- Per-device pricing scales with value
- "Most Popular" badges dramatically increase conversion

### Building in Public
Proven by: Baremetrics ($166K MRR), Plausible ($3.1M ARR zero ads), Tally ($150K MRR 4 people)
Share: dev progress, feature decisions, milestones, challenges
Don't share: exact technical implementation, individual user data, legal-sensitive details

### Early Revenue Sequence
Week 1-4: Direct DMs, personal onboarding, Discord voice
Month 2-3: Community presence, forums, subreddits
Month 3-6: Content flywheel, YouTube, blog, SEO
Month 6+: Affiliates (10-20% recurring), partnerships
Target: 20-30% monthly MRR growth, CAC < $200, LTV > 3x CAC

---

## Startup Playbook Comparisons

### Relevant Success Stories
- Supabase: Open source → $5B valuation, rejected large enterprise contracts early
- n8n: Fair-code → $40M ARR, 67 employees, $597K revenue/employee
- Robocorp: Open source Python RPA → raised $21M Series A
- ElectroNeek: MSP-focused RPA → raised $23.7M
- Plausible Analytics: Bootstrapped → $3.1M ARR, zero ads

### Open Core Revenue Timelines
- GitLab: ~$10M Y1 (2017) → $150M Y3 → $759M FY2025
- Supabase: ~$1M Y1 (2021) → $16M Y3 → $27M projected 2025
- n8n: $629K Y1 (2020) → $7.2M Y4 → $40M ARR Y5

### Warning Signs (Appsmith)
5M+ downloads, 1000+ enterprises, but only ~$4M revenue. No new funding since 2022.
Open source adoption does NOT automatically equal revenue. Need clear commercial wedge.

### Bootstrap vs Raise Decision
Bootstrap when: can reach $10K MRR with 1-3 people, organic acquisition, want control,
low infra costs, gray-area legality (VCs won't touch game bots)
Raise when: need speed vs funded competitor, narrow opportunity window, high infra costs

### Scaling What Changes
1-10 customers: You are everything, personal attention
10-100: FAQ docs, video tutorials, community self-help
100-500 ($10-50K MRR): First hire (support). Ticketing system.
500+ ($50K+ MRR): Second hire (dev). CI/CD, staging, monitoring.
Target: $300K+ revenue per employee

### First Hire: $10-15K MRR
Role: Community Manager / Support (frees founder to develop)
Where: Your own community (power users who understand both game and tool)
Model: Part-time/contract first, full-time when role proven

---

## Churn & Retention (Game Bot Specific)

### Why Game Bot Users Churn
- Game burnout / quit: 35-40%
- Bot detection / ban: 15-20%
- Doesn't work well enough: 15-20%
- Too expensive: 10-15%
- Game update breaks bot: 5-10%
- Payment failure: 5-10%

### Expected Rate
10% monthly churn → 10-month average lifetime
LTV: Starter $150, Cloud Basic $290, Cloud Pro $590

### Key Retention Strategies
1. Instant game-update response (push bot update within hours)
2. Visible value metrics ("847 rallies joined this month")
3. Protocol interception reliability (10-100x vs pure-vision)
4. Annual pricing (2 months free locks past high-churn first 3 months)
5. Multi-account stickiness (3+ accounts = rare churn)
6. Dunning recovery (smart retries = 2-4% churn reduction)
7. Community belonging (Discord = 20-30% churn reduction)

---

## Legal Considerations

### Game Bot Legal Landscape
- Most games prohibit bots in ToS — users risk bans, not necessarily the maker
- Key precedent: MDY v. Blizzard (Glider bot) — DMCA anti-circumvention for DRM bypass
- 9Bot operates at UI level (screenshots + taps), not memory injection — lower legal exposure
- Screen-based automation is legally safer than code modification or DRM circumvention

### How to Structure
1. Terms/EULA: Users responsible for game ToS compliance. Tool for "automation research"
2. No game IP in marketing: Generic "mobile game automation" not "[Game Name] bot"
3. Entity: Operate through LLC for liability protection
4. Payment: Have backup processor ready (Stripe/PayPal may freeze for "game hacking")
5. Jurisdiction: US LLC or EU entity sufficient with above guidelines

---

## White-Label / Platform Potential

### "AWS for Game Bots" Vision
Platform providing:
- Cloud Emulator Provisioning API
- Vision API (screenshot, template match, OCR)
- Navigation API (screen detection, nav)
- Protocol Interception API (Frida, decoding)
- Billing & User Management (multi-tenant scaffolding)

Third-party developers use APIs to build bots for OTHER games:
- Their templates (elements/ equivalent)
- Their actions (actions/ equivalent)
- Their web UI

### Market: 50-80 games with active bot communities
30+ games across GnBots/BoostBot/BotSauce/MuBots.
Each has 2K-10K paying users at $10-15/mo.

### RPA White-Label
Vertical consultants (bookkeeping, healthcare, real estate) use your tool
to deliver automation services to their clients. They pay platform fee,
charge clients markup.

---

## AI-Enhanced Automation (Future)

### LLM Integration Opportunities
1. Exception handling: Feed screenshot to LLM on unknown screen, ask how to dismiss
2. Document understanding: LLM extracts fields from variable-layout invoices
3. Self-healing workflows: Vision-language model finds buttons after UI change
4. Natural language creation: "Check email for invoices, extract amounts, enter QuickBooks"
5. Adaptive learning: training.py near-miss data fine-tunes models per customer

### Architecture
```
Existing: find_image() → Template match (OpenCV)
           Score < 0.8? → training.py captures near-miss
New:       near-miss → LLM fallback (vision model)
           "Find the Submit button in this screenshot"
           → Return coordinates → Continue workflow
```
Hybrid: fast template matching primary, LLM fallback on exceptions only.
Cost-effective: LLM calls on exceptions, not every action.

---

## Channel Partners

### MSP Channel (Highest Potential)
- ~40,000 MSPs in North America
- Each manages IT for 50-200 small businesses
- One deal = dozens of end clients
- ElectroNeek model: unlimited bots, flat monthly fee per MSP
- Key differentiator: no per-client licensing

### Freelancer Platforms
- Upwork/Fiverr "automate this process" gigs
- Currently use ad-hoc scripts
- Polished tool + dashboard = higher deliverables = higher rates
- Free tier as acquisition funnel

### Affiliate Program
- 10-20% recurring commission on referrals
- Popular with tech bloggers, YouTube tutorial creators
- Low cost, high leverage

---

## Database Strategy

### Start: SQLite via Flask-SQLAlchemy
- Zero deployment overhead (same as settings.json today)
- ORM abstraction means switching to PostgreSQL is a URI change
- Sufficient for first 1000+ users

### Schema
```python
class User(db.Model):
    id, discord_id, email, stripe_customer_id, tier, created_at
class Device(db.Model):
    id, user_id, device_id, alias, settings (JSON)
class BotSession(db.Model):
    id, device_id, started_at, ended_at, task_type, stats_json
```

### Migration Path
settings.json → SQLite (Phase 1) → PostgreSQL (Phase 2, when > 1000 users)

---

## Key Metrics Dashboard (Day 1)

| Metric | Target |
|--------|--------|
| MRR | 10-20% month-over-month growth |
| Monthly Churn | < 5% (ideally < 3%) |
| LTV | > 3x CAC |
| CAC | < $200 for SMB SaaS |
| NRR | > 100% |
| Activation Rate | > 40% |
| Time to Value | < 5 minutes |
| NPS | > 41 (SaaS average) |

### What Investors/Acquirers Care About
- Consistent MRR growth with low volatility
- Net Revenue Retention > 110%
- Gross margin > 70%
- Rule of X (growth rate x 2 + profit margin > 40)

---

## International Expansion

### Phase 1: English-first, global reach
- Accept USD, show local currencies (20-30% conversion lift)
- Use Merchant of Record (Paddle, LemonSqueezy) for tax compliance
- Support in English only

### Phase 2: Localize for top 3-5 markets
- AI translation (DeepL, GPT) + community review
- Format dates, numbers, currencies locally

### Phase 3: Local community presence
- Language-specific Discord channels
- Timezone-aware moderators
