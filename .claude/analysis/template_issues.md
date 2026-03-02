# Template Matching Issues

## search.png -- Low confidence match [CONFIRMED, CROSS-PLATFORM]
- **Platforms**: macOS (0.666), Windows (0.665)
- **Sessions**: 2026-03-01 (macOS), 2026-03-02 (Windows)
- **Score**: Consistently 66% on both platforms (13 hits on Windows, all at 0.665)
- **Position**: Always at (654, 1395), region [0,960,1080,1920]
- **Used in**: Titan rally flow (titan_select_to_search), AP restore flow
- **Custom threshold**: 0.65 in titans.py `wait_for_image_and_tap("search.png", ..., threshold=0.65)`
- **Risk**: Only 1.5% headroom above threshold. One of the lowest-confidence templates in the system.
- **Action needed**: Re-capture search.png with a tighter crop or from a cleaner state. The template
  likely includes too much surrounding UI context that varies slightly.

## stationed.png -- Region miss [CONFIRMED]
- **Platform**: Windows (65x region misses in current session, 103x in previous)
- **Sessions**: 2026-03-01, 2026-03-02
- **Found at**: Various Y positions outside IMAGE_REGIONS
- **Impact**: Every EG priest probe triggers full-screen fallback search. Performance waste.
- **Action needed**: Widen `IMAGE_REGIONS["stationed.png"]` to cover actual hit range

## mithril_return.png -- Borderline misses [NEW]
- **Platform**: Windows
- **Session**: 2026-03-02
- **Stats**: 3 misses at 0.693, 3 hits at 1.0
- **Pattern**: Misses when checking if mithril is ready for return (isn't ready), hits when actually ready
- **Risk**: Low -- the 0.693 score is a true negative (template not present, correct behavior)

## mithril_depart.png -- Region miss [CONFIRMED]
- **Platform**: macOS (previously), Windows (13x this session)
- **Session**: 2026-03-02
- **Found at**: Y range 715-1250 (4 hits across full range)
- **Position varies**: (493,715), (493,894), (493,1072), (493,1250)
- **Action needed**: IMAGE_REGIONS needs to cover full Y range 700-1300

## rally_button.png -- Never matched titan popup [REVERTED]
- **Platform**: macOS (likely cross-platform)
- **Sessions**: 2026-03-02 (macOS sessions)
- **Introduced**: Commit 1502f45 replaced blind tap at (420,1400) with rally_button.png polling
- **Stats**: `titan_popup_check` 0/7 met -- template NEVER matches on titan on-map popup
- **Root cause**: rally_button.png was captured from a dialog context, not the titan map popup
- **Resolution**: Reverted to pre-1502f45 blind tap. See [lessons.md](lessons.md) #001.

## rally_titan_select.png -- Platform-dependent [WATCH]
- **Windows**: Stable at 0.865 (10/10 hits, 0 misses in 2026-03-02 session)
- **macOS**: Intermittent (2 hits at 0.866 + 12 misses at 0.418-0.423 in previous sessions)
- **Used with**: threshold 0.5 (timed_wait) and 0.65 (wait_for_image_and_tap)
- **Status**: Reliable on Windows, needs investigation on macOS

## aq_claim.png -- Always misses [LOW]
- **Platform**: Windows
- **Session**: 2026-03-02
- **Stats**: 5 misses, best score 0.442 (far below 0.8 threshold)
- **Context**: Quest claim button check -- template simply not present (quests not ready to claim)
- **Risk**: None -- this is expected behavior (checking for claimable quests)

## heal.png -- Expected misses [NORMAL]
- **Platform**: Windows
- **Session**: 2026-03-02
- **Stats**: 19 misses (best 0.462-0.476), 2 hits at 1.0
- **Context**: heal_all checks for heal button; 19 misses are when no injured troops exist
- **Risk**: None -- working as designed. 100% confidence when button is present.

## close_x.png -- One miss, stable hits [NORMAL]
- **Platform**: Windows
- **Session**: 2026-03-02
- **Stats**: 1 miss at 0.534, 8 hits at 0.997-1.0
- **Positions**: Two distinct positions: (1005,499) and (991,450) -- two different close buttons
- **Risk**: None -- single miss is likely a transient overlay or wrong screen state

## war_screen.png -- Bimodal scores [LOW]
- **Platform**: Windows
- **Session**: 2026-03-02
- **Stats**: 44 hits, scores bimodal at 0.94 and 1.0
- **Risk**: Low -- 0.94 is well above threshold. The bimodal pattern suggests minor UI variation
  (e.g., war screen with/without active rallies changes layout slightly).

## Dimensional Treasure (mithril) screen -- UNKNOWN [NEW]
- **Platform**: Windows
- **Session**: 2026-03-02
- **Debug screenshot**: `20260302_143838_127.0.0.1_5635_unknown_screen.png`
- **Issue**: The "Dimensional Treasure" / mithril mining zone selector has no screen template.
  Detected as UNKNOWN, triggering recovery attempts during mithril mining.
- **Action needed**: Consider adding a screen template for this screen, or handle it as
  an expected intermediate state in mine_mithril flow.

## verify_fail_war_screen -- MAP during nav to WAR [NEW]
- **Platform**: Windows
- **Session**: 2026-03-02
- **Debug screenshot**: `20260302_143442_127.0.0.1_5635_verify_fail_war_screen.png`
- **Issue**: During navigation MAP->WAR, verification found MAP screen instead of WAR.
  Screenshot shows the map with active rally troops, AP use banner visible.
  The AP banner may be blocking the alliance button tap that should navigate to WAR.
- **Stats**: 1 `map_screen->war_screen` nav failure in this session.

## Screen detection stability [GOOD]
- **map_screen.png**: 162 hits, 100% at (911,1817), scores 0.964-1.0 -- excellent
- **alliance_screen.png**: 44 hits, all at (186,1220), score 1.0 -- perfect
- **aq_screen.png**: 20 hits, all at (540,478), 0.973-1.0 -- excellent
- **bl_screen.png**: 25 hits, all at (251,1186), 0.934-1.0 -- solid
- **td_screen.png**: 22 hits, all at (540,1893), score 1.0 -- perfect
- **back_arrow.png**: 34 hits, positions (73,73)-(74,74), score 1.0 -- perfect
- **kingdom_screen.png**: 5 hits, all at (107,1882), score 1.0 -- perfect
