# Timing Issues

## ADB Performance (Sessions 8-12, 2026-03-02)

### Per-device averages (session 10, 175 min, 6 devices)

| Device | Screenshots | Avg (s) | Max (s) | Taps | Tap Avg |
|--------|-------------|---------|---------|------|---------|
| :5655 | 6,493 | 0.299 | 0.97 | 1,469 | 0.098 |
| :5555 | 6,426 | 0.319 | 1.35 | 1,324 | 0.104 |
| :5625 | 6,743 | 0.331 | 1.24 | 1,149 | 0.103 |
| :5645 | 4,076 | 0.333 | 1.19 | 785 | 0.103 |
| :5635 | 5,015 | 0.323 | 1.06 | 962 | 0.104 |
| :5585 | 1,099 | 0.341 | 0.94 | 43 | 0.145 |

### emulator-5554 (session 12, local ADB -- fastest)
| Op | Count | Avg (s) | Max (s) |
|----|-------|---------|---------|
| screenshot | 2,022 | **0.270** | 0.58 |
| tap | 484 | 0.095 | 0.19 |
| swipe | 16 | 0.568 | 0.63 |

**Totals**: 32,268 screenshots, 6,325 taps, 558 swipes -- **zero failures, zero slow ops**.
Local ADB (emulator-5554) is 15-20% faster than TCP devices.

---

## Transition Budget Analysis (Session 10, 6 devices)

### CRITICAL: 0% met transitions

| Transition | Total Attempts | Met | Notes |
|------------|---------------|-----|-------|
| titan_on_map_select | 218 | 0 | Blind tap condition never triggers (see action_patterns) |
| titan_depart_settle | 149 | 0 | Related to above -- depart checked too early |
| jr_detail_load | 129 | 0 | Rally detail never loads (join_rally 0% root cause) |
| eg_p6_boss_tap | 14 | 0 | P6 attack dialog never opens |
| eg_p6_attack_dialog | 14 | 0 | Same -- P6 flow is broken |
| recover_cancel | 7 | 0 | Cancel button never found in recovery |

### BELOW 70%: Needs attention

| Transition | Rate | Budget | Avg When Met | Devices |
|------------|------|--------|-------------|---------|
| titan_search_menu_open | 52-67% | 1.5s | 1.16s | All -- menu open is inconsistent |
| eg_search_menu_open | 3/25 (12%) | 1.5s | -- | All -- EG search rarely opens |
| recover_back arrow | 1/13 (8%) | 2.0s | 2.13s | Recovery back arrow rarely works |
| eg_defending_to_depart | 3/19 (16%) | 1.0s | 0.87s | :5555 only |
| eg_depart_retry_wait | 2/14 (14%) | 2.0s | 0.45s | :5555 only |
| eg_proceed_to_depart | 4/16 (25%) | 2.0s | 1.14s | :5625 only |
| probe_dialog_open | 46/93 (49%) | 2.5s | 1.5-1.6s | All devices, ~50% |

### Budget Overruns (100% met but avg exceeds budget)

| Transition | Rate | Budget | Avg | Max | Devices |
|------------|------|--------|-----|-----|---------|
| verify_aq_screen | 100% | 2.0s | **2.3s** | 2.8s | :5555, :5635 |
| nav_map_to_alliance | 100% | 2.0s | **2.1s** | 2.3s | :5635, :5555 |
| nav_alliance_menu_load | 100% | 1.0s | **1.1s** | 1.2s | :5635, :5555 |
| aq_claim_settle | 100% | 1.0s | **1.05s** | 1.4s | :5555, :5635 |
| nav_td_to_map | 100% | 2.0s | **2.73s** | 3.0s | :5555 |

**Recommendation**: Widen `verify_aq_screen` to 3.0s, `nav_map_to_alliance` to 2.5s,
`nav_alliance_menu_load` to 1.5s. These are succeeding but burning through adaptive
budget -- will trigger tighter adaptive windows that eventually fail.

### Healthy (previously fixed, still good)

| Transition | Rate | Budget | Headroom |
|------------|------|--------|----------|
| nav_kingdom_to_map | 100% | 3.0s | +1.3s |
| nav_td_exit_to_map | 100% | 3.5s | +1.6s |
| titan_search_complete | 100% | 2.0s | +1.4s |
| titan_select_to_search | 100% | 1.0s | +0.6s |
| titan_rally_tab_load | 100% | 1.5s | +0.5s |
| verify_bl_screen | 100% | 2.5s | +0.9s |

---

## Tunnel Stability [ONGOING]

Session 12 (43 min): 18+ "no data in 90s" reconnections. The relay connection drops every
90 seconds due to idle timeout, reconnects in ~5s, then idles again. Constant cycle.

Cross-session (21 hours): 77 tunnel warnings, 17 idle timeouts, 17 connection errors,
6 HTTP 503s, multiple short-lived sessions (5-61s).

**Root cause**: Missing server-side keepalive pings. Bot sends no heartbeat during idle.

---

## Memory [CONCERN]

| Session | Duration | RSS (MB) | Peak (MB) | Notes |
|---------|----------|----------|-----------|-------|
| 8 | 5 min | 5,295 | 3,210 | Anomalous (snapshot timing) |
| 9 | 15 min | 5,807 | 6,114 | Stable |
| 10 | 175 min | 6,167 | 6,353 | 6 devices, stable |
| 11 | 5 min | 507 | 29 | Fresh restart |
| 12 | 50 min | 6,098 | 6,205 | 3 devices |

**No memory leak** (peak/current ratio ~1.02-1.05x in long sessions). But absolute RSS of
6+ GB is high. Log-reported deltas of +5658 MB per check_quests call suggest measurement
artifact or gc spike during concurrent 3-device OCR. Three restarts in session 12
(possibly OOM-triggered) warrant investigation.

---

## Frida Protocol Interceptor

Session 12: Frida detached at 19:43 (`application-requested`). After detachment, protocol
fast path returned no data -- fell through correctly to UI-based scanning.

Cross-session (21h): 161 Frida transport errors -- expected when gadget not installed on
most devices. Protocol works correctly on emulator-5554 when gadget is running.

---

## LZ4 Package Missing [NEW, EASY FIX]

~477 "lz4 package not installed" + ~477 "LZ4 decompression failed" warnings per session.
Accounts for ~88% of all WARNING lines, drowning meaningful warnings.

**Fix**: `pip install lz4`. Eliminates ~954 noise lines per session and enables
CompressedMessage decoding from the protocol interceptor.
