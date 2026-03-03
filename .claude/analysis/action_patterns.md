# Action Success Rates and Failure Patterns

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
