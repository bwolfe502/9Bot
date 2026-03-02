# Lessons Learned

Post-mortems from regressions and failed fixes. Reference before making similar changes.

## 001: rally_button.png titan regression (1502f45, reverted)

**Date**: 2026-03-02
**Commit**: 1502f45 → reverted same day
**Impact**: rally_titan dropped from 13% to 0% success

### What happened
Analysis showed `titan_on_map_select` had 0% met rate — the `timed_wait(lambda: False)` was just
a disguised sleep with no verification that the titan popup actually appeared. Claude proposed
replacing the blind tap at (420,1400) with a retry loop polling for `rally_button.png`.

### Why it failed
1. **Wrong template**: `rally_button.png` was captured from a rally dialog context, not the titan
   on-map popup. The popup has a different button style/size — template scored 0/7 matches.
2. **Retry loop dismissed the popup**: The loop re-tapped (540,900) center-screen on each iteration,
   which dismissed the popup before the next poll could find it.
3. **Shortened depart budget**: Code set `depart_budget = 2` when popup wasn't found (vs 8s original),
   giving the depart button almost no time to appear even if the tap worked.

### Rules derived
- **Never replace a working blind tap with template matching without first verifying the template
  matches the actual target UI.** Capture a screenshot of the exact popup, crop the button, and
  confirm the template score is >0.8 before committing.
- **Test template changes against real screenshots** from stats/debug data before deploying.
  A template that works in one UI context (dialog) may not work in another (map popup).
- **Don't reduce timeouts as part of a "fix"** — if the existing timeout was 8s, keep it 8s.
  Shortening budgets compounds failures when the primary fix doesn't work.
- **Retry loops that re-tap the trigger coordinate will dismiss the very UI they're trying to detect.**
  If polling for a popup, don't re-tap the trigger between polls.
