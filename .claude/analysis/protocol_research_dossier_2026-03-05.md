# Protocol Research Dossier (2026-03-05)

## Scope
Research-only snapshot of current protocol capabilities in 9Bot, with emphasis on:
- What is already decoded and tracked
- What is already consumed by automations/UI
- What is decoded but not yet operationalized
- What to research next, ranked by effort/value/risk

Primary sources:
- `protocol/interceptor.py`
- `protocol/events.py`
- `protocol/game_state.py`
- `protocol/messages.py`
- `protocol/proto_field_map.json`
- `protocol/wire_registry.json`
- `startup.py`
- `vision.py`
- `troops.py`
- `actions/rallies.py`
- `runners.py`
- `web/dashboard.py`
- `ROADMAP.md`

## Executive Summary
- Protocol interception is production-integrated and per-device.
- Decoder surface is large (`wire_registry.json`: 4,169 IDs), but only a small subset is currently routed into automations.
- Current high-confidence operational fast paths are AP, rallies (early bail-out), troops home count, lineup snapshot, and chat.
- Event bus and raw message routing already support significantly broader protocol-driven automation without changing the Frida pipeline.
- Best near-term research upside: defense/attack intelligence, timer-driven march orchestration, and alliance workflow expansion.

## Capability Matrix

### A) Interception and decode substrate
| Capability | Current state | Evidence |
|---|---|---|
| Hook point | NetMsgData `FromByte` (recv) + `MakeByte` (send) | `protocol/frida_hook.js`, `protocol/interceptor.py` |
| Directionality | Inbound and outbound decoded | `ProtocolInterceptor._handle_recv/_handle_send` |
| Message ID registry scale | 4,169 wire IDs | `protocol/wire_registry.json` |
| Schema map scale | 3,770 mapped message/type entries | `protocol/proto_field_map.json` |
| Typed dataclass coverage | 43 typed message/leaf classes currently in `MESSAGE_CLASSES` | `protocol/messages.py` |
| Semantic event routing | 15 default high-value routes (`DEFAULT_ROUTING_TABLE`) | `protocol/events.py` |
| Raw message access | Every decoded message emits `msg:{MessageName}` | `MessageRouter.route()` |

### B) Stateful protocol model (GameState)
| State bucket | Freshness category | Populated by |
|---|---|---|
| AP/power | `ap` | `PowerNtf`, `AssetNtf` |
| Rallies | `rallies` | `RallyNtf`, `RallyDelNtf` |
| Quests | `quests` | `QuestChangeNtf`, `QuestsNtf` |
| Resources | `resources` | `AssetNtf` |
| World entities | `entities` | `EntitiesNtf`, `PositionNtf`, `DelEntitiesNtf`, `UnionEntitiesNtf` |
| Attack intel | `attacks` | `IntelligencesNtf` |
| Chat | `chat` | `ChatOneMsgNtf`, `ChatPullMsgAck`, send/notify pairing |
| Buffs | `buffs` | `BuffNtf` |
| Troop lineups/states | `lineups` | `LineupsNtf`, `NewLineupStateNtf` |
| Clock heartbeat | `heartbeat` | `HeartBeatAck` |

### C) Startup accessors and TTL contracts
| Accessor | TTL gate | Output |
|---|---|---|
| `get_protocol_ap()` | `ap <= 10s` | `(current, max)` or `None` |
| `get_protocol_rallies()` | `rallies <= 30s` | `list`, `[]` (explicit none), or `None` |
| `get_protocol_troops_home()` | `lineups <= 30s` | home troop count or `None` |
| `get_protocol_troop_snapshot()` | `lineups <= 30s` | typed `DeviceTroopSnapshot` or `None` |
| `get_protocol_ally_cities()` | `entities <= 60s` | ally city list or `None` |
| `get_protocol_chat_messages()` | no TTL gate | recent chat list |
| `get_protocol_event_bus()` | active device only | `EventBus` |

## Coverage Gap Analysis

### In production use now
| Consumer | Protocol usage |
|---|---|
| `vision.read_ap()` | Protocol AP fast path before OCR fallback |
| `troops.troops_avail()` | Protocol home-count fast path before template matching |
| `troops.read_panel_statuses()` | Protocol lineup snapshot before icon matching |
| `actions.rallies.join_rally()` | Protocol rally pre-filter / early bail-out |
| `runners.run_auto_reinforce_ally()` | Event-driven ally city spotting via `EVT_ALLY_CITY_SPOTTED` |
| `web/dashboard.py` chat APIs | Protocol chat feed + cross-device mirroring |
| Debug UI | Per-device protocol toggle + status API |

### Decoded/tracked but underused (high research potential)
| Domain | Current tracking exists | Current product use |
|---|---|---|
| Incoming attack intel | Yes (`IntelligencesNtf`) | Not wired to defense automation |
| Battle outcomes | Yes (`BattleResultNtf`) | Not used for adaptive logic |
| Burning state | Yes (`CombustionStateNtf`) | Not used for automated response |
| Buff state | Yes (`BuffNtf`) | Not used for policy gating |
| Quest state stream | Yes (`QuestChangeNtf`, `QuestsNtf`) | Limited direct protocol quest flow |
| World entities movement | Yes (`Entities/Position/DelEntities`) | Primarily ally reinforcement path |
| Outgoing message intent | Yes (`send` decode path, chat send pairing) | Minimal orchestration use |

### Large schema areas not yet surfaced to typed/runtime workflows
Observed in `proto_field_map.json` and/or wire registry names:
- Mail and mailbox operations
- Building/research/healing lifecycles
- Teleport/watchtower detail flows
- Broader alliance/union operations and records
- Additional boss/event-specific combat domains

Research implication: protocol surface is much larger than current typed class set and routed event set; expansion is mostly modeling and handler work, not interceptor redesign.

## Opportunity Backlog (Research-First Ranking)
Scoring: 1 (low) to 5 (high). Implementation complexity is inverted value (5 = easiest).

| Opportunity | Automation value | Reliability vs vision | Complexity (ease) | Risk/ToS sensitivity | Research priority |
|---|---:|---:|---:|---:|---:|
| Attack defense engine (intel + burn + buffs) | 5 | 5 | 3 | 3 | 1 |
| Timer-accurate march orchestration from lineup end times | 5 | 5 | 3 | 2 | 2 |
| Protocol-native quest triggering | 4 | 4 | 3 | 2 | 3 |
| Alliance operations expansion (beyond reinforce-ally) | 4 | 4 | 2 | 3 | 4 |
| Battle-result adaptive strategy loop | 4 | 4 | 2 | 2 | 5 |
| Resource economy policy engine | 3 | 4 | 3 | 1 | 6 |
| Mail/watchtower driven alerting | 3 | 3 | 2 | 2 | 7 |

## Recommended Research Tracks

### Track 1: Message Coverage Mapping
Goal: map high-frequency runtime traffic to present-day automations and identify highest-value unconsumed message families.

Steps:
1. Instrument per-message counters by `msg_name` and direction during representative sessions.
2. Bucket by domain (combat, movement, alliance, economy, chat, system).
3. Identify top 20 message names by frequency and top 20 by automation relevance.

Output:
- Frequency heatmap by message family
- Candidate shortlist for typed model expansion

### Track 2: Stability Across Game Updates
Goal: verify resilience of hooks, message IDs, and field extraction assumptions.

Steps:
1. Run capture set before and after game update.
2. Compare unknown-ID rate, decode failure rate, and field-level null-rate for target messages.
3. Record any drift in routing assumptions and dataclass field expectations.

Output:
- Version compatibility report
- “Break risk” index per candidate automation

### Track 3: Defense Automation Feasibility
Goal: determine whether protocol-only defense triggers can replace screenshot polling decisions.

Steps:
1. Capture scenarios: incoming attack warning, city burning on/off, shield buff transitions.
2. Validate event ordering and latency from receive to state update.
3. Define deterministic trigger logic and false-positive controls.

Output:
- Trigger spec with confidence levels
- Minimal implementation contract (events + cooldown rules)

## Validation Methodology (Repeatable)
Use fixed scenario scripts on a test account and collect:
- Interceptor stats: msg counts, bytes, errors, unknown IDs
- State freshness metrics by category
- Event latency (message recv timestamp to handler execution)
- Fallback rate (protocol returned `None` and vision path engaged)

Suggested scenario set:
1. Idle city (baseline heartbeat/resources)
2. Rally creation/join/end cycle
3. Troop deployment and return (lineup state transitions)
4. Chat world/alliance send+pull with mirrored device
5. Map entity spawn/move/despawn near alliance cities
6. Controlled incoming attack / watchtower warning
7. Buff/shield activation and expiration

## Open Questions
- Which specific message families are most stable across patch versions in practice, beyond currently routed core notifications?
- Should typed class expansion prioritize breadth (many messages shallowly) or depth (few messages with strongly-typed nested fields)?
- At what point does protocol-driven control move from assistive optimization to full protocol-native execution risk?

## Immediate Next Research Deliverables
1. `protocol_message_frequency_baseline.md` with one-session and multi-session histograms.
2. `protocol_candidate_messages_top20.md` with value/complexity notes.
3. `protocol_update_compat_matrix.md` comparing at least two game versions.

