# 9Bot Diagnostic Memory

Index of known issues and patterns discovered from session analysis.
Run `/analyze` to process new diagnostic data and update these files.
Last updated: 2026-03-02 (6 sessions: 4 macOS local, 2 Windows inbox)

## Topic Files

- [template_issues.md](template_issues.md) — Template matching scores, region misses, platform diffs
- [ocr_issues.md](ocr_issues.md) — OCR misreads, platform differences, quest parsing
- [timing_issues.md](timing_issues.md) — Transition budgets, ADB performance
- [action_patterns.md](action_patterns.md) — Action success rates, failure cascades
- [session_history.md](session_history.md) — Per-session metrics log
- [lessons.md](lessons.md) — Regression post-mortems and rules for future changes

## Critical Issues

### rally_titan — REVERTED 1502f45 regression [FIXED]
- Commit 1502f45 replaced blind tap with rally_button.png polling — template never matched (0/7).
- Reverted to pre-1502f45 blind-tap code: tap (540,900) → 1.5s wait → tap (420,1400).
- Restores ~13% success rate (was 0% with polling). Still low — titan walks off-center.
- **Lesson**: [lessons.md](lessons.md) — don't replace working blind taps with unverified templates

### join_rally — 0% success, cross-platform [CONFIRMED]
- macOS 26% (old session), now 0/4 in latest. Windows 0/8.
- `jr_detail_load` transition never met on either platform.
- **Details**: [action_patterns.md](action_patterns.md)

### PVP attack menu failure — 58x on Windows [CONFIRMED]
- Attack button not found after target(). Tower state changes between nav and tap.
- **Details**: [action_patterns.md](action_patterns.md)

## Template Issues

### rally_button.png — Never matched titan popup [REVERTED]
- `titan_popup_check`: 0/7 met. Template captured from dialog, not titan map popup.
- 1502f45 reverted — blind-tap restored. Template still exists but unused for titans.
- **Details**: [template_issues.md](template_issues.md)

### rally_titan_select.png — Degraded to 0.42 (12 misses) [CONFIRMED]
- 12 misses (best 0.418-0.423) vs 2 hits (0.866). Intermittent, not reliable.
- **Details**: [template_issues.md](template_issues.md)

### stationed.png — Region miss, 103x on Windows [CONFIRMED]
- IMAGE_REGIONS too narrow, full-screen fallback on every EG probe
- **Details**: [template_issues.md](template_issues.md)

### search.png — Matching at 0.666 on macOS [SUSPECTED]
- Below 0.8 threshold yet recorded as hit. Only seen on macOS.
- **Details**: [template_issues.md](template_issues.md)

### Unknown screen popups — 89x on Windows [CONFIRMED]
- Expedition Gold popup, shop/rewards screen not recognized
- **Details**: [template_issues.md](template_issues.md)

## Timing Issues

### nav_kingdom_to_map — Budget too tight, both platforms [CONFIRMED]
- 0-9% met rate, 1.0s budget. Needs 2.0s.
- **Details**: [timing_issues.md](timing_issues.md)

### nav_td_exit_to_map — Budget too tight [CONFIRMED]
- 71-78% met (macOS). Needs ~3.5s.
- **Details**: [timing_issues.md](timing_issues.md)

### Evil Guard — 6 transition budget overruns [CONFIRMED]
- All EG transitions systematically underbudgeted (1.0-2.5s budgets, actual 2.1-3.0s).
- **Details**: [timing_issues.md](timing_issues.md)

## Fixed Issues

### macOS OCR — Apple Vision drops `(` on titan counters [FIXED in dev]
- Paren insertion + name trim, gated to darwin
- **Details**: [ocr_issues.md](ocr_issues.md)
