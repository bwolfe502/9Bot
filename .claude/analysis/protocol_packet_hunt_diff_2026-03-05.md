# Packet Hunt Diff (2026-03-05 11:32 PST)

Session: `stats/protocol_baseline/session_20260305_1132_packet_hunt` (120 snapshots, ~10 min)

## Session Metrics
| Session | Recv delta | Send delta | Avg msg/s | Peak msg/s | Error delta | Unique top10 names observed |
|---|---:|---:|---:|---:|---:|---:|
| Pre-update active | 236 | 47 | 3.73 | 4.53 | 0 | 20 |
| Post-update active | 902 | 295 | 3.37 | 3.85 | 0 | 16 |
| Packet hunt | 3386 | 803 | 4.23 | 4.58 | 0 | 13 |

## Newly Observed Message Names (vs prior active sessions)
| Message | Lower-bound increment | Snapshots seen (top10) |
|---|---:|---:|
| `WildMapViewAck` | 122 | 120 |
| `PositionNtf` | 15 | 18 |

## Top Growth During Packet Hunt (top10-limited)
| Message | Lower-bound increment | Snapshots seen (top10) |
|---|---:|---:|
| `CompressedMessage` | 717 | 120 |
| `EntitiesNtf` | 496 | 120 |
| `RallyNtf` | 484 | 120 |
| `DelEntitiesNtf` | 146 | 120 |
| `RedPointNtf` | 138 | 120 |
| `AssetNtf` | 126 | 120 |
| `WildMapViewAck` | 122 | 120 |
| `RedPointReq` | 66 | 66 |
| `RedPointAck` | 66 | 66 |
| `HeartBeatAck` | 18 | 54 |
| `HeartBeatReq` | 18 | 54 |
| `PositionNtf` | 15 | 18 |
| `HeroSkillPropNtf` | 0 | 102 |

## Interpretation
1. This run significantly expanded high-volume evidence windows for existing hotspot families.
2. New packet discovery is limited by top10 truncation in `stats.top_message_types`.
3. To discover long-tail packets, next step is exporting full `msg_type_counts` instead of only top10.
