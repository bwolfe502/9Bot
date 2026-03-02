# 9Bot Diagnostic Memory

Index of known issues and patterns discovered from session analysis.
Run `/analyze` to process new diagnostic data and update these files.
Last updated: 2026-03-02 (7 sessions: 4 macOS local, 2 Windows inbox, 1 Windows local)

## Topic Files

- [template_issues.md](template_issues.md) -- Template matching scores, region misses, platform diffs
- [ocr_issues.md](ocr_issues.md) -- OCR misreads, platform differences, quest parsing
- [timing_issues.md](timing_issues.md) -- Transition budgets, ADB performance
- [action_patterns.md](action_patterns.md) -- Action success rates, failure cascades
- [session_history.md](session_history.md) -- Per-session metrics log
- [training_data.md](training_data.md) -- Training data collector patterns
- [lessons.md](lessons.md) -- Regression post-mortems and rules for future changes

## Critical Issues

### join_rally -- 0% success, confirmed cross-platform [CRITICAL]
- Windows session 2026-03-02 (device 5635): 0/10 attempts, all "unknown" failures
- Average failure time ~15.3s per attempt, total 153.4s wasted
- `jr_scroll_down_settle` and `jr_scroll_up_settle`: 0/28 combined met -- scroll never settling
- `jr_detail_load` transition never met on any platform
- **Root cause**: Scroll-based rally finding flow is broken. Needs protocol fast-path or rework.
- **Details**: [action_patterns.md](action_patterns.md)

### rally_titan -- 69% success, titan_on_map_select still 0% [HIGH]
- Windows session 2026-03-02: 9/13 attempts (69%). 4 failures at ~1.4s each.
- `titan_on_map_select`: 0/10 met -- blind tap at (540,900) never hits after search.
- `titan_depart_settle`: 0/9 met -- depart confirmation never detected.
- When blind tap works, success follows. When titan walks off-center, fast 1.3-1.6s fail.
- **Details**: [action_patterns.md](action_patterns.md)

### LOGGED OUT -- 60x on device 5585 [HIGH]
- Device 127.0.0.1:5585 triggered 60 ATTENTION popup detections across session.
- Likely game disconnects or server kicks causing repeated login/popup cycles.
- **Details**: [action_patterns.md](action_patterns.md)

### PVP attack menu failure -- 22x "attack menu did not open" [CONFIRMED]
- 56 "PVP: attack menu did not open" warnings + 22 "pvp_attack returned failure"
- Tower state changes between navigation and tap.
- **Details**: [action_patterns.md](action_patterns.md)

## Template Issues

### search.png -- Matching at 66% on Windows [CONFIRMED, CROSS-PLATFORM]
- Windows session 2026-03-02: all 13 hits at exactly 66% confidence
- Used with custom threshold 0.65 -- only 1% headroom above threshold
- **Risk**: Very fragile. Any slight rendering change will break titan search flow.
- **Details**: [template_issues.md](template_issues.md)

### stationed.png -- Region miss, 65x on Windows [CONFIRMED]
- IMAGE_REGIONS too narrow, full-screen fallback on every EG probe
- **Details**: [template_issues.md](template_issues.md)

### mithril_return.png -- Borderline at 69% [NEW]
- 3 misses at 0.693 (below 0.8 threshold), 3 hits at 1.0
- Template works when present but misses when mithril not ready to return
- **Details**: [template_issues.md](template_issues.md)

### Mithril Dimensional Treasure screen -- UNKNOWN [NEW]
- Debug screenshot shows "Dimensional Treasure" screen detected as UNKNOWN
- This is the mithril mining zone selector -- no screen template exists for it
- **Details**: [template_issues.md](template_issues.md)

### rally_titan_select.png -- Stable at 87% on Windows [IMPROVED]
- Windows session: 10/10 hits at exactly 0.865 (was intermittent on macOS)
- 0 misses this session (was 12 misses on macOS previously)
- **Status**: Stable on Windows, still needs monitoring on macOS

## Timing Issues

### heal_* transitions -- All 0% met [KNOWN]
- All 4 heal transitions at 0/2 met. These are `lambda: False` waits (just sleeps).
- Not a real issue -- heal_all still succeeds 100% (19/19).
- **Details**: [timing_issues.md](timing_issues.md)

### Mithril transitions -- All 0% met [KNOWN]
- 9 mithril transitions all 0% met. Same `lambda: False` sleep pattern.
- mine_mithril still succeeds 100% (1/1).
- **Details**: [timing_issues.md](timing_issues.md)

### titan_search_menu_open -- 70% met [WATCH]
- 7/10 met at budget 1.5s. avg=1.00s, max=1.23s.
- 3 misses suggest occasional slow menu opens.
- **Details**: [timing_issues.md](timing_issues.md)

## Tunnel Issues

### Frequent disconnects and reconnects [NEW]
- 77 tunnel warnings across session
- 17 "no data in 90s" timeouts, 17 connection errors
- Multiple short-lived sessions (5-61s uptime before server close)
- **Details**: [timing_issues.md](timing_issues.md)

## Fixed Issues

### rally_titan_select.png on macOS -- 12 misses [FIXED on Windows]
- Was 12/14 misses on macOS. Windows shows 10/10 hits at 0.865.
- May be macOS-specific rendering issue. Cross-platform status unclear.

### nav_kingdom_to_map budget [FIXED]
- Previously 0-9% met at 1.0s budget. Now widened to 3s, 1/1 met at 1.69s.

### nav_td_exit_to_map budget [FIXED]
- Previously 71-78% met. Now widened to 3.5s, 11/11 met at avg 1.91s.
