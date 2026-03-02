# 9Bot SaaS & Business Strategy Brainstorm

Deep research across 5 parallel agents covering SaaS architecture, framework extraction,
auth/accounts, RPA pivot, and startup growth playbooks.
Date: 2026-03-02 | Branch: claude/research-architecture-planning-fMyOM

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Strategic Options](#strategic-options)
3. [Option A: SaaS Game Bot Platform](#option-a-saas)
4. [Option B: Game Automation Framework/SDK](#option-b-sdk)
5. [Option C: RPA/Business Automation Pivot](#option-c-rpa)
6. [Common Infrastructure (All Options)](#common-infra)
7. [Growth Playbook](#growth-playbook)
8. [Financial Projections](#financials)
9. [Recommended Path](#recommendation)

---

## 1. Executive Summary <a id="executive-summary"></a>

9Bot's technology stack -- OpenCV template matching, EasyOCR, ADB automation, state-machine
navigation, adaptive timing, protocol interception, web dashboard, relay tunnel -- is
significantly more valuable than a single-game bot. It maps directly to three business
opportunities of increasing ambition:

| Option | Revenue Ceiling | Time to First Revenue | Risk | Tech Overlap |
|--------|----------------|----------------------|------|-------------|
| **A. SaaS Game Bot** | $3-5M/year | 1-3 months | Low | 95% |
| **B. Game SDK + Marketplace** | $1-3M/year | 6-12 months | Medium | 85% |
| **C. RPA/Business Automation** | $10M+/year | 3-6 months | Medium-High | 90% |

All three options build on the same core technology. The key insight: **9Bot is already an
RPA tool that happens to automate a game.** The vision pipeline, state machine, adaptive
timing, error recovery, and web dashboard are domain-agnostic infrastructure.

---

## 2. Strategic Options <a id="strategic-options"></a>

### The Three Paths

**Option A: Host 9Bot as SaaS** — The simplest path. Pack emulators on GPU servers, users
get a web dashboard instead of running locally. Minimal code changes. Revenue from hosting
margins.

**Option B: Extract a Game Bot SDK** — Extract the reusable infrastructure into an open-source
framework. Build a marketplace where developers sell "game packs" (templates + actions for
specific games). Revenue from premium features + marketplace commission.

**Option C: Pivot to Business RPA** — Replace ADB with pyautogui/Playwright, keep everything
else. Sell to small businesses automating invoices, data entry, web scraping. Revenue from
cloud dashboard subscriptions.

### They're Not Mutually Exclusive

The recommended path is **A first (immediate revenue), then B (moat), then evaluate C
(ceiling)**. Each builds on the previous:

```
Month 1-6:   Option A — SaaS game bot, validate cloud hosting
Month 6-12:  Option A + B — Extract SDK while building second game bot
Month 12-18: A + B + evaluate C — If SDK is successful, test RPA vertical
```

---

## 3. Option A: SaaS Game Bot Platform <a id="option-a-saas"></a>

### Cloud Architecture

**Recommended: Hybrid (GPU dedicated servers, multiple users per box)**

```
Dedicated Server ($60-180/month with GPU)
  ├── LDPlayer Instance 1 (User A)
  ├── LDPlayer Instance 2 (User B)
  ├── LDPlayer Instance 3 (User C)
  ├── 9Bot Instance 1 (serving User A)
  ├── 9Bot Instance 2 (serving User B)
  ├── 9Bot Instance 3 (serving User C)
  └── Orchestrator Service (provisions, monitors, health checks)
```

| Server Tier | Monthly Cost | Emulators | Cost/User |
|------------|-------------|-----------|-----------|
| GTX 1650 (64GB) | $60/mo | 6 | $10.00 |
| RTX 2060 (128GB) | $68-160/mo | 12 | $5.67-13.33 |

Providers: GPU-Mart ($59-160/mo), CloudClusters, DatabaseMart.

**Phase 1 (Now)**: Manual provisioning, 5-10 cloud users on 1-2 servers.
**Phase 2 (3-6mo)**: Automated provisioning with server inventory manager.
**Phase 3 (12mo+)**: Evaluate Anbox Cloud / Android Cuttlefish for 10x density.

### Pricing Tiers

| Tier | Price | Accounts | Margin |
|------|-------|----------|--------|
| Starter (self-hosted) | $15/mo | Unlimited | 100% (no hosting) |
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

### Multi-Tenant Architecture

**Shared database with `tenant_id`** (Pool Model):

```sql
users (id, email, password_hash, stripe_customer_id, plan, created_at)
devices (id, user_id, device_alias, emulator_instance, server_id, status)
settings (device_id, key, value)  -- replaces settings.json
bot_sessions (id, device_id, started_at, ended_at, task_type, stats_json)
bot_logs (id, device_id, timestamp, level, message)
```

Runtime isolation: each user gets their own process (maps to 9Bot's existing per-device
thread model). Settings move from `settings.json` to database. Status moves to Redis.

### Authentication & Accounts

**Recommended: Discord OAuth primary, Google secondary, using Authlib.**

Why Discord: Game bot users live on Discord. Removes password management burden.
No custom auth system -- use Authlib library with Flask-Session (server-side sessions).

**Feature gating by tier:**
```python
FEATURE_GATES = {
    "multi_device": "pro",          # 3+ devices
    "protocol_interception": "pro", # Frida fast paths
    "relay_tunnel": "basic",        # Remote access
    "api_access": "premium",        # REST API
}
```

**Migration path**: Dual-mode for 3 months (both license keys and accounts work),
then link keys to accounts, then accounts required. Grandfather existing users.

### Payment Processing

**Layered approach (risk mitigation):**

1. **Paddle (primary for cloud tiers)** — Merchant of Record, handles tax/compliance globally.
   Higher fees (5% + $0.50) but legal buffer since they are the seller of record.
2. **Crypto (secondary)** — NOWPayments (0.5% fees). No chargeback risk. No account freezes.
   Offer 10-15% discount for crypto payments.
3. **Stripe (Starter tier only)** — Software licensing is lower risk. Position as "automation
   software."
4. **BTCPay Server (backup)** — Self-hosted, zero fees. Failsafe if all processors fail.

**Stripe risk**: Game bots are gray-area. Stripe can freeze funds for 90+ days. Do NOT use
Stripe as sole processor for cloud hosting services.

### Admin Dashboard

**Retool** for admin (connects to PostgreSQL, build in 2-3 days). User-facing dashboard
remains the Flask app.

### API Design

Formalize existing `web/dashboard.py` endpoints for third-party use:
```
POST /api/v1/auth/login → JWT
GET  /api/v1/devices → list user's game accounts
POST /api/v1/devices/{id}/start → start bot
GET  /api/v1/devices/{id}/status → current status
GET  /api/v1/devices/{id}/screenshot → JPEG
POST /api/v1/webhooks → register callback URL
```

Webhook events: `bot.started`, `bot.quest.completed`, `bot.rally.joined`, `bot.ap.low`, etc.
Maps naturally to existing `protocol/events.py` EventBus.

---

## 4. Option B: Game Automation Framework/SDK <a id="option-b-sdk"></a>

### The Framework Extraction Boundary

| Reusable (SDK) | Game-Specific (Game Pack) |
|---------------|-------------------------|
| `vision.py` (screenshot, find_image, OCR) | `IMAGE_REGIONS`, `TAP_OFFSETS` values |
| `navigation.py` (state machine engine) | Screen enum, templates, nav graph |
| `devices.py` (ADB management) | Nothing |
| `runners.py` (task launching/stopping) | Task function registry |
| `web/dashboard.py` (skeleton) | Page content, auto-mode defs |
| `protocol/` (Frida, protobuf decoder, event bus) | wire_registry.json, proto_field_map.json |
| `botlog.py` (all of it) | Nothing |
| `settings.py` (load/save pattern) | Settings keys |

### How to Extract

**Do NOT design in the abstract.** Build a second game bot (Lords Mobile, Whiteout Survival)
using the 9Bot codebase. Every copy-paste reveals the framework boundary.

This is the proven pattern (Rails from Basecamp, Shopify from a snowboard store):
1. Build second game bot on same codebase
2. Extract common code into shared package
3. Ship SDK with two working game packs as proof

### Plugin Architecture (3 Layers)

1. **Declarative (YAML/JSON)** — Screen defs, templates, nav graphs, popups
2. **Python API (subclass GamePack)** — Complex multi-step sequences with logic
3. **Raw Access** — Direct OpenCV, ADB, Frida for edge cases

### SDK Developer Experience

```bash
pip install gamebot-sdk
gamebot init my-game-pack      # scaffold project
gamebot capture --device ...   # interactive template capture
gamebot test-match element.png # test matching offline
gamebot run my-game-pack       # run with dashboard
gamebot pack my-game-pack      # package for distribution
```

Critical DX features (ranked):
1. **Template Capture Tool** — Interactive screenshot + crop + threshold test
2. **Offline Replay/Testing** — Record sessions, replay against logic without emulator
3. **Visual Debugger** — Real-time overlay showing matches, scores, state
4. **Hot Reload** — Change template, bot picks it up without restart
5. **Structured Logging** — StatsTracker + timed_action generalized for any game

### Marketplace Model

| Revenue Split | Commission |
|--------------|-----------|
| First $10K | 0% (attract developers, build catalog) |
| Above $10K | 15% |
| Listing fee | $0 |

At steady state: 20 game packs x 500 users x $12/mo = $120K/mo GMV, $18K/mo platform revenue.

### Licensing

**Apache 2.0 for SDK core, proprietary for premium features.**

Why not AGPL: Game bot developers build desktop tools, not SaaS. AGPL's network clause
doesn't bite. It would just scare developers away.

Why Apache 2.0: Maximum adoption, patent grant, compatible with everything, no corporate fear.

Commercial revenue from: multi-device, relay tunnel, protocol interception, marketplace
commission, cloud hosting, support.

### Gap in the Market

No one provides a developer-grade SDK with template matching, OCR, nav state machines,
multi-device threading, protocol interception, and web dashboard. GnBots/BoostBot sell
finished products. Airtest (9.2K stars) is for QA testing, not game automation. Graor's
Bot Maker is a basic macro recorder. The SDK would fill a real gap.

---

## 5. Option C: RPA/Business Automation Pivot <a id="option-c-rpa"></a>

### Market Size

The RPA market is $8-35B in 2026 (varies by scope) growing at 20-44% CAGR.

- UiPath: ~29% share, $1.3B revenue
- Automation Anywhere: ~18% share
- Microsoft Power Automate: rising fast

**The underserved gap**: Small businesses ($50-500/mo budget) and freelancers. UiPath starts
at $420/mo for serious use. Power Automate's desktop automation is $40/mo but limited. The
jump from $40 to $420 is where a Python-based tool fits.

### Technology Overlap: 90-95%

| 9Bot Component | RPA Equivalent | Overlap |
|---------------|---------------|---------|
| `vision.py` screenshot + find_image | Screen capture + template matching | 100% |
| `vision.py` read_text, read_number | OCR / IDP (document processing) | 100% |
| `vision.py` adb_tap, adb_swipe | Mouse click, keyboard (pyautogui) | 85% |
| `vision.py` timed_wait | Smart waits for UI transitions | 100% |
| `navigation.py` check_screen | Screen/state recognition | 100% |
| `navigation.py` navigate | Workflow orchestration | 100% |
| `navigation.py` _recover_to_known | Exception recovery | 100% |
| `botlog.py` StatsTracker | Audit logging, metrics | 100% |
| `web/dashboard.py` | Control room / orchestration | 95% |
| `runners.py` task launching | Bot orchestration, scheduling | 100% |
| `tunnel.py` relay | Remote bot management | 100% |

**What changes**: Replace ADB calls with pyautogui/mss (~200-400 lines). Add Playwright
for browser automation (~500-800 lines). Everything else works as-is.

### Competitive Advantage

1. **Code-first (Python)** — Developers write Python, not proprietary workflow languages
2. **Lightweight** — pip install, not 2GB IDE
3. **Cross-platform** — macOS support (already have Apple Vision OCR)
4. **No vendor lock-in** — Workflows are Python scripts
5. **10x cheaper** — $49-149/mo vs UiPath's $420/mo
6. **Battle-tested** — Game UI automation is HARDER than business UI (less predictable)

### Pricing

| Tier | Price | What |
|------|-------|------|
| Free | $0 | Open source engine, local execution |
| Starter | $49/mo | Cloud dashboard, scheduling, 5 bots |
| Pro | $149/mo | Unlimited bots, advanced OCR, team |
| Business | $499/mo | Priority support, SSO, SOC2, SLA |

### High-Value Use Cases

1. **Invoice processing** — OCR extract fields → enter into accounting system
2. **Data entry between systems** — Read legacy app (screenshot+OCR) → enter in web app
3. **Report generation** — Navigate dashboard → screenshot → extract → compile
4. **Form filling** — Read spreadsheet → navigate web form → fill → submit
5. **CRM updates** — Read leads → navigate CRM → create/update records

### Compliance

- SOC 2 NOT needed initially (sell to SMBs first, $20-50K for certification later)
- GDPR: on-premises/self-hosted option satisfies data residency requirements
- Audit trails: extend existing StatsTracker + timed_action

### Vertical Opportunity

General RPA is a race to the bottom. **Vertical specialization** is where small companies win.
Recommended starting vertical: **accounting/bookkeeping** (largest RPA use case, universal
need, clear ROI calculation).

### Precedent

Robocorp raised $21M Series A on exactly this thesis (open-source Python RPA). n8n (fair-code
automation) hit $40M ARR with 67 employees. The model is proven and VC-fundable.

---

## 6. Common Infrastructure (All Options) <a id="common-infra"></a>

### Database (Replace settings.json)

Start with **SQLite via Flask-SQLAlchemy** (zero deployment overhead). Switch to PostgreSQL
when you outgrow it (just change the URI).

```python
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    discord_id = db.Column(db.String, unique=True)
    email = db.Column(db.String)
    stripe_customer_id = db.Column(db.String)
    tier = db.Column(db.String, default="free")

class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    device_id = db.Column(db.String)
    alias = db.Column(db.String)
    settings = db.Column(db.JSON)
```

### Stripe Integration

- **Stripe Checkout** for payment page (hosted by Stripe, zero PCI burden)
- **Stripe Billing Portal** for self-service subscription management
- **Webhooks** at `/stripe/webhook` for payment events
- Events: `checkout.session.completed`, `customer.subscription.deleted`, `invoice.payment_failed`

### Feature Gating Pattern

```python
@require_tier("pro")
@app.route("/api/v1/devices/<id>/protocol")
def toggle_protocol(id):
    ...
```

Task launching checks tier before spawning threads.

### Monetization Strategy

**Reverse trial** (7-day Premium on signup → downgrade to free → upgrade prompts).
Industry data: 15-25% conversion rate, benefits from loss aversion.

---

## 7. Growth Playbook <a id="growth-playbook"></a>

### Community-Led Growth (Discord)

**Phase 1 (0-100 members)**: Invite every early user personally. Be extremely active.
Respond to every message within hours.

**Phase 2 (100-500)**: Server partnerships with complementary communities. User-generated
content. Weekly Q&A events.

**Phase 3 (500-1000+)**: Appoint moderators. Champions/Power Users role with early access.
Community-driven roadmap.

### Content Strategy (1-Person Team)

- **2 YouTube videos/month** (highest ROI for technical tools)
- **1 blog post/week** (SEO compounds, repurpose video content)
- **Daily social posts** (X/Twitter, building in public)
- **Weekly Discord update** (dev progress, what's coming)

### Product-Led Growth Benchmarks

| Metric | Baseline | Good | Excellent |
|--------|----------|------|-----------|
| Free → Paid Conversion | 9% | 15% | 24%+ |
| Time to Value | 30 min | 5 min | < 1 min |
| Activation Rate | 25% | 40% | 64% |
| Monthly Churn | 8% | 5% | 3% |
| Net Revenue Retention | 100% | 110% | 120%+ |

### Pricing Psychology

- **Three tiers** convert 20-35% better than single tier
- **Price anchoring**: Show expensive tier first ($99 makes $39 feel cheap)
- **Left-digit effect**: $29/mo converts 8% better than $30/mo
- **Annual discount**: 2 months free (17% off) improves cash flow, reduces churn
- **Per-device pricing**: Natural scaling with customer value

### Early Revenue: $0 → $1K MRR

**Week 1-4**: Direct DMs to community members. Personal onboarding via Discord voice.
**Month 2-3**: Community presence (forums, subreddits, Discord servers).
**Month 3-6**: Content flywheel (YouTube tutorials, blog posts, SEO).
**Month 6+**: Affiliate program (10-20% recurring), partnerships.

### Churn & Retention (Game Bot Specific)

Expected: **10% monthly churn** (higher than SaaS average due to game burnout + ban risk).
Average customer lifetime: 10 months.

Key retention strategies:
1. **Instant game-update response** — Push bot update within hours of game patch
2. **Visible value metrics** — "847 rallies joined this month" in dashboard
3. **Protocol interception reliability** — 10-100x more reliable than pure-vision competitors
4. **Annual pricing** — 2 months free locks users past high-churn first 3 months
5. **Multi-account stickiness** — Users with 3+ accounts rarely churn (high switching cost)
6. **Dunning recovery** — Smart retries for failed payments (2-4% churn reduction)

### Scaling Stages

| Stage | Customers | What Changes |
|-------|-----------|-------------|
| 1-10 | Validation | You are everything. Personal Discord DMs. |
| 10-100 | PMF | FAQ docs, video tutorials, community self-help |
| 100-500 | $10-50K MRR | First hire (community manager). Ticketing system. |
| 500+ | $50K+ MRR | Second hire (developer). CI/CD, staging, monitoring. |

**First hire at $10-15K MRR** (community/support). **Second hire at $20-30K MRR** (developer).

---

## 8. Financial Projections <a id="financials"></a>

### Option A: SaaS Game Bot

**100 Cloud Users:**
- Revenue: 60 x $29 + 30 x $59 + 10 x $99 = $4,500/mo
- Infrastructure: 9 RTX servers + DB + monitoring = $1,590/mo
- **Gross margin: 64.7%**

**1,000 Cloud Users:**
- Revenue: $45,000/mo
- Infrastructure + staff: $16,940/mo
- **Gross margin: 62.4%**

**Breakeven**: ~16 cloud users covers fixed costs ($500/mo base).

### Option B: SDK + Marketplace

| Year | Revenue Sources | Est. Annual |
|------|----------------|------------|
| 1 | 9Bot subscriptions + SDK early adopters | $200-500K |
| 2 | Two game bots + SDK Pro licenses + early marketplace | $500K-1M |
| 3 | Marketplace commissions + cloud hosting | $1-3M |

### Option C: RPA

| Year | Customers | Avg. Revenue | Annual |
|------|-----------|-------------|--------|
| 1 | 100 | $99/mo | $119K |
| 2 | 500 | $129/mo | $774K |
| 3 | 2,000 | $149/mo | $3.6M |

At $1M+ ARR, the company is VC-fundable if desired.

### Revenue Per Path ($0 → $50K MRR Roadmap)

| Month | What | MRR Target |
|-------|------|-----------|
| 1-3 | Ship, sell, personal onboarding | $500 |
| 3-6 | Community, content, first affiliates | $3K |
| 6-12 | SEO compounds, word of mouth | $10K |
| 12-18 | First hire, systemize, partnerships | $25K |
| 18-24 | Second hire, enterprise tier, multiple processors | $50K |

**Median indie project: $500/mo. Only 10% break $10K/mo. Takes 18-36 months.**

---

## 9. Recommended Path <a id="recommendation"></a>

### Phase 1: Validate Cloud Hosting (Months 1-3)
- Keep selling Starter licenses (100% margin)
- Manually set up 5-10 Cloud Basic users on GPU-Mart servers ($29/mo each)
- Add Discord OAuth + basic account system to web dashboard
- Integrate Paddle for cloud tier payments
- **Goal**: Prove users will pay for hosted service. $500+ MRR.

### Phase 2: Automate + Second Game (Months 3-9)
- Build provisioning automation (server inventory, emulator creation)
- Start building a second game bot (Lords Mobile / Whiteout Survival)
- Extract common code into shared package as the second bot progresses
- Launch Cloud Basic, Pro, Premium tiers
- **Goal**: 100 cloud users, $4,500 MRR.

### Phase 3: SDK + Marketplace (Months 9-18)
- Ship gamebot-sdk with two working game packs
- Open-source core (Apache 2.0), commercial premium features
- Launch marketplace (0% commission initially)
- Formalize REST API with JWT auth, add webhook system
- Admin dashboard (Retool)
- **Goal**: 500+ users, $20-45K MRR.

### Phase 4: Evaluate RPA / Scale (Months 18+)
- If SDK gains traction, evaluate RPA vertical (accounting/bookkeeping)
- Replace ADB with pyautogui/Playwright (~1000 lines of new code)
- Target small businesses and freelancers
- **Goal**: $100K+ MRR or $1M+ ARR.

### The Uncomfortable Truth

- Building in public and community-first growth WORK but compound slowly
- The work you do now has a 6-12 month delayed payoff
- Most projects die at month 12 when growth stalls -- persistence is the differentiator
- 9Bot's competitive advantages (protocol interception, adaptive timing, 852 tests,
  web dashboard + relay) are REAL and meaningfully ahead of competitors

### The Bottom Line

9Bot is not just a game bot -- it's a complete automation platform disguised as one.
The technology stack is worth significantly more than its current single-game application.
Whether through SaaS hosting, SDK extraction, or RPA pivot, there are multiple viable
paths to turn this into a sustainable business with strong margins.

---

## Sources

### SaaS Architecture & Hosting
- [GPU-Mart Pricing](https://www.gpu-mart.com/pricing)
- [GPU-Mart RTX 2060](https://www.gpu-mart.com/rtx-2060-hosting)
- [CloudClusters Emulator VPS](https://www.cloudclusters.io/emulator)
- [Canonical Anbox Cloud](https://canonical.com/anbox-cloud)
- [Running Android on Kubernetes](https://realz.medium.com/running-android-on-kubernetes-be73b940833f)

### Multi-Tenant & Auth
- [Multi-Tenant Architecture Guide](https://supertokens.com/blog/multi-tenant-architecture)
- [AWS Tenant Isolation](https://docs.aws.amazon.com/whitepapers/latest/saas-architecture-fundamentals/tenant-isolation.html)
- [Stripe Usage-Based Billing](https://docs.stripe.com/billing/subscriptions/usage-based)

### Payments & Compliance
- [Stripe vs Paddle](https://designrevision.com/blog/stripe-vs-paddle)
- [Stripe Restricted Activities](https://webzeto.com/stripe-restricted-business-activities/)
- [NOWPayments Crypto Processing](https://nowpayments.io/blog/risk-merchant-providers-gateways)
- [SOC 2 Compliance Cost](https://www.complyjet.com/blog/soc-2-compliance-cost)

### Competitors
- [GnBots](https://www.gnbots.com/shop/download/)
- [BoostBot](https://boostbot.org/)
- [BotSauce](https://botsauce.org/)
- [Graor Bot Maker](https://graor.com/game-bot)
- [MuBots](https://mubots.io/)

### Framework & SDK
- [Airtest GitHub](https://github.com/AirtestProject/Airtest)
- [Shopify App Store Revenue Share](https://shopify.dev/docs/apps/launch/distribution/revenue-share)
- [Rails Origin Story - DHH](https://world.hey.com/dhh/the-origin-of-ruby-on-rails-b3dab24e)

### RPA Market
- [Precedence Research RPA Market](https://www.precedenceresearch.com/robotic-process-automation-market)
- [Fortune Business Insights RPA](https://www.fortunebusinessinsights.com/robotic-process-automation-rpa-market-102042)
- [Robocorp Raises $21M](https://www.techtarget.com/searchenterpriseai/news/252502803/Open-source-RPA-vendor-Robocorp-raises-21-million)
- [UiPath Pricing](https://www.uipath.com/pricing)
- [Python RPA vs UiPath](https://pythonrpa.org/why-python-rpa-is-the-better-uipath-alternative/)

### Growth & Marketing
- [PLG Benchmarks](https://productled.com/blog/product-led-growth-benchmarks)
- [Inside Supabase Growth](https://www.craftventures.com/articles/inside-supabase-breakout-growth)
- [n8n Revenue ($40M ARR)](https://getlatka.com/companies/n8nio)
- [Subscription Churn Reasons](https://www.revenuecat.com/blog/growth/subscription-app-churn-reasons-how-to-fix/)
- [Discord Community Growth 2025](https://www.influencers-time.com/discord-community-growth-guide-for-2025-success/)
- [SaaS Pricing Psychology](https://dodopayments.com/blogs/pricing-psychology)
- [Building in Public Guide](https://buildvoyage.com/articles/building-in-public-guide)

### Legal
- [Game Bots Under Copyright Law](https://www.lexology.com/library/detail.aspx?g=f2d4195a-1210-4505-b39c-dae75b29a979)
- [Responsible Use Policy (Qontinui)](https://qontinui.io/responsible-use)
