# Template Matching Issues

## bl_button.png -- Device-specific failure on 5575 [NEW, CRITICAL]

**Device 127.0.0.1:5575**: 714 misses, best score consistently 41% (threshold 80%)
**Device 127.0.0.1:5585**: 47 hits at 99%, 0 misses

This is the root cause of the check_quests 89% failure rate on 5575. The template matches
perfectly on 5585 but scores only 41% on 5575. The BATTLE_LIST screen itself IS detectable
on 5575 when it loads (bl_screen scores 97%), so the screen template is fine — the button
template fails.

Possible causes:
- 5575 has different UI rendering (different emulator resolution/DPI setting)
- An overlay is covering the button region on 5575
- The template was captured from 5585's layout and 5575 has a slightly different position

**Source**: stats/session_20260303_160818.json (760 misses total in session)

---

## search.png -- 66% confidence [CONFIRMED, CROSS-PLATFORM]
- **Platforms**: macOS (0.666), Windows (0.665)
- **Score**: Consistently 66% across all devices and sessions (63 hits total)
- **Position**: Always at (654, 1395), region [0,960,1080,1920]
- **Custom threshold**: 0.65 -- only 1% headroom
- **Risk**: EXTREME. Any slight rendering change breaks titan search and AP restore flows.
- **Action needed**: Re-capture with tighter crop or find alternative detection method.

## statuses/defending.png -- Device-specific failure [NEW, CONFIRMED]
- **Device emulator-5554**: 11/12 misses, best score 52-60% (threshold 80%)
- **Device 127.0.0.1:5625**: 1/1 hits at 100%
- **Impact**: Tower recall completely broken on emulator-5554 (0/11 success in session 12)
- **Root cause**: Rendering difference between emulator instances
- **Action needed**: Recapture template from emulator-5554, or use multi-template approach

## mithril_return.png -- Device-specific failure [NEW, CONFIRMED]
- **Device emulator-5554**: 14/14 misses at conf=60 (threshold 80%)
- **Device 127.0.0.1:5625**: 5/5 hits at conf=100
- **Impact**: Mithril return detection fails on emulator-5554
- **Data**: Training data shows clear device split during simultaneous check (19:44:07-19:44:26)
- **Action needed**: Same as defending.png -- recapture or multi-template

## stationed.png -- Region miss [CONFIRMED]
- **Platform**: Windows (65x region misses in session 7, 103x in earlier sessions)
- **Found at**: Various Y positions outside IMAGE_REGIONS
- **Impact**: Every EG priest probe triggers full-screen fallback search. Performance waste.
- **Action needed**: Widen `IMAGE_REGIONS["stationed.png"]` to cover actual hit range

## mithril_depart.png -- Borderline hit [WATCH]
- **Device :5555**: Hit at 0.746 (below 0.8 threshold) -- BELOW standard threshold
- **All other devices**: 98-100% confidence
- **Position varies**: Y range 715-1250 across 4 known positions
- **Action needed**: Monitor. May need device-specific template if :5555 starts missing.

## rally_eg_select.png -- Borderline at 84% [WATCH]
- **Devices**: :5635, :5555, :5625, :5645, :5655 -- all at 0.844
- **Margin**: +0.044 above threshold -- thin but consistent across devices
- **Risk**: Moderate. Stable within sessions but could regress with game updates.

## close_x.png -- Occasional borderline [LOW]
- **Device :5655**: One hit at 0.815 (+0.015 above threshold)
- **All other devices**: 0.997-1.0
- **Risk**: Very low. Single outlier.

## aq_screen vs war_screen -- Tight margin [NEW, WATCH]
- **Gap**: Only 6-9 points (aq_screen 97-100% vs war_screen 91%)
- **Impact**: Not currently causing misclassification
- **Risk**: If aq_screen template quality degrades, war_screen could win the match
- **Root cause**: Both screens share alliance navigation tab chrome
- **Data**: 119 aq_screen detections, war_screen runner-up at 91% on every one

## back_arrow.png region miss false positive [KNOWN, IGNORE]
- **Position**: (1001, 1083) -- mid-right of screen, not a real back arrow
- **Log**: "REGION MISS for back_arrow.png" at 20:01:37 on emulator-5554
- **Action**: Do NOT widen IMAGE_REGIONS. This is a coincidental pattern match during
  popup/overlay state. The real back arrow is always at (73-74, 73-74).

## Dimensional Treasure screen -- UNKNOWN [KNOWN]
- Mithril mining zone selector has no screen template
- Detected as UNKNOWN, triggers recovery attempts during mine_mithril
- **Status**: mine_mithril still succeeds 98% (44/45) -- not blocking

## Screen detection stability [GOOD]
- **map_screen.png**: 586 hits, 100% at 95-99% -- rock solid on current devices
- **search.png fallback** (for 70-79% MAP): Not needed this session (was added for shoda's emulator)
- **alliance_screen.png**: 48 hits, all at 100% -- perfect
- **aq_screen.png**: 119 hits, 97.5% hit rate -- good (3 misses at 55-59% during transitions)
- **bl_screen.png**: 132 hits, 100% at 91-97% -- solid
- **td_screen.png**: 30 hits, 100% at 90-100% -- solid
- **kingdom_screen.png**: 10 hits, 100% -- perfect
- **war_screen.png**: 127 hits, 96.1% (5 misses at 61% during transitions)
- **back_arrow.png**: 84 hits, 97% at 99-100% -- 3 near-misses at 65% (screen obstructed)

## Fixed Issues
- **rally_titan_select.png**: Stable at 0.865 on Windows (50 hits, 0 misses). Was intermittent on macOS.
- **rally_button.png**: Reverted to blind tap. See lessons.md #001.
