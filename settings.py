"""Shared settings persistence for 9Bot.

Provides load/save for settings.json with validation and defaults.
Used by both main.py (GUI) and web/dashboard.py (Flask).

Key exports:
    SETTINGS_FILE — absolute path to settings.json
    DEFAULTS      — default settings dict
    load_settings — load + validate + merge with defaults
    save_settings — write settings dict to JSON
"""

import json
import os
import tempfile

from botlog import get_logger
from config import validate_settings

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# Keys that can be overridden per device via device_settings.
DEVICE_OVERRIDABLE_KEYS = {
    "auto_heal", "auto_restore_ap", "ap_use_free", "ap_use_potions",
    "ap_allow_large_potions", "ap_use_gems", "ap_gem_limit", "min_troops",
    "my_team", "enemy_teams", "frontline_enemy_teams", "frontline_occupy_action",
    "gather_enabled",
    "gather_mine_level", "gather_max_troops",
    "tower_quest_enabled", "eg_rally_own", "titan_rally_own", "mithril_interval",
    "protocol_enabled", "home_x", "home_z", "max_reinforce_distance",
}

# ── Default territory zone data ──
# Map is fixed — zones are the same for everyone.  Only pass ownership changes.
# Passes default to unowned; user toggles during territory war.
# fmt: off
_DEFAULT_PASSES = {
    "1": {"name": "Fire North", "owned": False},
    "2": {"name": "Fire East", "owned": False},
    "3": {"name": "Earth South", "owned": False},
    "4": {"name": "Earth East", "owned": False},
    "5": {"name": "Forest West", "owned": False},
    "6": {"name": "Forest South", "owned": False},
    "7": {"name": "Ice West", "owned": False},
    "8": {"name": "Ice North", "owned": False},
}
_DEFAULT_MUTUAL_ZONES = {
    "fire_earth": [[7,5],[7,6],[7,7],[8,3],[8,4],[8,5],[8,6],[8,7],[8,8],[9,0],[9,1],[9,2],[9,3],[9,4],[9,5],[9,6],[9,7],[9,8],[10,0],[10,1],[10,2],[10,3],[10,4],[10,5],[10,6],[10,7],[10,8],[10,9],[11,0],[11,1],[11,2],[11,3],[11,4],[11,5],[11,6],[11,7],[11,8],[11,9],[11,10],[12,0],[12,1],[12,2],[12,3],[12,4],[12,5],[12,6],[12,7],[12,8],[12,9],[12,10],[13,0],[13,1],[13,2],[13,3],[13,4],[13,5],[13,6],[13,7],[13,8],[13,9],[13,10],[14,0],[14,1],[14,2],[14,3],[14,4],[14,5],[14,6],[14,7],[14,8],[14,9],[15,3],[15,4],[15,5],[15,6],[15,7],[15,8],[16,5],[16,6],[17,6]],
    "fire_ice": [[13,11],[13,12],[13,13],[14,10],[14,11],[14,12],[14,13],[14,14],[15,9],[15,10],[15,11],[15,12],[15,13],[15,14],[15,15],[16,7],[16,8],[16,9],[16,10],[16,11],[16,12],[16,13],[16,14],[16,16],[17,7],[17,8],[17,9],[17,10],[17,11],[17,12],[17,13],[17,14],[17,17],[18,7],[18,8],[18,9],[18,10],[18,11],[18,12],[18,13],[18,14],[19,8],[19,9],[19,10],[19,11],[19,12],[19,13],[19,14],[20,8],[20,9],[20,10],[20,11],[20,12],[20,13],[20,14],[21,9],[21,10],[21,11],[21,12],[21,13],[21,14],[22,9],[22,10],[22,11],[22,12],[22,13],[22,14],[23,9],[23,10],[23,11],[23,12],[23,13],[23,14]],
    "earth_forest": [[0,9],[0,10],[0,11],[0,12],[0,13],[0,14],[1,9],[1,10],[1,11],[1,12],[1,13],[1,14],[2,9],[2,10],[2,11],[2,12],[2,13],[2,14],[3,8],[3,9],[3,10],[3,11],[3,12],[3,13],[3,14],[3,15],[4,8],[4,9],[4,10],[4,11],[4,12],[4,13],[4,14],[4,15],[5,7],[5,8],[5,9],[5,10],[5,11],[5,12],[5,13],[5,14],[5,15],[5,16],[6,6],[6,7],[6,8],[6,9],[6,10],[6,11],[6,12],[6,13],[6,14],[6,15],[6,16],[7,7],[7,8],[7,9],[7,10],[7,11],[7,12],[7,13],[7,14],[7,15],[8,8],[8,9],[8,10],[8,11],[8,12],[8,13],[8,14],[9,9],[9,10],[9,11],[9,12],[9,13],[10,10],[10,11],[10,12]],
    "forest_ice": [[6,17],[7,16],[7,17],[7,18],[8,15],[8,16],[8,17],[8,18],[8,19],[8,20],[9,14],[9,15],[9,16],[9,17],[9,18],[9,19],[9,20],[9,21],[9,22],[9,23],[10,13],[10,14],[10,15],[10,16],[10,17],[10,18],[10,19],[10,20],[10,21],[10,22],[10,23],[11,13],[11,14],[11,15],[11,16],[11,17],[11,18],[11,19],[11,20],[11,21],[11,22],[11,23],[12,13],[12,14],[12,15],[12,16],[12,17],[12,18],[12,19],[12,20],[12,21],[12,22],[12,23]],
}
_DEFAULT_SAFE_ZONES = {
    "yellow": [[0,0],[0,1],[0,2],[0,3],[0,4],[1,0],[1,1],[1,2],[1,3],[1,4],[2,0],[2,1],[2,2],[2,3],[3,0],[3,1],[3,2],[4,0],[4,1]],
    "green": [[0,19],[0,20],[0,21],[0,22],[0,23],[1,19],[1,20],[1,21],[1,22],[1,23],[2,20],[2,21],[2,22],[2,23],[3,21],[3,22],[3,23],[4,22],[4,23]],
    "red": [[19,0],[19,1],[20,0],[20,1],[20,2],[21,0],[21,1],[21,2],[21,3],[22,0],[22,1],[22,2],[22,3],[22,4],[23,0],[23,1],[23,2],[23,3],[23,4]],
    "blue": [[19,22],[19,23],[20,21],[20,22],[20,23],[21,20],[21,21],[21,22],[21,23],[22,19],[22,20],[22,21],[22,22],[22,23],[23,19],[23,20],[23,21],[23,22],[23,23]],
}
_DEFAULT_HOME_ZONES = {
    "yellow": [[0,5],[0,6],[0,7],[0,8],[1,5],[1,6],[1,7],[1,8],[2,4],[2,5],[2,6],[2,7],[2,8],[3,3],[3,4],[3,5],[3,6],[3,7],[4,2],[4,3],[4,4],[4,5],[4,6],[4,7],[5,0],[5,1],[5,2],[5,3],[5,4],[5,5],[5,6],[6,0],[6,1],[6,2],[6,3],[6,4],[6,5],[7,0],[7,1],[7,2],[7,3],[7,4],[8,0],[8,1],[8,2]],
    "green": [[0,15],[0,16],[0,17],[0,18],[1,15],[1,16],[1,17],[1,18],[2,15],[2,16],[2,17],[2,18],[2,19],[3,16],[3,17],[3,18],[3,19],[3,20],[4,16],[4,17],[4,18],[4,19],[4,20],[4,21],[5,17],[5,18],[5,19],[5,20],[5,21],[5,22],[5,23],[6,18],[6,19],[6,20],[6,21],[6,22],[6,23],[7,19],[7,20],[7,21],[7,22],[7,23],[8,21],[8,22],[8,23]],
    "red": [[15,0],[15,1],[15,2],[16,0],[16,1],[16,2],[16,3],[16,4],[17,0],[17,1],[17,2],[17,3],[17,4],[17,5],[18,0],[18,1],[18,2],[18,3],[18,4],[18,5],[18,6],[19,2],[19,3],[19,4],[19,5],[19,6],[19,7],[20,3],[20,4],[20,5],[20,6],[20,7],[21,4],[21,5],[21,6],[21,7],[21,8],[22,5],[22,6],[22,7],[22,8],[23,5],[23,6],[23,7],[23,8]],
}
# fmt: on

DEFAULTS = {
    "auto_heal": True,
    "auto_restore_ap": False,
    "ap_use_free": True,
    "ap_use_potions": True,
    "ap_allow_large_potions": True,
    "ap_use_gems": False,
    "ap_gem_limit": 0,
    "min_troops": 0,
    "variation": 0,
    "titan_interval": 30,
    "groot_interval": 30,
    "reinforce_interval": 30,
    "pass_interval": 30,
    "pass_mode": "Rally Joiner",
    "my_team": "red",
    "enemy_teams": [],
    "mode": "bl",
    "verbose_logging": False,
    "eg_rally_own": True,
    "titan_rally_own": True,
    "mithril_interval": 19,
    "web_dashboard": False,
    "gather_enabled": True,
    "gather_mine_level": 4,
    "gather_max_troops": 3,
    "tower_quest_enabled": False,
    "remote_access": True,
    "auto_upload_logs": False,
    "upload_interval_hours": 24,
    "collect_training_data": False,
    "protocol_enabled": False,
    "home_x": 0,
    "home_z": 0,
    "max_reinforce_distance": 55,
    "chat_mirror": True,
    "chat_translate_enabled": False,
    "frontline_occupy_action": "reinforce",
    "frontline_enemy_teams": [],
    "chat_translate_api_key": "",
    "territory_passes": dict(_DEFAULT_PASSES),
    "territory_mutual_zones": {k: list(v) for k, v in _DEFAULT_MUTUAL_ZONES.items()},
    "territory_safe_zones": {k: list(v) for k, v in _DEFAULT_SAFE_ZONES.items()},
    "territory_home_zones": {k: list(v) for k, v in _DEFAULT_HOME_ZONES.items()},
}


def load_settings():
    """Load settings from disk, merging with defaults and validating."""
    _log = get_logger("settings")
    try:
        with open(SETTINGS_FILE, "r") as f:
            saved = json.load(f)
        merged = {**DEFAULTS, **saved}
        merged, warnings = validate_settings(merged, DEFAULTS)
        for w in warnings:
            _log.warning("Settings: %s", w)
        _log.info("Settings loaded (%d keys, %d from file)", len(merged), len(saved))
        return merged
    except FileNotFoundError:
        _log.info("No settings file found, using defaults (%d keys)", len(DEFAULTS))
        return dict(DEFAULTS)
    except json.JSONDecodeError as e:
        _log.warning("Settings file corrupted (%s), using defaults", e)
        return dict(DEFAULTS)


def save_settings(settings):
    """Write settings dict to settings.json."""
    _log = get_logger("settings")
    try:
        dir_name = os.path.dirname(SETTINGS_FILE)
        with tempfile.NamedTemporaryFile("w", dir=dir_name, suffix=".tmp",
                                         delete=False) as tmp:
            json.dump(settings, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, SETTINGS_FILE)
        _log.debug("Settings saved (%d keys)", len(settings))
    except Exception as e:
        _log.error("Failed to save settings: %s", e)
