# Session History

Chronological record of analyzed sessions.

## Sessions 8-12: 2026-03-02 (Windows, local)

Five sessions from a single day, version transition from v2.0.6 to v2.0.7.

### Session 8: 15:07-15:12 (5 min, v2.0.6)
- **Device**: 127.0.0.1:5635
- **Actions**: 1 check_quests (100%), 1 rally_titan (fail), 1 join_rally (fail)
- **ADB**: 243 screenshots (avg 0.345s), 37 taps, 2 swipes
- **Source**: stats/session_20260302_150707.json

### Session 9: 15:13-15:28 (15 min, v2.0.6)
- **Device**: 127.0.0.1:5635
- **Data**: Session metadata only, no action stats recorded
- **Source**: stats/session_20260302_151312.json

### Session 10: 15:30-18:25 (175 min, v2.0.7) -- MAIN SESSION
- **Devices**: 6 (127.0.0.1:5635, :5555, :5625, :5645, :5655, :5585)
- **Memory**: 6167 MB RSS, peak 6353 MB
- **ADB totals**: 29,852 screenshots, 5,732 taps, 533 swipes -- zero failures
- **Key stats**:
  - join_rally: 0/172 (0%) across all 6 devices
  - rally_titan: 114/153 (74%) across all 6 devices
  - rally_eg: 10/25 (40%)
  - pvp_attack: 5/16 (31%)
  - occupy_tower: 7/15 (47%)
  - recall_tower: 3/3 (100%)
  - mine_mithril: 44/45 (98%)
  - heal_all: 78/78 (100%)
  - gather_gold: 33/33 (100%)
  - check_quests: 136/134 (94%)
  - restore_ap: 33/36 (92%)
- **Source**: stats/session_20260302_153003.json (353 KB)

### Session 11: 18:29-18:34 (5 min, v2.0.7)
- **Devices**: 6 (same as session 10)
- **Memory**: 507 MB (fresh restart) -> 28.9 MB peak (minimal activity)
- **ADB**: 3 screenshots only
- **Source**: stats/session_20260302_182955.json

### Session 12: 19:05-19:55 (50 min, v2.0.7)
- **Devices**: 7 (added emulator-5554)
- **Memory**: 6098 MB RSS, peak 6205 MB
- **ADB**: 2,413 screenshots (emulator-5554 avg 0.270s -- fastest), 593 taps, 25 swipes
- **Key stats**:
  - join_rally: 0/42 (0%) -- avg only 1.8s per attempt (protocol early bail)
  - rally_titan: 27/43 (63%)
  - recall_tower: 0/11 (0%) -- defending.png at 59%
  - pvp_attack: 2/3 (67%)
  - check_quests: 19/21 (90%)
- **Source**: stats/session_20260302_190519.json (157 KB)

### Session 12 Logs: 19:33-20:16 (43 min, v2.0.7)
- **Devices**: 3 (emulator-5554 "Nine", :5625 "Rhino", :5635 "diss1")
- **Bot restarts**: 3 (at 19:58, 20:01, 20:09) -- possibly OOM-triggered
- **Dominant noise**: ~477 LZ4 decompression warnings (lz4 package not installed)
- **Rally joins**: 3 successful (all EG), 38 depart-not-found failures
- **Rally titans**: 14/22 successful (64%), 2 full 3-retry exhaustions
- **Unknown screens**: 25 (14 from diss1 startup, 5 WAR transitional, 4 AQ popup, 2 black)
- **Navigation errors**: 2 recursion limits (WAR stuck), 1 backout stuck
- **Memory spikes**: +5658 MB delta on single check_quests call (3 devices concurrent OCR)
- **Tunnel**: 18+ "no data in 90s" reconnections
- **Frida**: Detached at 19:43 (application-requested), no protocol fast path after
- **Source**: logs/9bot.log (632 KB, 7253 lines)

### Session 12 Training Data: 19:05-20:00 (55 min)
- **Devices**: emulator-5554 (81%), 127.0.0.1:5625 (19%)
- **Entries**: 1,440 (1,052 screen, 377 template, 0 OCR)
- **Images**: 11 in JSONL + 18 orphans on disk (29 total)
- **Screen detection**: 99.2% hit rate (1044/1052)
- **NEW**: mithril_return.png device-specific failure (0% on emulator-5554, 100% on :5625)
- **NEW**: statuses/defending.png device-specific failure (8% on emulator-5554)
- **Source**: training_data/td_20260302_190541.jsonl (299 KB), td_20260302_195819.jsonl (30 KB)

### Session 12 Debug Assets: 280 files (103 MB)
- 63 failure screenshots: 41 jr_detail_load_fail, 18 titan miss/depart, 4 other
- 138 owner_ocr crops (69 pairs)
- 50 click trails (at rolling cap)
- 29 top-level debug PNGs (25 unknown screens, 2 OCR crops, 2 misc)

---

## Sessions 1-7: 2026-02-28 through 2026-03-02 (mixed platforms)

Previous sessions analyzed in earlier runs. 4 macOS local + 2 Windows inbox + 1 Windows local.
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
