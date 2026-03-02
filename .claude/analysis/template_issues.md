# Template Matching Issues

## rally_button.png — Never matched titan popup [REVERTED]
- **Platform**: macOS (likely cross-platform)
- **Sessions**: 2026-03-02 (all 4 latest sessions)
- **Introduced**: Commit 1502f45 replaced blind tap at (420,1400) with rally_button.png polling
- **Stats**: `titan_popup_check` 0/7 met — template NEVER matches on titan on-map popup
- **Root cause**: rally_button.png (golden "RALLY" button) was captured from a dialog context, not the titan map popup. The on-map popup likely has a different button style/size.
- **Resolution**: Reverted to pre-1502f45 blind tap. See [lessons.md](lessons.md) #001.
- **Note**: rally_button.png still exists in elements/ — used in runners.py for pass rally (threshold 0.7)

## rally_titan_select.png — Intermittent failure [CONFIRMED, WORSENED]
- **Platform**: macOS
- **Sessions**: 2026-03-01 (3 hits at 0.866), 2026-03-02 (2 hits at 0.866 + **12 misses** at 0.418-0.423)
- **Used with**: threshold 0.5 (timed_wait) and 0.65 (wait_for_image_and_tap)
- **Risk**: 12/14 attempts are misses. The 0.42 scores are far below even the 0.5 threshold.
- **Possible cause**: Search menu opens but titan tab not visible, or rendering difference causes mismatch
- **Action needed**: Re-capture template; investigate why scores swing between 0.42 and 0.87

## stationed.png — Region miss [CONFIRMED]
- **Platform**: Windows (103x region misses in single session)
- **Session**: 2026-03-01 (inbox bugreport_191135)
- **Found at**: y=630, y=987, y=1344 (all at x=155)
- **Impact**: Every EG priest probe triggers full-screen fallback search. Performance waste.
- **Action needed**: Widen `IMAGE_REGIONS["stationed.png"]` to cover y-range 600-1400

## search.png — Low confidence match [CONFIRMED]
- **Platform**: macOS only
- **Sessions**: 2026-03-01, 2026-03-02 (consistent)
- **Score**: 0.666 (all hits at position (654, 1395))
- **Used in**: Titan rally flow (titan_select_to_search), AP restore flow
- **Uses custom threshold**: 0.65 in titans.py `wait_for_image_and_tap("search.png", ..., threshold=0.65)`
- **Risk**: Barely above 0.65 threshold. Template needs re-capture for macOS.

## mithril_depart.png — Region miss [CONFIRMED]
- **Platform**: macOS
- **Session**: 2026-03-01
- **Found at**: Y=894 and Y=715 (outside IMAGE_REGIONS)
- **Action needed**: Widen IMAGE_REGIONS for mithril_depart.png to cover Y 700-1450

## war_screen.png — Lower than peers [LOW]
- **Platform**: macOS (0.950-0.965), Windows (0.965+)
- **Risk**: Stable but notably lower than other screen templates (0.99+). Not at risk of threshold failure.

## aq_screen.png — Occasional dip [LOW]
- **Platform**: macOS
- **Score**: Usually 0.988-0.999, one dip to 0.842 in latest session
- **Risk**: Single outlier, likely transient overlay. Monitor.

## Unknown screen popups [CONFIRMED]
- **Platform**: Windows (89x in single session)
- **Popups identified from debug screenshots**:
  - Expedition Gold quest popup (blue screen with coin icon, no close X)
  - Shop/rewards screen (item icons at top, back arrow at bottom-left)
- **Impact**: Triggers recovery sequences, wastes 5-15s per occurrence
- **Action needed**: Consider adding templates for common game event popups
