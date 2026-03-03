# 9Bot Diagnostic Memory

Index of known issues and patterns discovered from session analysis.
Run `/analyze` to process new diagnostic data and update these files.
Last updated: 2026-03-02 (12 sessions: 4 macOS local, 2 Windows inbox, 6 Windows local)

## Topic Files

- [template_issues.md](template_issues.md) -- Template matching scores, region misses, platform diffs
- [ocr_issues.md](ocr_issues.md) -- OCR misreads, platform differences, quest parsing
- [timing_issues.md](timing_issues.md) -- Transition budgets, ADB performance
- [action_patterns.md](action_patterns.md) -- Action success rates, failure cascades
- [session_history.md](session_history.md) -- Per-session metrics log
- [training_data.md](training_data.md) -- Training data collector patterns
- [lessons.md](lessons.md) -- Regression post-mortems and rules for future changes

## Critical Issues

### join_rally -- 0% success, 215 attempts [CRITICAL]
- 0/215 across 7 devices, 5 sessions, both platforms
- `jr_detail_load` transition: 0/129 met (3s budget)
- Rallies fill faster than bot can tap -- 3 EG joins succeeded when rallies were fresh
- Protocol early bail-out works correctly (instant "no rallies" detection)
- **Details**: [action_patterns.md](action_patterns.md)

### recall_tower -- 21% success [HIGH, NEW]
- 3/14 attempts, 0/11 on emulator-5554
- Root cause: `statuses/defending.png` at 52-60% on emulator-5554 (needs 80%)
- Template is device-specific -- works on :5625 (100%), fails on emulator-5554
- **Details**: [action_patterns.md](action_patterns.md), [template_issues.md](template_issues.md)

### Memory spikes -- +5658 MB per check_quests [HIGH, NEW]
- 3 bot restarts in 43 minutes suggest OOM with 3+ concurrent devices
- RSS plateaus at ~6.1 GB with 6 active devices
- Spikes correlate with concurrent EasyOCR across multiple devices
- **Details**: [timing_issues.md](timing_issues.md)

## Template Issues

### search.png -- 66% confidence [CONFIRMED, CROSS-PLATFORM]
- 1% headroom above 0.65 threshold -- most fragile template in the system
- **Details**: [template_issues.md](template_issues.md)

### Device-specific template failures [NEW]
- `statuses/defending.png`: 0% on emulator-5554, 100% on :5625
- `mithril_return.png`: 0% on emulator-5554, 100% on :5625
- Both need recapture from emulator-5554 or multi-template approach
- **Details**: [template_issues.md](template_issues.md)

### aq_screen vs war_screen -- Tight margin [NEW, WATCH]
- Only 6-9 point gap (97-100% vs 91%) -- narrowest pair
- Not currently misclassifying but vulnerable to template degradation
- **Details**: [template_issues.md](template_issues.md)

## Timing Issues

### Budget overruns [WATCH]
- `verify_aq_screen`: avg 2.3s vs 2.0s budget -- needs 3.0s
- `nav_map_to_alliance`: avg 2.1s vs 2.0s budget -- needs 2.5s
- `nav_alliance_menu_load`: avg 1.1s vs 1.0s budget -- needs 1.5s
- **Details**: [timing_issues.md](timing_issues.md)

### LZ4 not installed [NEW, EASY FIX]
- ~954 noise warnings per session (88% of all warnings)
- `pip install lz4` eliminates noise and enables CompressedMessage decoding
- **Details**: [timing_issues.md](timing_issues.md)

## Tunnel Issues

### Constant 90s idle disconnects [ONGOING]
- 18+ reconnections per session, 77 tunnel warnings across 21 hours
- Missing server-side keepalive pings
- **Details**: [timing_issues.md](timing_issues.md)

## Action Rates Summary

| Action | Rate | Attempts | Trend |
|--------|------|----------|-------|
| join_rally | 0% | 215 | Stable (broken) |
| recall_tower | 21% | 14 | NEW |
| pvp_attack | 37% | 19 | Worse |
| rally_eg | 40% | 25 | NEW |
| occupy_tower | 44% | 18 | NEW |
| rally_titan | 72% | 197 | Improved |
| All others | 92-100% | 400+ | Stable |

## Fixed Issues

- rally_titan_select.png: Stable at 0.865 on Windows (50 hits, 0 misses)
- nav_kingdom_to_map budget: 100% met at 3.0s (was 0-9% at 1.0s)
- nav_td_exit_to_map budget: 100% met at 3.5s (was 71-78%)

## Training Data Gaps

- Zero OCR entries logged (1,440 total entries, 0 OCR)
- 18 orphan training images without JSONL entries
- **Details**: [training_data.md](training_data.md)
