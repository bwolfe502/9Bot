# Session History

Chronological record of analyzed sessions.

## Session 7: 2026-03-02 14:31-14:46 (Windows, local)

- **Version**: 2.0.6
- **Duration**: 15 minutes
- **Device**: 127.0.0.1:5635 (diss1, MuMu Player)
- **Memory**: 6081 MB RSS, peak 6091 MB
- **Data source**: Local stats/session_20260302_143136.json + logs + training data

### Key Metrics
| Metric                  | Value    |
|-------------------------|----------|
| ADB screenshots         | 769      |
| ADB taps                | 215      |
| ADB swipes              | 31       |
| Avg screenshot time     | 0.312s   |
| Template hits total     | ~380     |
| Template misses total   | ~28      |
| Training data entries   | 485      |
| Screen unknowns         | 1        |

### Action Results
| Action       | Rate    | Attempts |
|--------------|---------|----------|
| heal_all     | 100%    | 19       |
| check_quests | 100%    | 4        |
| restore_ap   | 100%    | 4        |
| mine_mithril | 100%    | 1        |
| rally_titan  | 69%     | 13       |
| join_rally   | 0%      | 10       |

### Issues Found
- join_rally: 0% success (scroll settle broken)
- rally_titan: 31% failure (titan_on_map_select 0% met)
- search.png: 66% confidence (1% above threshold)
- stationed.png: 65 region misses
- mithril_depart.png: 13 region misses
- 21 AP read failures
- Frida interceptor failing (gadget not installed)
- Dimensional Treasure screen detected as UNKNOWN

### Debug Assets
- 2 failure screenshots (verify_fail_war, unknown_screen)
- 2 debug crops (ap_menu_crop, aq_ocr_crop)
- Empty directories: clicks/, failures/, owner_ocr/, training_rows/, training_squares/

---

## Sessions 1-6: 2026-02-28 through 2026-03-02 (mixed)

Previous sessions analyzed in earlier runs. Summary in MEMORY.md.
4 macOS local sessions + 2 Windows inbox sessions.
Key findings: rally_button.png regression (reverted), join_rally 0% cross-platform,
stationed.png region miss, search.png low confidence, multiple timing budget fixes applied.

---

## Cross-Session Log Analysis: 2026-03-01 17:32 - 2026-03-02 14:48

- **Duration**: ~21 hours
- **Log volume**: 129,121 lines across 4 log files (15.4 MB)
- **Devices active**: 5 (5555, 5585, 5625, 5635, 5645)
- **Key findings**:
  - 60 LOGGED OUT events on device 5585
  - 929 navigation warnings (unknown screens)
  - 161 Frida connection errors (expected -- gadget not installed)
  - 77 tunnel disconnects
  - 86 "could not join or start any rally" warnings
  - 56 PVP attack menu failures
  - 47 titan depart misses (attempt 1/3)
