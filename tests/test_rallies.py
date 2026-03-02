"""Tests for protocol rally accessor and join_rally bail-out.

Tests cover:
- get_protocol_rallies() accessor (None vs [] vs list)
- join_rally() protocol early bail-out logic
"""
import time
import pytest
from unittest.mock import patch, MagicMock

import config
from actions.rallies import join_rally, _is_rally_owner_blacklisted, _blacklist_rally_owner


# ============================================================
# Helper: build Rally-like objects for testing
# ============================================================

def _make_rally(rally_state=1, max_num=5, troops=None, troop_id=1,
                 npcCity=None, playerCity=None):
    """Create a minimal Rally object for testing."""
    from protocol.messages import Rally, RallyTroopDetail
    troop_list = troops if troops is not None else []
    return Rally(
        rallyTroopID=troop_id,
        rallyState=rally_state,
        rallyMaxNum=max_num,
        troops=troop_list,
        npcCity=npcCity,
        playerCity=playerCity,
    )


def _make_troop_detail(name="Owner1"):
    """Create a RallyTroopDetail with a name."""
    from protocol.messages import RallyTroopDetail
    return RallyTroopDetail(name=name)


# ============================================================
# get_protocol_rallies() accessor tests
# ============================================================

class TestGetProtocolRallies:
    def test_returns_none_when_no_state(self):
        """No game state → None (fall through to UI)."""
        with patch("startup._game_state", None):
            from startup import get_protocol_rallies
            assert get_protocol_rallies() is None

    def test_returns_none_when_stale(self):
        """Stale rally data → None (fall through to UI)."""
        mock_state = MagicMock()
        mock_state.is_fresh.return_value = False
        with patch("startup._game_state", mock_state):
            from startup import get_protocol_rallies
            assert get_protocol_rallies() is None
        mock_state.is_fresh.assert_called_once_with("rallies", max_age_s=30.0)

    def test_returns_empty_list_when_no_rallies(self):
        """Fresh data, zero rallies → [] (bail-out signal)."""
        mock_state = MagicMock()
        mock_state.is_fresh.return_value = True
        mock_state.rallies = {}
        with patch("startup._game_state", mock_state):
            from startup import get_protocol_rallies
            result = get_protocol_rallies()
            assert result == []
            assert result is not None  # explicitly not None

    def test_returns_rally_list(self):
        """Fresh data with rallies → list of Rally objects."""
        rally = _make_rally()
        mock_state = MagicMock()
        mock_state.is_fresh.return_value = True
        mock_state.rallies = {1: rally}
        with patch("startup._game_state", mock_state):
            from startup import get_protocol_rallies
            result = get_protocol_rallies()
            assert len(result) == 1
            assert result[0] is rally


# ============================================================
# join_rally() protocol bail-out tests
# ============================================================

# Common patches for join_rally tests — mock everything except the protocol path
_JR_PATCHES = {
    "heal_all": MagicMock(),
    "troops_avail": MagicMock(return_value=3),
    "read_panel_statuses": MagicMock(return_value=None),
}


class TestJoinRallyProtocolBailout:
    """Protocol bail-out in join_rally(): early return when no joinable rallies."""

    def _run_join_rally(self, device, protocol_enabled, get_rallies_return,
                        navigate_called_check=True):
        """Helper: run join_rally with mocked protocol and track navigate calls."""
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch.object(config, "PROTOCOL_ENABLED", protocol_enabled), \
             patch("startup.get_protocol_rallies", return_value=get_rallies_return), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = protocol_enabled
            mock_config.get_device_config = config.get_device_config
            mock_nav.return_value = True
            result = join_rally(["titan", "eg"], device, skip_heal=True)
            return result, mock_nav

    def test_protocol_disabled_no_bailout(self, mock_device):
        """Protocol off → no bail-out, navigate to WAR called."""
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch("actions.rallies.load_screenshot", return_value=None), \
             patch.object(config, "PROTOCOL_ENABLED", False), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = False
            mock_config.get_device_config = config.get_device_config
            mock_nav.return_value = False  # fail navigate to end quickly
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False
            mock_nav.assert_called()

    def test_protocol_none_falls_through(self, mock_device):
        """Protocol on but returns None (stale) → fall through to UI."""
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch("actions.rallies.load_screenshot", return_value=None), \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=None), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            mock_nav.return_value = False
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False
            mock_nav.assert_called()

    def test_protocol_empty_bails_out(self, mock_device):
        """Protocol confirms zero rallies → bail out, navigate NOT called."""
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False
            mock_nav.assert_not_called()

    def test_protocol_all_full_bails_out(self, mock_device):
        """All rallies are full (troops == maxNum) → bail out."""
        full_rally = _make_rally(
            rally_state=1, max_num=3,
            troops=[_make_troop_detail("A"), _make_troop_detail("B"), _make_troop_detail("C")],
        )
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[full_rally]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False
            mock_nav.assert_not_called()

    def test_protocol_all_marching_bails_out(self, mock_device):
        """All rallies are marching (state=3) → not joinable → bail out."""
        marching_rally = _make_rally(rally_state=3, max_num=5, troops=[_make_troop_detail()])
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[marching_rally]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False
            mock_nav.assert_not_called()

    def test_protocol_has_joinable_proceeds(self, mock_device):
        """Joinable rally exists → don't bail out, navigate to WAR called."""
        joinable_rally = _make_rally(rally_state=1, max_num=5, troops=[_make_troop_detail("Good")],
                                     npcCity={"cfgID": 100})
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch("actions.rallies.load_screenshot", return_value=None), \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[joinable_rally]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            mock_nav.return_value = False  # fail navigate to end quickly
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False  # failed at navigate, but navigate WAS called
            mock_nav.assert_called()

    def test_protocol_all_blacklisted_bails_out(self, mock_device):
        """All joinable rallies have blacklisted owners → bail out."""
        _blacklist_rally_owner(mock_device, "BadOwner")
        rally = _make_rally(rally_state=2, max_num=5, troops=[_make_troop_detail("BadOwner")],
                            npcCity={"cfgID": 100})
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[rally]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False
            mock_nav.assert_not_called()

    def test_protocol_exception_falls_through(self, mock_device):
        """Exception in protocol path → silently fall through to UI."""
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch("actions.rallies.load_screenshot", return_value=None), \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", side_effect=RuntimeError("boom")), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            mock_nav.return_value = False
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False
            mock_nav.assert_called()  # fell through to UI

    def test_protocol_mixed_rallies_some_joinable(self, mock_device):
        """Mix of full + marching + joinable → don't bail out."""
        full = _make_rally(rally_state=1, max_num=2,
                           troops=[_make_troop_detail("A"), _make_troop_detail("B")],
                           troop_id=1, npcCity={"cfgID": 100})
        marching = _make_rally(rally_state=3, max_num=5,
                               troops=[_make_troop_detail("C")],
                               troop_id=2, npcCity={"cfgID": 100})
        joinable = _make_rally(rally_state=2, max_num=5,
                               troops=[_make_troop_detail("Good")],
                               troop_id=3, npcCity={"cfgID": 100})
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch("actions.rallies.load_screenshot", return_value=None), \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[full, marching, joinable]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            mock_nav.return_value = False
            result = join_rally(["titan"], mock_device, skip_heal=True)
            mock_nav.assert_called()  # proceeded to UI

    def test_protocol_rally_no_troops_not_blacklisted(self, mock_device):
        """Rally with empty troops list → can't check owner → not blacklisted."""
        empty_troops_rally = _make_rally(rally_state=1, max_num=5, troops=[],
                                         npcCity={"cfgID": 100})
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch("actions.rallies.load_screenshot", return_value=None), \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[empty_troops_rally]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            mock_nav.return_value = False
            result = join_rally(["titan"], mock_device, skip_heal=True)
            mock_nav.assert_called()  # not blacklisted → proceeds


# ============================================================
# Rally type filtering tests
# ============================================================

from actions.rallies import _rally_matches_target, _NPC_RALLY_TYPES, _PLAYER_RALLY_TYPES


class TestRallyMatchesTarget:
    """Unit tests for the _rally_matches_target helper."""

    def test_npc_rally_matches_when_want_npc(self):
        rally = _make_rally(npcCity={"cfgID": 100})
        assert _rally_matches_target(rally, want_npc=True, want_player=False) is True

    def test_npc_rally_rejected_when_want_player_only(self):
        rally = _make_rally(npcCity={"cfgID": 100})
        assert _rally_matches_target(rally, want_npc=False, want_player=True) is False

    def test_player_rally_matches_when_want_player(self):
        rally = _make_rally(playerCity={"cfgID": 200})
        assert _rally_matches_target(rally, want_npc=False, want_player=True) is True

    def test_player_rally_rejected_when_want_npc_only(self):
        rally = _make_rally(playerCity={"cfgID": 200})
        assert _rally_matches_target(rally, want_npc=True, want_player=False) is False

    def test_both_fields_match_npc(self):
        rally = _make_rally(npcCity={"cfgID": 100}, playerCity={"cfgID": 200})
        assert _rally_matches_target(rally, want_npc=True, want_player=False) is True

    def test_neither_field_rejects(self):
        rally = _make_rally()  # no npcCity, no playerCity
        assert _rally_matches_target(rally, want_npc=True, want_player=True) is False


class TestJoinRallyTypeFiltering:
    """Protocol bail-out filters rallies by NPC vs player target type."""

    def test_player_rally_filtered_when_npc_requested(self, mock_device):
        """Rally with only playerCity → filtered when requesting titan/eg → bail out."""
        player_rally = _make_rally(
            rally_state=1, max_num=5,
            troops=[_make_troop_detail("Good")],
            playerCity={"cfgID": 200},
        )
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[player_rally]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            result = join_rally(["titan", "eg"], mock_device, skip_heal=True)
            assert result is False
            mock_nav.assert_not_called()  # bailed out early

    def test_npc_rally_kept_when_npc_requested(self, mock_device):
        """Rally with npcCity set → passes filter → proceeds to UI."""
        npc_rally = _make_rally(
            rally_state=1, max_num=5,
            troops=[_make_troop_detail("Good")],
            npcCity={"cfgID": 100},
        )
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch("actions.rallies.load_screenshot", return_value=None), \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[npc_rally]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            mock_nav.return_value = False  # fail navigate to end quickly
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False  # failed at navigate, but navigate WAS called
            mock_nav.assert_called()

    def test_mixed_npc_and_player_only_npc_counted(self, mock_device):
        """Mix of player + NPC rallies → only NPC considered when requesting NPC types."""
        player_rally = _make_rally(
            rally_state=1, max_num=5,
            troops=[_make_troop_detail("P1")],
            playerCity={"cfgID": 200},
            troop_id=1,
        )
        npc_rally = _make_rally(
            rally_state=1, max_num=5,
            troops=[_make_troop_detail("N1")],
            npcCity={"cfgID": 100},
            troop_id=2,
        )
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch("actions.rallies.load_screenshot", return_value=None), \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[player_rally, npc_rally]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            mock_nav.return_value = False
            result = join_rally(["titan", "eg"], mock_device, skip_heal=True)
            # NPC rally passes filter → proceeds to UI (navigate called)
            mock_nav.assert_called()

    def test_only_player_rallies_all_filtered_bails_out(self, mock_device):
        """Multiple player rallies, requesting NPC → all filtered → bail out."""
        r1 = _make_rally(rally_state=1, max_num=5,
                         troops=[_make_troop_detail("A")],
                         playerCity={"cfgID": 201}, troop_id=1)
        r2 = _make_rally(rally_state=2, max_num=5,
                         troops=[_make_troop_detail("B")],
                         playerCity={"cfgID": 202}, troop_id=2)
        with patch.multiple("actions.rallies", **_JR_PATCHES), \
             patch("actions.rallies.navigate") as mock_nav, \
             patch.object(config, "PROTOCOL_ENABLED", True), \
             patch("startup.get_protocol_rallies", return_value=[r1, r2]), \
             patch("actions.rallies.config") as mock_config:
            mock_config.PROTOCOL_ENABLED = True
            mock_config.get_device_config = config.get_device_config
            result = join_rally(["titan"], mock_device, skip_heal=True)
            assert result is False
            mock_nav.assert_not_called()
