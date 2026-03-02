# Action Success Rates and Failure Patterns

## Session 2026-03-02 (v2.0.6, Windows, device 127.0.0.1:5635, 15 min)

### Action Summary

| Action       | Attempts | Success | Fail | Rate   | Avg Time | Notes                          |
|--------------|----------|---------|------|--------|----------|--------------------------------|
| heal_all     |    19    |   19    |   0  | 100%   |  2.0s    | Working perfectly               |
| rally_titan  |    13    |    9    |   4  |  69%   | 19.4s    | Improved from previous sessions |
| join_rally   |    10    |    0    |  10  |   0%   | 15.3s    | BROKEN -- 0% success            |
| restore_ap   |     4    |    4    |   0  | 100%   | 15.4s    | Working perfectly               |
| check_quests |     4    |    4    |   0  | 100%   | 141.8s   | Long but reliable               |
| mine_mithril |     1    |    1    |   0  | 100%   | 67.0s    | Single successful run           |

### join_rally -- 0% success [CRITICAL]

**Error pattern**: All 10 failures logged as "unknown" with durations 11.4-27.0s.

**Timeline of failures** (from stats errors):
```
14:33:57  join_rally  15.2s  (first attempt)
14:35:27  join_rally  11.7s
14:36:25  join_rally  15.3s
14:37:31  join_rally  27.0s  (longest -- likely more scrolling)
14:40:26  join_rally  12.0s
14:41:39  join_rally  11.6s
14:42:11  join_rally  11.4s
14:44:59  join_rally  12.1s
14:45:34  join_rally  11.6s
14:46:21  join_rally  25.5s
```

**Root cause analysis**:
- `jr_scroll_down_settle`: 0/18 met -- scroll animation never produces expected UI state
- `jr_scroll_up_settle`: 0/10 met -- same issue in reverse direction
- The join_rally function scrolls the war screen looking for join buttons but the scroll
  settle condition never triggers, so it burns through the budget on every attempt
- This correlates with 86x "Rally loop: could not join or start any rally" warnings in logs
- **Recommendation**: Needs complete rework. Protocol fast-path (already implemented) should
  be the primary path when available. UI fallback needs new scroll detection logic.

### rally_titan -- 69% success [IMPROVED BUT STILL FAILING]

**Error pattern**: 4 failures at 1.3-1.6s each (fast fails = titan walked off-center).

**Timeline of failures**:
```
14:37:36  rally_titan  1.3s
14:39:16  rally_titan  1.4s
14:42:17  rally_titan  1.6s
14:43:53  rally_titan  1.6s
```

**Analysis**:
- `titan_on_map_select`: 0/10 met -- the timed_wait checking for titan popup after blind tap
  never detects the popup. This is the known issue where blind tap (540,900) misses when
  titan walks to a different position after search.
- `titan_depart_settle`: 0/9 met -- depart button appears but settle condition not detected.
- The retry loop (3 attempts) recovers from the first miss in most cases.
- 69% success is improved from previous ~13% (post-revert to blind tap).
- The search.png template at 66% confidence adds fragility to the entire flow.

### check_quests -- 100% success, slow but reliable

- 4/4 success, average 141.8s per call
- Most time spent on quest OCR + dispatch + navigation cycles
- 567.3s total (9.5 minutes) out of 15-minute session = 63% of runtime in quest checking

### heal_all -- 100% success, fast

- 19/19 success, 2.0s average -- very efficient
- heal.png: 19 misses (expected -- no injured troops) + 2 hits (troops to heal)
- All heal_* transitions are `lambda: False` sleeps, 0% met is expected

### restore_ap -- 100% success

- 4/4 success, average 15.4s per call
- AP restoration working correctly through free restores + potions

## Cross-Session Log Analysis (Mar 1 17:32 - Mar 2 14:48, ~21 hours)

### Warning Frequency (top issues)

| Count | Warning                                        | Severity |
|-------|------------------------------------------------|----------|
|  929  | navigation: Unknown screen / recovery          | HIGH     |
|  591  | actions: Various action warnings                | HIGH     |
|  162  | actions.quests: Mostly pvp_attack failures     | MEDIUM   |
|  161  | protocol.interceptor: Frida connection fails   | LOW*     |
|  114  | vision: Region misses + AP read fails          | MEDIUM   |
|   77  | tunnel: Disconnect/reconnect cycles            | MEDIUM   |
|   77  | actions.titans: rally_titan failures            | MEDIUM   |
|   38  | actions.farming: Gather failures               | LOW      |
|   21  | web: Dashboard warnings                        | LOW      |

*Low severity because protocol is enabled but gadget not installed -- expected behavior.

### Unknown Screen Detection (929 navigation warnings)

| Count | Screen State                              | Notes                        |
|-------|-------------------------------------------|------------------------------|
|  242  | best: MAP at 54%                         | MAP with overlay/popup        |
|  175  | best: TERRITORY at 63%                   | Territory screen variant      |
|  115  | best: WAR at 61%                         | War screen loading/transition |
|  109  | best: MAP at 53%                         | Another MAP variant           |
|   88  | best: MAP at 55%                         | Another MAP variant           |
|   27  | Recovery FAILED (all strategies)         | Unrecoverable UNKNOWN state   |
|   17  | TERRITORY at 43%                         | Low confidence territory      |
|   14  | WAR at 66%                               | War screen borderline         |
|   12  | None at 0%                               | Completely unrecognized       |

The high count of MAP-at-50-55% unknowns suggests popups or overlays on top of the MAP screen
that reduce the map_screen.png confidence below the 80% threshold. The "Expedition Gold" popup
and AP use banners are known offenders.

### Navigation Errors

| Count | Error                                    | Notes                        |
|-------|------------------------------------------|------------------------------|
|   60  | LOGGED OUT (ATTENTION popup detected)    | All on device 5585           |
|    5  | Navigation recursion limit reached       | Max depth 3 hit              |
|   26  | Verify FAILED: expected BATTLE_LIST      | Got MAP instead              |
|   17  | Verify FAILED: expected WAR, got MAP     | Alliance button tap missed   |

**Device 5585** has a severe stability issue -- 60 LOGGED OUT events suggests the game is
crashing or being disconnected frequently on this device.

### Specific Action Warning Patterns

| Count | Warning                                  | Impact                       |
|-------|------------------------------------------|------------------------------|
|   93  | Not enough troops available              | Normal gate -- troops deployed|
|   86  | Rally loop: could not join/start rally   | join_rally broken            |
|   56  | PVP: attack menu did not open            | Tower state changed          |
|   47  | Depart not found (titan attempt 1/3)     | Titan walked off-center      |
|   32  | Tower quest: failed to deploy troop      | Tower occupy issues          |
|   29  | Gather button not found after 2 attempts | Gold mine not visible        |
|   26  | Failed to navigate to quest screen       | UNKNOWN screen blocking      |
|   23  | PVP: depart button not found             | PVP panel issues             |
|   22  | Reinforce button not found after tower   | Wrong tower type/state       |
|   21  | Failed to find Titan select              | Search menu not opening      |
|   16  | Failed to navigate to war screen         | Navigation failure           |
|   16  | Depart button not found, backing out     | Rally join sub-failure       |
|   11  | Lost war screen after failed join        | War screen navigation lost   |
|   10  | Gather troop failed on retry             | Gather deploy issues         |
|   10  | Depart not found (titan attempt 2/3)     | Second retry also failing    |

### Device Activity Distribution

| Device         | Log Lines | Notes                                    |
|----------------|-----------|------------------------------------------|
| 127.0.0.1:5625 |   28,299  | Most active (likely primary account)     |
| 127.0.0.1:5645 |   21,834  | Active (Plippy)                          |
| 127.0.0.1:5555 |   20,994  | Active (Nine)                            |
| 127.0.0.1:5585 |    8,038  | Less active + 60 logged-out events       |
| 127.0.0.1:5635 |    4,922  | Least active (diss1, stats session)      |
