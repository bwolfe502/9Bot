"""
stats_data.py  —  9Bot PvP Statistics backend
==============================================
Data sources:
  Alliance API : https://kg.dbapp.ru/api/alliance/656f240a5af26c97
  Character API: https://kg.dbapp.ru/api/character/{player_id}
                 → powerHistory.diffDamage[]  (daily PvP damage per player)
                 → powerHistory.labels[]      (corresponding dates)

Cron schedule (all UTC):
  00:05 daily   → python stats_data.py roster
  00:10 daily   → python stats_data.py season
  */10  *       → python stats_data.py daily        (damage+score only)
  00:00 every 28 days (new season) → python stats_data.py archive

Score formula: score = damage_trillions / (power_billions ^ 0.6)
  power_billions  = maxPower / 1e9
  damage_trillions = pvpDamage / 1e12
"""

import json
import os
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
ALLIANCE_API  = "https://kg.dbapp.ru/api/alliance/656f240a5af26c97"
CHARACTER_API = "https://kg.dbapp.ru/api/character/{}"
DATA_DIR      = Path(__file__).parent / "data" / "stats"
REQUEST_DELAY = 0.3   # seconds between character API calls (be polite)
REQUEST_TIMEOUT = 10  # seconds per request

# Browser-like headers so kg.dbapp.ru treats requests as real browser visits
# and calculates fresh daily stats instead of returning cached data
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://kg.dbapp.ru/",
    "Origin": "https://kg.dbapp.ru",
}

SEASON_28_START = datetime(2026, 2, 16, tzinfo=timezone.utc)
SEASON_28_NUM   = 28
SEASON_LENGTH   = 28  # days

# ── Season helpers ───────────────────────────────────────────────────────────
def get_current_season():
    now = datetime.now(timezone.utc)
    offset = int((now - SEASON_28_START).days // SEASON_LENGTH)
    return SEASON_28_NUM + max(0, offset)

def get_season_start(season_num):
    """Return UTC date string for the start of a given season."""
    days_offset = (season_num - SEASON_28_NUM) * SEASON_LENGTH
    dt = SEASON_28_START + timedelta(days=days_offset)
    return dt.strftime("%Y-%m-%d")

def get_current_season_start():
    return get_season_start(get_current_season())

# ── Score formula ────────────────────────────────────────────────────────────
def calc_score(pvp_damage_raw, power_raw):
    """score = damage(T) / power(B)^0.6
    power_billions  = raw / 1e9
    damage_trillions = raw / 1e12
    """
    try:
        power_b  = float(power_raw) / 1_000_000_000
        damage_t = float(pvp_damage_raw) / 1_000_000_000_000
        if power_b <= 0:
            return 0.0
        return round(damage_t / (power_b ** 0.6), 2)
    except (ValueError, TypeError, ZeroDivisionError):
        return 0.0

def fmt_power(raw):
    """raw maxPower → billions  e.g. 254183.17"""
    try:
        return round(float(raw) / 1_000_000_000, 2)
    except (ValueError, TypeError):
        return 0.0

def fmt_damage(raw):
    """raw pvpDamage → trillions  e.g. 489193.21"""
    try:
        return round(float(raw) / 1_000_000_000_000, 2)
    except (ValueError, TypeError):
        return 0.0

# ── Data dir ─────────────────────────────────────────────────────────────────
def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

def save_json(filename, data):
    ensure_data_dir()
    path = DATA_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Saved {path}")

def load_json(filename, default=None):
    path = DATA_DIR / filename
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default

# ── Alliance fetch ────────────────────────────────────────────────────────────
def fetch_alliance():
    """Fetch alliance roster from the alliance API."""
    print("Fetching alliance data...")
    r = requests.get(ALLIANCE_API, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    members = [m for m in data["alliance"]["members"] if not m.get("deleted")]
    print(f"  Found {len(members)} active members")
    return members

# ── Character API fetch ───────────────────────────────────────────────────────
def wakeup_all_players(player_ids):
    """
    Pass 1: Visit each player's character page to trigger kg.dbapp.ru
    to calculate their daily stats. Then wait 60 seconds before fetching API.
    """
    print(f"  Waking up {len(player_ids)} player pages...")
    for i, pid in enumerate(player_ids):
        try:
            requests.get(f"https://kg.dbapp.ru/character/{pid}", headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
        except Exception:
            pass
        if (i + 1) % 10 == 0:
            print(f"    Woken up {i+1}/{len(player_ids)}...")
        time.sleep(REQUEST_DELAY)
    print(f"  All pages woken up. Waiting 60 seconds for kg.dbapp.ru to calculate...")
    time.sleep(60)


def fetch_character(player_id):
    """Fetch a single player's character data including powerHistory."""
    url = CHARACTER_API.format(player_id)
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def get_daily_damage(player_id, date_str):
    """
    Get a player's PvP damage for a specific date (YYYY-MM-DD).
    Returns raw damage value or 0 if not found.
    """
    try:
        data = fetch_character(player_id)
        ph = data.get("powerHistory", {})
        labels = ph.get("labels", [])
        diff_damage = ph.get("diffDamage", [])
        if date_str in labels:
            idx = labels.index(date_str)
            val = diff_damage[idx]
            if val and int(val) > 0:  # skip negative corrections
                return int(val)
        return 0
    except Exception as e:
        print(f"    Warning: could not fetch character {player_id}: {e}")
        return 0

def get_season_damage(player_id, season_start_str):
    """
    Sum all diffDamage entries from season_start_str to today.
    Returns raw total damage value.
    """
    try:
        data = fetch_character(player_id)
        ph = data.get("powerHistory", {})
        labels = ph.get("labels", [])
        diff_damage = ph.get("diffDamage", [])
        total = 0
        for i, label in enumerate(labels):
            if label >= season_start_str:
                val = diff_damage[i]
                if val and int(val) > 0:  # skip negative corrections
                    total += int(val)
        return total
    except Exception as e:
        print(f"    Warning: could not fetch character {player_id}: {e}")
        return 0

# ── update_roster ─────────────────────────────────────────────────────────────
def update_roster():
    """
    Runs at 00:05 UTC daily.
    Fetches alliance roster and saves basic player info.
    No character API calls needed — all data comes from alliance API.
    """
    print("\n=== update_roster ===")
    members = fetch_alliance()
    season = get_current_season()
    RANK_ORDER = {"R5": 5, "R4": 4, "R3": 3, "R2": 2, "R1": 1}

    players = []
    for m in members:
        players.append({
            "id":     m["id"],
            "name":   m["nickname"],
            "rank":   m.get("allianceRankName", "R1"),
            "power":  fmt_power(m.get("maxPower", 0)),
            "powerRaw": int(m.get("maxPower", 0)),
            "lastOnline": m.get("lastOnline", ""),
        })

    # Sort: rank desc, then power desc
    players.sort(
        key=lambda p: (RANK_ORDER.get(p["rank"], 0), p["power"]),
        reverse=True
    )

    save_json("roster.json", {
        "season": season,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "players": players
    })
    print(f"  Roster saved: {len(players)} players")


# ── update_daily ──────────────────────────────────────────────────────────────
def update_daily():
    """
    Runs every 10 minutes.
    Fetches today's PvP damage per player from the character API.
    130 players × ~0.3s delay ≈ ~40 seconds to complete.
    """
    print("\n=== update_daily ===")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Load roster for player list and power values
    roster_data = load_json("roster.json")
    if not roster_data:
        print("  No roster.json found — running update_roster first")
        update_roster()
        roster_data = load_json("roster.json")

    # Pass 1: wake up all player pages so kg.dbapp.ru calculates fresh stats
    player_ids = [p["id"] for p in roster_data["players"]]
    wakeup_all_players(player_ids)

    # Pass 2: fetch API data for all players
    players_out = []
    total = len(roster_data["players"])
    for i, p in enumerate(roster_data["players"]):
        print(f"  [{i+1}/{total}] {p['name']}...", end=" ", flush=True)
        daily_raw = get_daily_damage(p["id"], today)
        daily_t   = fmt_damage(daily_raw)
        score     = calc_score(daily_raw, p["powerRaw"])
        print(f"damage={daily_t}T score={score}")
        players_out.append({
            "id":       p["id"],
            "name":     p["name"],
            "rank":     p["rank"],
            "power":    p["power"],
            "dailyDamage": daily_t,
            "score":    score,
        })
        time.sleep(REQUEST_DELAY)

    # Sort by score desc
    players_out.sort(key=lambda p: p["score"], reverse=True)

    save_json("daily.json", {
        "date":    today,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "players": players_out
    })
    print(f"  Daily saved: {len(players_out)} players")


# ── update_season ─────────────────────────────────────────────────────────────
def update_season():
    """
    Runs at 00:10 UTC daily.
    Auto-detects season rollover and archives previous season before updating.
    Sums each player's diffDamage from season start to today.
    """
    print("\n=== update_season ===")
    season = get_current_season()
    season_start = get_current_season_start()
    print(f"  Season {season} starting {season_start}")

    # ── Auto-archive on season rollover ──────────────────────────────────────
    existing = load_json("season_current.json")
    if existing and existing.get("season") != season:
        print(f"  Season changed {existing['season']} → {season}! Auto-archiving...")
        archive_previous_season()

    roster_data = load_json("roster.json")
    if not roster_data:
        print("  No roster.json — running update_roster first")
        update_roster()
        roster_data = load_json("roster.json")

    # Pass 1: wake up all player pages so kg.dbapp.ru calculates fresh stats
    player_ids = [p["id"] for p in roster_data["players"]]
    wakeup_all_players(player_ids)

    # Pass 2: fetch API data for all players
    players_out = []
    total = len(roster_data["players"])
    for i, p in enumerate(roster_data["players"]):
        print(f"  [{i+1}/{total}] {p['name']}...", end=" ", flush=True)
        season_raw = get_season_damage(p["id"], season_start)
        season_t   = fmt_damage(season_raw)
        score      = calc_score(season_raw, p["powerRaw"])
        print(f"damage={season_t}T score={score}")
        players_out.append({
            "id":           p["id"],
            "name":         p["name"],
            "rank":         p["rank"],
            "power":        p["power"],
            "seasonDamage": season_t,
            "score":        score,
        })
        time.sleep(REQUEST_DELAY)

    players_out.sort(key=lambda p: p["score"], reverse=True)

    save_json("season_current.json", {
        "season":      season,
        "seasonStart": season_start,
        "updated":     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "players":     players_out
    })
    print(f"  Season saved: {len(players_out)} players")


# ── archive_previous_season ───────────────────────────────────────────────────
def archive_previous_season():
    """
    Runs once at the start of each new season (every 28 days at 00:00 UTC).
    Copies current season data to season_previous.json.
    Should be run BEFORE update_season on the new season's first day.
    """
    print("\n=== archive_previous_season ===")
    current = load_json("season_current.json")
    if not current:
        print("  No season_current.json to archive")
        return
    prev_season_num = current.get("season", get_current_season() - 1)
    save_json("season_previous.json", {
        **current,
        "archivedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "season": prev_season_num,
    })
    print(f"  Archived Season {prev_season_num} to season_previous.json")


# ── Flask routes ──────────────────────────────────────────────────────────────
def register_stats_routes(app):
    from flask import jsonify

    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        return response

    @app.route("/api/stats/roster")
    def api_stats_roster():
        data = load_json("roster.json", {"players": [], "updated": "never"})
        return no_cache(jsonify(data))

    @app.route("/api/stats/daily")
    def api_stats_daily():
        data = load_json("daily.json", {"players": [], "updated": "never"})
        return no_cache(jsonify(data))

    @app.route("/api/stats/season")
    def api_stats_season():
        data = load_json("season_current.json", {"players": [], "updated": "never"})
        return no_cache(jsonify(data))

    @app.route("/api/stats/previous")
    def api_stats_previous():
        data = load_json("season_previous.json", {"players": [], "updated": "never"})
        return no_cache(jsonify(data))


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "roster":
        update_roster()
    elif cmd == "daily":
        update_daily()
    elif cmd == "season":
        update_season()
    elif cmd == "archive":
        archive_previous_season()
    elif cmd == "all":
        # First run / full refresh
        update_roster()
        update_season()
        update_daily()
    else:
        print("""
Usage: python stats_data.py <command>

Commands:
  roster   — Refresh player roster from alliance API (daily at 00:05 UTC)
  daily    — Fetch today's PvP damage per player (every 10 min)
  season   — Sum season damage per player (daily at 00:10 UTC)
  archive  — Manually archive current season → previous (auto-called by season on rollover)
  all      — Run roster + season + daily (use for first-time setup)

Cron examples (3 lines, set and forget forever):
  5  0 * * *   cd /opt/9bot-repo && python web/stats_data.py roster
  10 0 * * *   cd /opt/9bot-repo && python web/stats_data.py season
  */10 * * * * cd /opt/9bot-repo && python web/stats_data.py daily

Season archiving is automatic — season job detects rollover and archives on its own.
        """)
