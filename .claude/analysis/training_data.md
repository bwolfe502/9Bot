# Training Data Patterns

## Session 12: 2026-03-02 (2 JSONL files, 1440 entries)

### Volume
| Metric | Value |
|--------|-------|
| File 1 (td_20260302_190541.jsonl) | 292 KB, 1309 entries, 52 min |
| File 2 (td_20260302_195819.jsonl) | 29 KB, 131 entries, 3 min |
| Total JSONL | 321 KB |
| Total images | 29 files (11 in JSONL, 18 orphans) |
| Entry rate | 25-49/min |

### Type Distribution
| Type | Count | % |
|------|-------|---|
| screen | 1,052 | 73% |
| template | 377 | 26% |
| ocr | **0** | **0%** |

### Device Distribution
| Device | Entries | % |
|--------|---------|---|
| emulator-5554 | 1,166 | 81% |
| 127.0.0.1:5625 | 274 | 19% |
| 127.0.0.1:5635 | 0 (18 orphan images only) | 0% |

### Screen Detection (1,052 entries)

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

### Template Analysis (377 entries, 16 unique templates)

**High-concern (device-specific failures):**
| Template | Hit Rate | Issue |
|----------|----------|-------|
| mithril_return.png | 26% (5/19) | 100% miss on emulator-5554, 100% hit on :5625 |
| statuses/defending.png | 8% (1/12) | 100% miss on emulator-5554, hit on :5625 |

**Fragile:**
| Template | Hit Rate | Issue |
|----------|----------|-------|
| search.png | 100% (50/50) | All at exactly 66%, threshold 0.65, 1-point margin |
| back_arrow.png | 97% (84/87) | 3 near-misses at 65% (screen obstructed) |

**Expected misses (correct behavior):**
| Template | Hit Rate | Notes |
|----------|----------|-------|
| aq_claim.png | 32% | Misses = no quest to claim (correct) |
| heal.png | 29% | Misses = no injured troops (correct) |
| close_x.png | 82% | Misses = no popup (correct) |

**Reliable (100%):**
bl_button.png, target_menu.png, attack_button.png, depart_pvp.png, mithril_attack.png,
mithril_depart.png, detail_button.png, oneclickrecovery.png

### Near-Misses (conf 65-79%)
- **search.png**: 50 occurrences at exactly 66% (custom 0.65 threshold, so technically hits)
- **back_arrow.png**: 3 occurrences at exactly 65% (misses -- screen obstructed)

### Region Drift
Zero entries in JSONL. One orphan region_drift image on disk (emulator-5554, 20:01:37).

### Notable Gaps
1. **Zero OCR entries**: Training data collector not capturing OCR decisions
2. **18 orphan images**: From 127.0.0.1:5635 (17) + emulator-5554 (1), no JSONL entries
3. **aq_screen/war_screen tight margin**: 6-9 point gap, narrowest pair in the system
