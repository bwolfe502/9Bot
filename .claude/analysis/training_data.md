# Training Data Patterns

## Session 2026-03-02 (td_20260302_143146.jsonl)

- **Total entries**: 485
- **Type breakdown**: 358 screen, 123 template, 4 unknown-type entries
- **Images captured**: 0 (no near-miss or low-confidence images saved this session)

### Screen Detection (358 entries)

- **Unknown screens**: 1 (out of 358 = 0.3%)
- **All screens correctly identified**: map_screen, bl_screen, aq_screen, alliance_screen,
  war_screen, td_screen -- no misclassifications observed
- **Score separation**: Good. Best screen typically 94-100%, runner-up 40-75%.
  The largest runner-up gap is war_screen at 75% when alliance_screen is best (100%),
  but this is expected (war tab is within alliance screen).

### Template Patterns (123 entries)

**Misses (expected negatives):**
| Template          | Count | Best Score | Notes                                |
|-------------------|-------|------------|--------------------------------------|
| heal.png          |   20  |    48%     | No injured troops -- correct miss    |
| aq_claim.png      |    5  |    44%     | No claimable quests -- correct miss  |
| mithril_return.png|    3  |    69%     | Mithril not ready -- correct miss    |
| close_x.png       |    1  |    53%     | No close button visible -- normal    |

**Borderline hits:**
| Template          | Count | Score | Notes                                |
|-------------------|-------|-------|--------------------------------------|
| search.png        |   13  |  66%  | All at (654,1395). Custom 0.65 thresh|

**Solid hits:**
| Template               | Count | Score Range | Notes                   |
|------------------------|-------|-------------|-------------------------|
| back_arrow.png         |   34  | 100%        | Perfect                 |
| rally_titan_select.png |   10  | 87%         | Stable on Windows       |
| close_x.png            |    8  | 99-100%     | Two positions (450,499) |
| bl_button.png          |    5  | 99%         | Stable                  |
| mithril_attack.png     |    4  | 100%        | Perfect                 |
| mithril_depart.png     |    4  | 98-100%     | Y varies (715-1250)     |
| mithril_return.png     |    3  | 100%        | Perfect when present    |
| heal.png               |    2  | 100%        | Perfect when injured    |

### Notable Patterns

1. **Repetitive cycles**: Training data shows a clear loop pattern:
   MAP -> (heal check) -> (titan search) -> ALLIANCE -> WAR -> (scroll for rally) ->
   back out -> MAP. This cycle repeats ~8 times in the 15-minute session.

2. **No region drift detected**: All template hits within expected IMAGE_REGIONS bounds.

3. **No image captures**: The training data collector was active but produced 0 images.
   This means no near-misses (65-79% confidence) were detected outside of the known
   search.png issue (which is at 66% but uses a custom 0.65 threshold, so it registers
   as a hit, not a near-miss).

4. **Screen transition flow**: The JSONL clearly shows the navigation state machine working:
   MAP(99%) -> alliance_screen(100%) -> war_screen(100%) -> back through td_screen(100%)
   -> MAP(100%). Clean transitions with no confusion between screens.
