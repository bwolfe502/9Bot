# Action Success Rates and Failure Patterns

## Aggregate Totals (Sessions 13-22, 2026-03-03, v2.1.0)

### Session 13-22 Combined (2026-03-03, 2 devices: :5575, :5585)

| Action | Attempts | Successes | Rate | Trend vs Prior |
|--------|----------|-----------|------|----------------|
| check_quests (5575) | 231 | ~8 | **~3%** | WORSE — bl_button broken on 5575 |
| check_quests (5585) | 114 | 112 | **98%** | Stable |
| join_rally (5575) | 77 | 43 | **56%** | IMPROVED (was 0%) |
| join_rally (5585) | 99 | 64 | **65%** | IMPROVED (was 0%) |
| rally_titan (5575) | 31 | 15 | **48%** | Worse |
| rally_titan (5585) | 31 | 12 | **39%** | Worse |
| rally_eg (5575) | 23 | 4 | **17%** | Worse |
| rally_eg (5585) | 21 | 2 | **10%** | Worse |
| pvp_attack (5575) | 9 | 3 | **33%** | Stable |
| pvp_attack (5585) | 22 | 3 | **14%** | Worse |
| gather_gold (5585) | 25 | 23 | **92%** | Stable |
| heal_all | 98+133 | 100% | 100% | Stable |

**Source**: stats/session_20260303_160818.json, session_20260303_201642.json

---

### NEW: check_quests 5575 — bl_button.png broken [CRITICAL, NEW]

**5575 check_quests fails 89% of the time (357/403 in session_20260303_160818).**

Root cause: `bl_button.png` scores **41%** consistently on device 127.0.0.1:5575 (needs 80%).
The button is never found, so navigation to BATTLE_LIST always fails. Device 5585 succeeds at
99% on the same template.

The `bl_screen` template (screen detection) scores only **7-14%** on 5575's MAP screen,
meaning the BATTLE_LIST navigation tap is not working at all on this device — the game may
not be in the right state, or 5575 has a UI variation.

When 5575 IS on BATTLE_LIST (seen from 20:26 onward), it scores 97% on bl_screen. So the
template is correct — the problem is that navigation TO the battle list is failing.

- 220 consecutive "Navigation verify FAILED: expected Screen.BATTLE_LIST, on Screen.MAP" for 5575
- Failure begins at 18:53 and continues until a different screen context is entered at 20:26
- After 20:26 (during a separate mode), 5575 reaches BATTLE_LIST 60 times successfully

**Cascade**: 220 quest screen navigation failures → check_quests 89% fail rate → no rally
dispatch, no PVP, no tower — 5575 bot is essentially non-functional during this period.

**Hypothesis**: 5575 had a popup or overlay blocking the battle list button. The
`bl_button.png` is in a search region that may be partially obscured.

**Distinguishing from prior join_rally 0%**: Join rally is now WORKING (56%/65% success),
which is a major improvement from previous sessions. The 0% join_rally was fixed.

---

### NEW: join_rally FIXED [IMPORTANT]

Previous sessions showed 0/215 join_rally successes. This session shows:
- 5575: 43/77 (56%)
- 5585: 64/99 (65%)

The `jr_detail_load` transition that was 0% in previous sessions is now working.
**The join_rally fix appears to have taken effect.**

---

### NEW: rally_titan degraded [WATCH]

Previous best: 141/197 (72%). This session: 27/62 combined (44%).
- titan_search_menu_open: 16/28 (57%) for 5575, 2/14 (14%) for 5585
- titan_on_map_select: 0/39 across both devices (known non-functional metric)
- titan_depart_settle: 0/28 across both devices (known non-functional metric)

Multiple `titan_select_not_found` failure screenshots. May be related to titan respawn timing
or the search menu not opening reliably this session.

---

### NEW: gather_gold on 5585 — transition failures [NEW]

gather_gold succeeds at 92% (23/25) but internal transitions fail completely:
- `gather_search_menu_open`: 0/24 met (1.0s budget)
- `gather_tab_load`: 0/24 met (0.8s budget)
- `gold_mine_select`: 0/25 met (3s budget)

Yet gather_gold itself succeeds. These transitions may be mis-named or the success condition
is checked differently in the gather flow. Not a blocking issue.

---

### NEW: EG P6 failures persist [CONFIRMED]

- `eg_p6_boss_tap`: 0/9 met across both devices
- `eg_p6_attack_dialog`: 0/9 met across both devices
- P6 attack dialog still never opens — the EG P6 flow remains broken

---

### NEW: heal transitions all 0% [LOW PRIORITY]

All heal sub-transitions (heal_dialog_open, heal_confirm_ready, heal_result_show, heal_close_settle)
show 0% met_count across both devices, but heal_all itself succeeds at 100%. These timed_wait
transitions are not matching the actual heal dialog state changes — the conditions are likely
checking wrong templates. Not blocking since heal_all succeeds.

---

## Aggregate Totals (Sessions 8-12, 2026-03-02, v2.0.6-v2.0.7)

| Action | Attempts | Successes | Rate | Trend vs Prior |
|--------|----------|-----------|------|----------------|
| join_rally | 215 | 0 | **0%** | Same (0%) |
| recall_tower | 14 | 3 | **21%** | NEW |
| pvp_attack | 19 | 7 | **37%** | Worse (was ~50%) |
| rally_eg | 25 | 10 | **40%** | NEW |
| occupy_tower | 18 | 8 | **44%** | NEW |
| rally_titan | 197 | 141 | **72%** | Improved (was 69%) |
| restore_ap | 37 | 34 | 92% | Stable |
| check_quests | 166 | 156 | 94% | Stable |
| mine_mithril | 45 | 44 | 98% | Stable |
| heal_all | 89 | 88 | 99% | Stable |
| gather_gold | 33 | 33 | 100% | Stable |

**6 devices tested**: :5635, :5555, :5625, :5645, :5655, :5585, emulator-5554

---

## join_rally -- 0% success [CRITICAL, CONFIRMED]

**215/215 failures across all 7 devices, all 5 sessions.**

The `jr_detail_load` transition (3s budget) NEVER succeeds. After tapping a join button on the
war screen, `depart.png` never appears. Pattern:
1. Protocol early bail-out works correctly (returns [] when no rallies, saves 20-30s)
2. UI fallback: war screen scan finds join buttons, owner OCR reads names correctly
3. Join button tapped, but rally is already full/departed by the time detail loads
4. 38 individual depart-not-found failures in the log session alone

**Session 12 log detail**: The 3 successful EG joins (20:08-20:15) suggest joining CAN work
when rallies are fresh. The 38 failures clustered 19:46-20:04 were against already-full
Morteza titan rallies. The issue may be timing (rallies filling faster than bot can tap)
rather than a template/UI bug.

**avg failure time**: 1.8s (with protocol) to 31.9s (UI-only scroll)

---

## recall_tower -- 21% success [HIGH]

**3/14 successes. 11 consecutive failures in session 12 (emulator-5554).**

Root cause: `statuses/defending.png` matches at only 52-60% on emulator-5554 (threshold 80%).
Protocol troop data confirms a troop IS defending -- the vision template just doesn't match
on this device. Device :5625 matches at 100%.

**Cascade**: quest OCR detects "troop still defending", recall attempted, defending icon not
found, recall returns False, next cycle repeats. 3 cycles observed (19:33, 19:36, 19:39).

---

## rally_eg -- 40% success [MODERATE]

**10/25 successes across 5 devices.**

Failure modes:
- `eg_search_menu_open`: 3/25 (12%) met -- EG search menu rarely opens on first try
- `eg_p6_boss_tap` / `eg_p6_attack_dialog`: 0/14 met across 3 devices -- P6 dialog never opens
- P6 failure logged: "attack dialog never opened after 3 attempts -- aborting" (20:15)
- `eg_proceed_to_depart`: 4/16 (25%) met on :5625

---

## pvp_attack -- 37% success [MODERATE]

**7/19 successes across 4 devices.**

Prior cross-session analysis showed 22x "attack menu did not open" + 56 "PVP: attack menu
did not open" warnings. Tower state changes between navigation and tap. Depart threshold
lowered to 0.7 (vs 0.8 standard) helps but doesn't solve the root cause.

---

## rally_titan -- 72% success [IMPROVED]

**141/197 successes (was 69% in session 7). Per-device range: 63-83%.**

| Device | Rate | Attempts |
|--------|------|----------|
| :5635 | 83% | 29 |
| :5645 | 77% | 13 |
| :5555 | 81% | 27 |
| :5625 | 71% | 35 |
| :5655 | 67% | 48 |
| :5585 | 100% | 1 |
| emulator-5554 | 63% | 43 |

**titan_on_map_select: 0/218 met across all sessions.** The blind tap at (540,900) never
triggers the timed_wait condition. When it DOES hit the titan, depart appears, but the
timed_wait still reports 0% because the condition function checks for something else.
Rally_titan succeeds via the retry-and-search loop, not via this transition.

**2 full 3-retry exhaustions** in session 12 (emulator-5554 at 19:37 and 19:53, 51s and 56.5s).

---

## Failure Cascades (from session 12 logs)

### Cascade 1 -- Morteza rally join failure chain (19:46-19:47)
Frida detached at 19:43 → no protocol fast path → UI rally scan → all Morteza titan rallies
already full → 38 depart-not-found failures across 2 devices simultaneously.

### Cascade 2 -- WAR screen stuck (19:58)
rally_titan completes → check_screen returns WAR → back_arrow tapped at 99% → screen stays WAR
→ navigation recursion limit hit × 2 → eventually resolves on third attempt.

### Cascade 3 -- Tower recall loop (19:33-19:39)
Quest OCR detects defending troop → defending.png at 59% → recall fails → next cycle repeats × 3.

### Cascade 4 -- P6 Evil Guard attack failure (20:15-20:16)
EG tapped at coordinates → attack dialog never opens after 3 attempts → ERROR abort.

---

## Memory Spike Pattern [NEW, HIGH]

Session 12 logs show extreme memory deltas:
```
20:04:05 [127.0.0.1:5625] check_quests completed: +5658.1 MB, RSS: 6173 MB
20:07:45 [emulator-5554]   check_quests completed: +5623.4 MB, RSS: 6137 MB
20:04:16 [127.0.0.1:5635]  check_quests completed: +1521.3 MB, RSS: 6130 MB
```

Three bot restarts in 43 minutes (19:58, 20:01, 20:09) suggest OOM pressure when 3 devices
run concurrent OCR. RSS plateaus at ~6.1 GB. Earlier in session, deltas were +10-100 MB.
The spike correlates with the third device joining.
