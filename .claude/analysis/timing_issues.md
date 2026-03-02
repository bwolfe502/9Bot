# Timing Issues

## ADB Performance (session 2026-03-02, device 127.0.0.1:5635)

| Operation   | Count | Avg (s) | Max (s) | Slow (>2s) | Failures |
|-------------|-------|---------|---------|------------|----------|
| screenshot  |   769 |  0.312  |  0.73   |     0      |    0     |
| tap         |   215 |  0.098  |  0.24   |     0      |    0     |
| swipe       |    31 |  0.598  |  0.70   |     0      |    0     |

**Assessment**: ADB performance is excellent. No slow operations, zero failures.
Screenshot avg 312ms is normal for TCP ADB over emulator.

## Transition Budget Analysis

### CRITICAL: 0% met transitions (always expire)

**Titan flow:**
- `titan_on_map_select`: 0/10 met, budget=1.5s -- Blind tap (540,900) never hits titan popup.
  This is the root cause of rally_titan's ~30% failure rate.
- `titan_depart_settle`: 0/9 met, budget=1s -- Depart confirmation never detected after tap.

**Rally join flow:**
- `jr_scroll_up_settle`: 0/10 met, budget=1.5s -- Scroll never produces expected result
- `jr_scroll_down_settle`: 0/18 met, budget=1.5s -- Same issue, both directions
- Combined 0/28 -- the entire scroll-based rally finding mechanism is non-functional.

**Heal flow (expected 0% -- `lambda: False` waits, just sleeps):**
- `heal_dialog_open`: 0/2, budget=1s
- `heal_confirm_ready`: 0/2, budget=1s
- `heal_result_show`: 0/2, budget=1s
- `heal_close_settle`: 0/2, budget=2s
- **Not a real issue** -- heal_all succeeds 100% (19/19). These are intentional delays.

**Mithril flow (expected 0% -- `lambda: False` waits, just sleeps):**
- `mithril_scroll_settle`: 0/3, budget=0.5s
- `mithril_scroll_done`: 0/1, budget=1s
- `mithril_tunnel_open`: 0/1, budget=2s
- `mithril_advanced_open`: 0/1, budget=2s
- `mithril_slot_tap`: 0/4, budget=1s
- `mithril_recall_anim`: 0/3, budget=1.5s
- `mithril_recall_settle`: 0/1, budget=1s
- `mithril_mine_popup`: 0/4, budget=3s
- `mithril_attack_to_depart`: 0/4, budget=2s
- `mithril_deploy_anim`: 0/4, budget=2s
- **Not a real issue** -- mine_mithril succeeds 100% (1/1). These are intentional delays.

### WATCH: Below 80% met

- `titan_search_menu_open`: 7/10 met (70%), budget=1.5s, avg=1.00s, max=1.23s
  - 3 misses likely due to slow menu open on certain attempts.
  - Budget is adequate (max 1.23s vs 1.5s). Misses may be from stale screenshots or lag.

### HEALTHY: Navigation transitions (all improved from previous sessions)

| Transition               | Met  | Budget | Avg    | Max    | Headroom |
|--------------------------|------|--------|--------|--------|----------|
| verify_bl_screen         | 5/5  | 2.5s   | 1.64s  | 2.38s  | 0.86s    |
| verify_aq_screen         | 5/5  | 2.0s   | 1.70s  | 2.48s  | 0.30s    |
| nav_map_to_alliance      | 11/12| 2.0s   | 1.64s  | 2.40s  | 0.36s    |
| nav_alliance_menu_load   | 11/12| 1.0s   | 0.82s  | 1.22s  | 0.18s    |
| verify_war_screen        | 11/12| 1.5s   | 0.80s  | 1.13s  | 0.70s    |
| nav_td_exit_to_map       | 11/11| 3.5s   | 1.91s  | 2.61s  | 1.59s    |
| verify_kingdom_screen    | 1/1  | 5.0s   | 1.65s  | 1.65s  | 3.35s    |
| nav_kingdom_to_map       | 1/1  | 3.0s   | 1.69s  | 1.69s  | 1.31s    |
| recover_close X          | 1/1  | 2.0s   | 0.98s  | 0.98s  | 1.02s    |
| titan_rally_tab_load     | 3/3  | 1.5s   | 1.02s  | 1.30s  | 0.48s    |
| titan_search_complete    | 10/10| 2.0s   | 0.63s  | 1.00s  | 1.37s    |
| titan_select_to_search   | 10/10| 1.0s   | 0.38s  | 0.56s  | 0.62s    |

**Notable improvements from previous sessions:**
- `nav_kingdom_to_map`: Was 0-9% met at 1.0s budget. Now 1/1 at 3.0s budget. FIXED.
- `nav_td_exit_to_map`: Was 71-78% met. Now 11/11 at 3.5s budget. FIXED.

**Tight headroom (watch for regression):**
- `verify_aq_screen`: Only 0.30s headroom. One outlier at 2.48s (budget 2.0s).
- `nav_alliance_menu_load`: Only 0.18s headroom. Max 1.22s vs 1.0s budget.
  Consider widening to 1.5s.

## Tunnel Stability

- **77 tunnel warnings** across Mar 1 17:32 - Mar 2 14:48 (~21 hours)
- 17 "no data in 90s" timeouts (tunnel idle disconnect)
- 17 "BaseEventLoop.create_connection()" errors (network level)
- 6 "server rejected WebSocket connection: HTTP 503" (relay overloaded)
- Multiple server-initiated closes with short uptimes (5-61s)
- 2 keepalive ping timeouts
- **Assessment**: Tunnel is unstable. The relay server appears to close connections
  aggressively (code=1000 after 5-60s). Possible causes: relay server memory pressure,
  nginx timeout configuration, or bot not sending heartbeats fast enough.

## Frida Protocol Interceptor

- **161 errors** -- all `frida.TransportError: connection closed`
- Connects every 10s, fails every time -- Frida Gadget not running in game process
- 3 devices being forwarded (5555, 5635, 5645) but none have gadget active
- **Assessment**: Protocol is enabled in settings but game APK not patched. Expected behavior
  when protocol_enabled=True but gadget not installed. Consider disabling if not in use to
  reduce log noise (161 errors + 161 warnings = 322 unnecessary log lines).

## Memory

- **Session memory**: 6081 MB RSS, peak 6091 MB (15-min session)
- **Previous sessions**: RSS ~6000-6200 MB range (stable, no growth observed)
- **Assessment**: High absolute value (6 GB) but stable. Typical for EasyOCR + OpenCV + multiple
  emulator screenshots. No memory leak detected.
