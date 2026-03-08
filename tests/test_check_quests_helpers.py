"""Tests for check_quests helper functions extracted in Phase 2.

Tests _deduplicate_quests, _get_actionable_quests, _ocr_quest_rows,
_claim_quest_rewards, _wait_for_rallies, _run_rally_loop, _check_quests_legacy,
get_quest_tracking_state, get_quest_last_checked, _is_troop_in_building_relaxed.
"""
import time
import numpy as np
from unittest.mock import patch, MagicMock, call

from config import QuestType, Screen
from actions.quests import (_deduplicate_quests, _get_actionable_quests,
                            _all_quests_visually_complete, _quest_rallies_pending,
                            check_quests, _quest_last_seen, _quest_target,
                            _attack_pvp_tower, _pvp_last_dispatch, _PVP_COOLDOWN_S,
                            _marker_errors, _quest_pending_since,
                            _quest_last_checked,
                            get_quest_tracking_state, get_quest_last_checked,
                            _claim_quest_rewards, _is_troop_in_building_relaxed,
                            _wait_for_rallies, _run_rally_loop,
                            _check_quests_legacy, _ocr_quest_rows,
                            _recall_tap_sequence)


# ============================================================
# _deduplicate_quests
# ============================================================

class TestDeduplicateQuests:
    def test_single_quest_unchanged(self):
        quests = [{"quest_type": QuestType.TITAN, "current": 3, "target": 15}]
        result = _deduplicate_quests(quests)
        assert len(result) == 1
        assert result[0]["current"] == 3

    def test_keeps_most_remaining(self):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 14, "target": 15},  # 1 remaining
            {"quest_type": QuestType.TITAN, "current": 0, "target": 5},    # 5 remaining
        ]
        result = _deduplicate_quests(quests)
        assert len(result) == 1
        assert result[0]["current"] == 0
        assert result[0]["target"] == 5

    def test_different_types_kept(self):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 0, "target": 15},
            {"quest_type": QuestType.EVIL_GUARD, "current": 1, "target": 3},
        ]
        result = _deduplicate_quests(quests)
        assert len(result) == 2

    def test_three_of_same_type(self):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 14, "target": 15},  # 1 remaining
            {"quest_type": QuestType.TITAN, "current": 10, "target": 15},  # 5 remaining
            {"quest_type": QuestType.TITAN, "current": 0, "target": 5},    # 5 remaining (tie)
        ]
        result = _deduplicate_quests(quests)
        assert len(result) == 1
        # Should keep one of the 5-remaining entries
        assert result[0]["target"] - result[0]["current"] == 5

    def test_empty_list(self):
        assert _deduplicate_quests([]) == []

    def test_mixed_types_with_duplicates(self):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 14, "target": 15},
            {"quest_type": QuestType.EVIL_GUARD, "current": 0, "target": 3},
            {"quest_type": QuestType.TITAN, "current": 0, "target": 5},
            {"quest_type": QuestType.EVIL_GUARD, "current": 2, "target": 3},
        ]
        result = _deduplicate_quests(quests)
        assert len(result) == 2
        types = {q["quest_type"] for q in result}
        assert types == {QuestType.TITAN, QuestType.EVIL_GUARD}

    def test_completed_quest_kept_if_most_remaining(self):
        """Even if current >= target, it should be kept if it's the only entry for that type."""
        quests = [{"quest_type": QuestType.PVP, "current": 5, "target": 5}]
        result = _deduplicate_quests(quests)
        assert len(result) == 1

    def test_non_actionable_types_still_deduped(self):
        quests = [
            {"quest_type": QuestType.GATHER, "current": 0, "target": 5},
            {"quest_type": QuestType.GATHER, "current": 3, "target": 5},
        ]
        result = _deduplicate_quests(quests)
        assert len(result) == 1
        assert result[0]["current"] == 0  # kept the one with 5 remaining


# ============================================================
# _get_actionable_quests
# ============================================================

class TestGetActionableQuests:
    def test_filters_completed(self, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": True},
        ]
        assert _get_actionable_quests(mock_device, quests) == []

    def test_tower_fortress_are_actionable(self, mock_device):
        quests = [
            {"quest_type": QuestType.FORTRESS, "current": 0, "target": 30, "completed": False},
            {"quest_type": QuestType.TOWER, "current": 0, "target": 30, "completed": False},
        ]
        result = _get_actionable_quests(mock_device, quests)
        assert len(result) == 2

    def test_returns_actionable(self, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 3, "target": 15, "completed": False},
            {"quest_type": QuestType.EVIL_GUARD, "current": 1, "target": 3, "completed": False},
        ]
        result = _get_actionable_quests(mock_device, quests)
        assert len(result) == 2

    def test_excludes_zero_effective_remaining(self, mock_device):
        # Simulate pending rallies covering all remaining
        _quest_rallies_pending[(mock_device, QuestType.TITAN)] = 12
        quests = [
            {"quest_type": QuestType.TITAN, "current": 3, "target": 15, "completed": False},
        ]
        result = _get_actionable_quests(mock_device, quests)
        assert result == []

    def test_mixed_actionable_and_not(self, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": True},  # done
            {"quest_type": QuestType.EVIL_GUARD, "current": 1, "target": 3, "completed": False},  # actionable
            {"quest_type": QuestType.GATHER, "current": 0, "target": 1000000, "completed": False},  # actionable
            {"quest_type": QuestType.FORTRESS, "current": 0, "target": 30, "completed": False},  # actionable
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False},  # actionable
        ]
        result = _get_actionable_quests(mock_device, quests)
        assert len(result) == 4
        types = {q["quest_type"] for q in result}
        assert types == {QuestType.EVIL_GUARD, QuestType.GATHER, QuestType.PVP, QuestType.FORTRESS}

    def test_empty_list(self, mock_device):
        assert _get_actionable_quests(mock_device, []) == []

    def test_none_quest_type_filtered(self, mock_device):
        quests = [
            {"quest_type": None, "current": 0, "target": 5, "completed": False},
        ]
        assert _get_actionable_quests(mock_device, quests) == []


# ============================================================
# _all_quests_visually_complete
# ============================================================

class TestAllQuestsVisuallyComplete:
    def test_all_complete(self, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": False},
            {"quest_type": QuestType.EVIL_GUARD, "current": 3, "target": 3, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is True

    def test_completed_flag_counts(self, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": True},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is True

    def test_incomplete_quest_blocks(self, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 10, "target": 15, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is False

    def test_ignores_pending_rallies(self, mock_device):
        """Gold should mine even when pending rallies exist — only visual matters."""
        _quest_rallies_pending[(mock_device, QuestType.TITAN)] = 5
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is True

    @patch("actions.quests._is_troop_in_building_relaxed", return_value=True)
    def test_tower_ok_if_defending(self, mock_defending, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": False},
            {"quest_type": QuestType.TOWER, "current": 10, "target": 30, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is True

    @patch("actions.quests._is_troop_in_building_relaxed", return_value=False)
    def test_tower_blocks_if_not_defending(self, mock_defending, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": False},
            {"quest_type": QuestType.TOWER, "current": 10, "target": 30, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is False

    @patch("actions.quests._is_troop_in_building_relaxed", return_value=True)
    def test_fortress_ok_if_defending(self, mock_defending, mock_device):
        quests = [
            {"quest_type": QuestType.FORTRESS, "current": 5, "target": 30, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is True

    def test_empty_quests_returns_true(self, mock_device):
        assert _all_quests_visually_complete(mock_device, []) is True

    def test_unknown_type_ignored(self, mock_device):
        quests = [
            {"quest_type": None, "current": 0, "target": 5, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is True

    def test_mixed_complete_and_incomplete(self, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": False},
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is False


# ============================================================
# check_quests: gather blocked by pending rallies
# ============================================================

class TestGatherBlockedByPendingRallies:
    """Gather gold should NOT deploy while titan/EG rallies are in flight."""

    @patch("actions.quests._run_tower_quest")
    @patch("actions.farming.gather_gold_loop")
    @patch("actions.quests.navigate", return_value=True)
    @patch("actions.quests._claim_quest_rewards", return_value=0)
    @patch("actions.quests._ocr_quest_rows")
    def test_gather_blocked_when_titan_pending(self, mock_ocr, mock_claim,
                                                mock_nav, mock_gather,
                                                mock_tower, mock_device):
        """When titan has pending rallies and gather is actionable, should wait not gather."""
        # Titan at 18/20 with 2 pending rallies -> effective remaining 0
        _quest_rallies_pending[(mock_device, QuestType.TITAN)] = 2
        _quest_last_seen[(mock_device, QuestType.TITAN)] = 18
        _quest_target[(mock_device, QuestType.TITAN)] = 20

        mock_ocr.return_value = [
            {"quest_type": QuestType.TITAN, "current": 18, "target": 20, "completed": False,
             "text": "Defeat Titans(18/20)"},
            {"quest_type": QuestType.GATHER, "current": 0, "target": 1000000, "completed": False,
             "text": "Gather(0/200,000)"},
        ]

        with patch("actions.quests._wait_for_rallies") as mock_wait:
            check_quests(mock_device)
            # Should wait for rallies, NOT gather
            mock_wait.assert_called_once()
            mock_gather.assert_not_called()

    @patch("actions.quests._run_tower_quest")
    @patch("actions.farming.gather_gold_loop")
    @patch("actions.quests.navigate", return_value=True)
    @patch("actions.quests._claim_quest_rewards", return_value=0)
    @patch("actions.quests._ocr_quest_rows")
    def test_gather_proceeds_when_no_pending(self, mock_ocr, mock_claim,
                                              mock_nav, mock_gather,
                                              mock_tower, mock_device):
        """When no pending rallies, gather should proceed normally."""
        mock_ocr.return_value = [
            {"quest_type": QuestType.GATHER, "current": 0, "target": 1000000, "completed": False,
             "text": "Gather(0/200,000)"},
        ]

        check_quests(mock_device)
        mock_gather.assert_called_once()

    @patch("actions.quests._attack_pvp_tower")
    @patch("actions.quests._wait_for_rallies")
    @patch("actions.quests._run_tower_quest")
    @patch("actions.farming.gather_gold_loop")
    @patch("actions.quests.navigate", return_value=True)
    @patch("actions.quests._claim_quest_rewards", return_value=0)
    @patch("actions.quests._ocr_quest_rows")
    def test_pvp_dispatches_then_waits_when_rallies_pending(self, mock_ocr, mock_claim,
                                                              mock_nav, mock_gather,
                                                              mock_tower, mock_wait,
                                                              mock_pvp, mock_device):
        """PVP + gather with pending rallies: PVP dispatches, then waits (no gather)."""
        _quest_rallies_pending[(mock_device, QuestType.EVIL_GUARD)] = 1

        mock_ocr.return_value = [
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False,
             "text": "PvP(0/1)"},
            {"quest_type": QuestType.GATHER, "current": 0, "target": 1000000, "completed": False,
             "text": "Gather(0/200,000)"},
        ]

        check_quests(mock_device)
        # PVP should attempt attack
        mock_pvp.assert_called_once()
        # Gather should NOT run — pending rallies block it
        mock_gather.assert_not_called()
        # Should wait for pending rallies instead
        mock_wait.assert_called_once()


# ============================================================
# _all_quests_visually_complete: PVP cooldown awareness
# ============================================================

class TestAllQuestsVisuallyCompletePVP:
    def test_pvp_on_cooldown_is_ok(self, mock_device):
        """PVP quest incomplete but troop recently dispatched — don't block gold."""
        _pvp_last_dispatch[mock_device] = time.time() - 60  # 1 min ago
        quests = [
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False},
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is True

    def test_pvp_no_cooldown_blocks(self, mock_device):
        """PVP quest incomplete and no recent dispatch — blocks gold mining."""
        quests = [
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False},
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is False

    def test_pvp_cooldown_expired_blocks(self, mock_device):
        """PVP cooldown expired — quest blocks again."""
        _pvp_last_dispatch[mock_device] = time.time() - (_PVP_COOLDOWN_S + 10)
        quests = [
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is False


# ============================================================
# _attack_pvp_tower: handler unit tests
# ============================================================

class TestAttackPvpTower:
    def _time_gen(self, *values):
        """Return a time.time mock that yields values then counts from last."""
        vals = list(values)
        counter = [vals[-1] if vals else 0.0]
        def fake():
            if vals:
                return vals.pop(0)
            counter[0] += 0.5
            return counter[0]
        return fake

    @patch("actions.quests.wait_for_image_and_tap", return_value=True)
    @patch("actions.quests.tap_image", return_value=True)
    @patch("actions.quests.find_image")
    @patch("actions.quests.load_screenshot", return_value=MagicMock())
    @patch("actions.quests.logged_tap")
    @patch("actions.quests.save_failure_screenshot")
    @patch("actions.quests.config")
    @patch("actions.quests.troops_avail", return_value=3)
    def test_success_full_flow(self, mock_troops, mock_config, mock_save,
                                mock_ltap, mock_load, mock_find,
                                mock_tap, mock_wait_tap, mock_device):
        """Happy path: target succeeds, attack button found, depart found."""
        mock_config.set_device_status = MagicMock()
        def find_side_effect(screen, image, threshold=0.8):
            if image == "attack_button.png":
                return (0.9, (100, 100), 50, 200)
            return None
        mock_find.side_effect = find_side_effect
        with patch("actions.combat.target", return_value=True), \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=self._time_gen(1000, 1000, 1000.5)):
            result = _attack_pvp_tower(mock_device)
            assert result is True
            assert mock_device in _pvp_last_dispatch

    def test_cooldown_skips(self, mock_device):
        """Recent dispatch within cooldown — should skip without calling target."""
        _pvp_last_dispatch[mock_device] = time.time() - 60  # 1 min ago
        with patch("actions.combat.target") as mock_target:
            result = _attack_pvp_tower(mock_device)
            assert result is False
            mock_target.assert_not_called()

    @patch("actions.quests.config")
    @patch("actions.quests.troops_avail", return_value=0)
    def test_no_troops_skips(self, mock_troops, mock_config, mock_device):
        """Zero troops available — skip after target() but before tapping tower."""
        mock_config.set_device_status = MagicMock()
        with patch("actions.combat.target", return_value=True):
            result = _attack_pvp_tower(mock_device)
            assert result is False

    @patch("actions.quests.save_failure_screenshot")
    @patch("actions.quests.config")
    def test_target_fails(self, mock_config, mock_save, mock_device):
        """target() returns False — should save screenshot and return False."""
        mock_config.set_device_status = MagicMock()
        with patch("actions.combat.target", return_value=False):
            result = _attack_pvp_tower(mock_device)
            assert result is False
            mock_save.assert_called_once_with(mock_device, "pvp_target_fail")

    @patch("actions.quests.save_failure_screenshot")
    @patch("actions.quests.config")
    def test_target_no_marker(self, mock_config, mock_save, mock_device):
        """target() returns 'no_marker' (truthy!) — should still fail."""
        mock_config.set_device_status = MagicMock()
        with patch("actions.combat.target", return_value="no_marker"):
            result = _attack_pvp_tower(mock_device)
            assert result is False
            mock_save.assert_called_once_with(mock_device, "pvp_target_fail")

    @patch("actions.quests.find_image", return_value=None)
    @patch("actions.quests.load_screenshot", return_value=MagicMock())
    @patch("actions.quests.logged_tap")
    @patch("actions.quests.save_failure_screenshot")
    @patch("actions.quests.config")
    @patch("actions.quests.troops_avail", return_value=3)
    def test_attack_menu_miss(self, mock_troops, mock_config, mock_save,
                               mock_ltap, mock_load, mock_find, mock_device):
        """Neither attack nor reinforce button found — save screenshot."""
        mock_config.set_device_status = MagicMock()
        # Time values: cooldown check (1000), start_time (1000), then loop iterations
        times = [1000, 1000] + [1000 + i * 0.5 for i in range(1, 25)]
        with patch("actions.combat.target", return_value=True), \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=self._time_gen(*times)):
            result = _attack_pvp_tower(mock_device)
            assert result is False
            mock_save.assert_called_once_with(mock_device, "pvp_attack_menu_fail")

    @patch("actions.quests.save_failure_screenshot")
    @patch("actions.quests.wait_for_image_and_tap", return_value=False)
    @patch("actions.quests.find_image")
    @patch("actions.quests.load_screenshot", return_value=MagicMock())
    @patch("actions.quests.logged_tap")
    @patch("actions.quests.tap_image", return_value=True)
    @patch("actions.quests.config")
    @patch("actions.quests.troops_avail", return_value=3)
    def test_depart_miss(self, mock_troops, mock_config, mock_tap,
                          mock_ltap, mock_load, mock_find,
                          mock_wait_tap, mock_save, mock_device):
        """Depart button not found — save screenshot."""
        mock_config.set_device_status = MagicMock()
        def find_side_effect(screen, image, threshold=0.8):
            if image == "attack_button.png":
                return (0.9, (100, 100), 50, 200)
            return None
        mock_find.side_effect = find_side_effect
        with patch("actions.combat.target", return_value=True), \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=self._time_gen(1000, 1000, 1000.5)):
            result = _attack_pvp_tower(mock_device)
            assert result is False
            mock_save.assert_called_once_with(mock_device, "pvp_depart_fail")

    @patch("actions.quests.config")
    @patch("actions.quests.troops_avail", return_value=3)
    def test_respects_stop_check_after_target(self, mock_troops, mock_config,
                                               mock_device):
        """Stop check fires after target() — should abort before attack menu."""
        mock_config.set_device_status = MagicMock()
        stop = MagicMock(return_value=True)  # stop immediately
        with patch("actions.combat.target", return_value=True):
            result = _attack_pvp_tower(mock_device, stop_check=stop)
            assert result is False

    @patch("actions.quests.save_failure_screenshot")
    @patch("actions.quests.config")
    def test_duplicate_markers_sets_error(self, mock_config, mock_save, mock_device):
        """target() returns 'duplicate_markers' → error status, saved to _marker_errors."""
        mock_config.set_device_status = MagicMock()
        with patch("actions.combat.target", return_value="duplicate_markers"):
            result = _attack_pvp_tower(mock_device)
            assert result is False
            mock_config.set_device_status.assert_called_with(
                mock_device, "ERROR: Duplicate Enemy Markers!")
            assert mock_device in _marker_errors
            assert any("duplicate" in e.lower() for e in _marker_errors[mock_device])

    def test_marker_error_skips_immediately(self, mock_device):
        """If marker error already set, _attack_pvp_tower returns False immediately."""
        _marker_errors[mock_device] = {"PVP ERROR: Multiple enemy markers set"}
        with patch("actions.combat.target") as mock_target:
            result = _attack_pvp_tower(mock_device)
            assert result is False
            mock_target.assert_not_called()

    @patch("actions.quests.find_image")
    @patch("actions.quests.load_screenshot", return_value=MagicMock())
    @patch("actions.quests.logged_tap")
    @patch("actions.quests.save_failure_screenshot")
    @patch("actions.quests.config")
    @patch("actions.quests.troops_avail", return_value=3)
    def test_wrong_button_reinforce_found(self, mock_troops, mock_config,
                                           mock_save, mock_tap, mock_load,
                                           mock_find, mock_device):
        """Reinforce button found instead of attack → wrong tower, sets error."""
        mock_config.set_device_status = MagicMock()
        def find_side_effect(screen, image, threshold=0.8):
            if image == "territory_reinforce.png":
                return (0.9, (100, 100), 50, 200)
            return None
        mock_find.side_effect = find_side_effect
        with patch("actions.combat.target", return_value=True), \
             patch("actions.quests.time.sleep"), \
             patch("actions.quests.time.time", side_effect=self._time_gen(1000, 1000, 1000.5)):
            result = _attack_pvp_tower(mock_device)
            assert result is False
            mock_config.set_device_status.assert_any_call(
                mock_device, "ERROR: Enemy Marker \u2192 Friendly Tower!")
            assert mock_device in _marker_errors


# ============================================================
# check_quests: PVP dispatch integration
# ============================================================

class TestPvpDispatchIntegration:
    @patch("actions.quests._attack_pvp_tower")
    @patch("actions.quests._run_tower_quest")
    @patch("actions.farming.gather_gold_loop")
    @patch("actions.quests.navigate", return_value=True)
    @patch("actions.quests._claim_quest_rewards", return_value=0)
    @patch("actions.quests._ocr_quest_rows")
    def test_pvp_dispatches_when_actionable(self, mock_ocr, mock_claim,
                                              mock_nav, mock_gather,
                                              mock_tower, mock_pvp,
                                              mock_device):
        """PVP quest triggers _attack_pvp_tower; gather runs after successful dispatch."""
        # Simulate successful PVP dispatch: sets _pvp_last_dispatch
        from actions.quests import _pvp_last_dispatch
        def pvp_side_effect(device, stop_check=None):
            _pvp_last_dispatch[device] = time.time()
            return True
        mock_pvp.side_effect = pvp_side_effect

        mock_ocr.return_value = [
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False,
             "text": "PvP(0/1)"},
            {"quest_type": QuestType.GATHER, "current": 0, "target": 1000000, "completed": False,
             "text": "Gather(0/200,000)"},
        ]

        check_quests(mock_device)
        mock_pvp.assert_called_once()
        # Gather should run because PVP was successfully dispatched (on cooldown)
        mock_gather.assert_called_once()
        # Should reserve 1 troop for PVP retry
        _, kwargs = mock_gather.call_args
        assert kwargs.get("reserve") == 1

    @patch("actions.quests._attack_pvp_tower", return_value=False)
    @patch("actions.quests._run_tower_quest")
    @patch("actions.farming.gather_gold_loop")
    @patch("actions.quests.navigate", return_value=True)
    @patch("actions.quests._claim_quest_rewards", return_value=0)
    @patch("actions.quests._ocr_quest_rows")
    def test_pvp_failure_blocks_gold_mining(self, mock_ocr, mock_claim,
                                              mock_nav, mock_gather,
                                              mock_tower, mock_pvp,
                                              mock_device):
        """When PVP dispatch fails, gold mining should NOT run."""
        mock_ocr.return_value = [
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False,
             "text": "PvP(0/1)"},
            {"quest_type": QuestType.GATHER, "current": 0, "target": 1000000, "completed": False,
             "text": "Gather(0/200,000)"},
        ]

        check_quests(mock_device)
        mock_pvp.assert_called_once()
        # Gather should NOT run — PVP is available but not dispatched
        mock_gather.assert_not_called()

    @patch("actions.quests._attack_pvp_tower")
    @patch("actions.quests._run_rally_loop", return_value=False)
    @patch("actions.quests._run_tower_quest")
    @patch("actions.farming.gather_gold_loop")
    @patch("actions.quests.navigate", return_value=True)
    @patch("actions.quests._claim_quest_rewards", return_value=0)
    @patch("actions.quests._ocr_quest_rows")
    def test_pvp_runs_before_rallies(self, mock_ocr, mock_claim, mock_nav,
                                       mock_gather, mock_tower,
                                       mock_rally_loop, mock_pvp,
                                       mock_device):
        """PVP runs before rally loop when both PVP and titan quests present."""
        mock_ocr.return_value = [
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False,
             "text": "PvP(0/1)"},
            {"quest_type": QuestType.TITAN, "current": 0, "target": 15, "completed": False,
             "text": "Defeat Titans(0/15)"},
        ]

        check_quests(mock_device)
        mock_pvp.assert_called_once()
        mock_rally_loop.assert_called_once()

    @patch("actions.quests._attack_pvp_tower")
    @patch("actions.quests._run_tower_quest")
    @patch("actions.farming.gather_gold_loop")
    @patch("actions.quests.navigate", return_value=True)
    @patch("actions.quests._claim_quest_rewards", return_value=0)
    @patch("actions.quests._ocr_quest_rows")
    def test_pvp_on_cooldown_still_gathers(self, mock_ocr, mock_claim,
                                              mock_nav, mock_gather,
                                              mock_tower, mock_pvp,
                                              mock_device):
        """PVP on cooldown falls through to gather."""
        mock_pvp.return_value = False  # cooldown skip
        # Simulate PVP was dispatched recently (on cooldown)
        _pvp_last_dispatch[mock_device] = time.time()
        mock_ocr.return_value = [
            {"quest_type": QuestType.PVP, "current": 0, "target": 1, "completed": False,
             "text": "PvP(0/1)"},
            {"quest_type": QuestType.GATHER, "current": 0, "target": 1000000, "completed": False,
             "text": "Gather(0/200,000)"},
        ]

        check_quests(mock_device)
        mock_pvp.assert_called_once()
        mock_gather.assert_called_once()
        # Should reserve 1 troop for PVP retry
        _, kwargs = mock_gather.call_args
        assert kwargs.get("reserve") == 1


# ============================================================
# _eg_troops_available
# ============================================================

class TestEgTroopsAvailable:
    def test_all_home_passes(self, mock_device):
        """5 troops at home — plenty available for EG."""
        from actions.quests import _eg_troops_available
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        snapshot = DeviceTroopSnapshot(
            device=mock_device,
            troops=[TroopStatus(action=TroopAction.HOME) for _ in range(5)],
        )
        with patch("actions.quests.troops_avail", return_value=5), \
             patch("actions.quests.get_troop_status", return_value=snapshot):
            assert _eg_troops_available(mock_device) is True

    def test_four_gathering_one_home_fails(self, mock_device):
        """4 gathering + 1 home = only 1 non-tied-up troop, need 2."""
        from actions.quests import _eg_troops_available
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.GATHERING) for _ in range(4)]
        troops.append(TroopStatus(action=TroopAction.HOME))
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)
        with patch("actions.quests.troops_avail", return_value=2), \
             patch("actions.quests.get_troop_status", return_value=snapshot):
            assert _eg_troops_available(mock_device) is False

    def test_three_gathering_two_rallying_passes(self, mock_device):
        """3 gathering + 2 rallying = 2 non-tied-up. Rallying counts as available."""
        from actions.quests import _eg_troops_available
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.GATHERING) for _ in range(3)]
        troops += [TroopStatus(action=TroopAction.RALLYING) for _ in range(2)]
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)
        with patch("actions.quests.troops_avail", return_value=2), \
             patch("actions.quests.get_troop_status", return_value=snapshot):
            assert _eg_troops_available(mock_device) is True

    def test_defending_counts_as_tied_up(self, mock_device):
        """3 gathering + 1 defending + 1 home = only 2 non-tied-up, but barely passes."""
        from actions.quests import _eg_troops_available
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.GATHERING) for _ in range(3)]
        troops.append(TroopStatus(action=TroopAction.DEFENDING))
        troops.append(TroopStatus(action=TroopAction.HOME))
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)
        with patch("actions.quests.troops_avail", return_value=2), \
             patch("actions.quests.get_troop_status", return_value=snapshot):
            # 5 total - 3 gathering - 1 defending = 1 available → fails
            assert _eg_troops_available(mock_device) is False

    def test_marching_returning_count_as_available(self, mock_device):
        """Marching + returning troops count as available."""
        from actions.quests import _eg_troops_available
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [
            TroopStatus(action=TroopAction.GATHERING),
            TroopStatus(action=TroopAction.GATHERING),
            TroopStatus(action=TroopAction.GATHERING),
            TroopStatus(action=TroopAction.MARCHING),
            TroopStatus(action=TroopAction.RETURNING),
        ]
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)
        with patch("actions.quests.troops_avail", return_value=2), \
             patch("actions.quests.get_troop_status", return_value=snapshot):
            assert _eg_troops_available(mock_device) is True

    def test_no_snapshot_falls_back_to_troops_avail(self, mock_device):
        """When no snapshot available, falls back to troops_avail."""
        from actions.quests import _eg_troops_available
        with patch("actions.quests.get_troop_status", return_value=None), \
             patch("actions.quests.troops_avail", return_value=2):
            assert _eg_troops_available(mock_device) is True

    def test_no_snapshot_fallback_fails(self, mock_device):
        """Fallback to troops_avail with only 1 troop available."""
        from actions.quests import _eg_troops_available
        with patch("actions.quests.get_troop_status", return_value=None), \
             patch("actions.quests.troops_avail", return_value=1):
            assert _eg_troops_available(mock_device) is False

    def test_pixel_check_catches_stale_snapshot(self, mock_device):
        """Snapshot says troops are home but pixel check shows 0 — stale snapshot."""
        from actions.quests import _eg_troops_available
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        snapshot = DeviceTroopSnapshot(
            device=mock_device,
            troops=[TroopStatus(action=TroopAction.HOME) for _ in range(5)],
        )
        with patch("actions.quests.troops_avail", return_value=0), \
             patch("actions.quests.get_troop_status", return_value=snapshot):
            assert _eg_troops_available(mock_device) is False


# ============================================================
# _recall_stray_stationed
# ============================================================

class TestRecallStrayStationed:
    def test_no_stationed_noop(self, mock_device):
        """No stationed troops — should not tap anything."""
        from actions.quests import _recall_stray_stationed
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        snapshot = DeviceTroopSnapshot(
            device=mock_device,
            troops=[TroopStatus(action=TroopAction.HOME) for _ in range(5)],
        )
        with patch("actions.quests.get_troop_status", return_value=snapshot), \
             patch("actions.quests.tap_image") as mock_tap:
            _recall_stray_stationed(mock_device)
            mock_tap.assert_not_called()

    def test_no_snapshot_noop(self, mock_device):
        """No snapshot available — should not tap anything."""
        from actions.quests import _recall_stray_stationed
        with patch("actions.quests.get_troop_status", return_value=None), \
             patch("actions.quests.tap_image") as mock_tap:
            _recall_stray_stationed(mock_device)
            mock_tap.assert_not_called()

    def test_stationed_recall_success(self, mock_device):
        """Stationed troop detected — full recall sequence."""
        from actions.quests import _recall_stray_stationed
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.HOME) for _ in range(4)]
        troops.append(TroopStatus(action=TroopAction.STATIONING))
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)

        with patch("actions.quests.get_troop_status", return_value=snapshot), \
             patch("actions.quests.tap_image", return_value=True) as mock_tap, \
             patch("actions.quests.wait_for_image_and_tap", return_value=True) as mock_wait_tap, \
             patch("time.sleep"):
            _recall_stray_stationed(mock_device)
            # Should tap stationing icon on panel
            mock_tap.assert_called_once_with("statuses/stationing.png", mock_device)
            # Should tap stationed marker then return button
            assert mock_wait_tap.call_count == 2
            mock_wait_tap.assert_any_call("stationed.png", mock_device, timeout=3)
            mock_wait_tap.assert_any_call("return.png", mock_device, timeout=3)

    def test_stationed_recall_no_panel_icon(self, mock_device):
        """Stationing icon not found on panel — aborts early."""
        from actions.quests import _recall_stray_stationed
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.STATIONING)]
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)

        with patch("actions.quests.get_troop_status", return_value=snapshot), \
             patch("actions.quests.tap_image", return_value=False), \
             patch("actions.quests.wait_for_image_and_tap") as mock_wait_tap:
            _recall_stray_stationed(mock_device)
            mock_wait_tap.assert_not_called()

    def test_stationed_recall_no_map_marker(self, mock_device):
        """Stationing icon found but stationed marker not on map."""
        from actions.quests import _recall_stray_stationed
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.STATIONING)]
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)

        with patch("actions.quests.get_troop_status", return_value=snapshot), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.wait_for_image_and_tap", return_value=False) as mock_wait_tap, \
             patch("actions.quests.save_failure_screenshot"), \
             patch("time.sleep"):
            _recall_stray_stationed(mock_device)
            # Only tried stationed.png, never got to return.png
            mock_wait_tap.assert_called_once_with("stationed.png", mock_device, timeout=3)

    def test_respects_stop_check(self, mock_device):
        """Stop check after panel tap aborts before map marker tap."""
        from actions.quests import _recall_stray_stationed
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.STATIONING)]
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)

        with patch("actions.quests.get_troop_status", return_value=snapshot), \
             patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.wait_for_image_and_tap") as mock_wait_tap, \
             patch("time.sleep"):
            _recall_stray_stationed(mock_device, stop_check=lambda: True)
            mock_wait_tap.assert_not_called()


# ============================================================
# get_quest_tracking_state
# ============================================================

class TestGetQuestTrackingState:
    def test_no_data_returns_empty(self, mock_device):
        assert get_quest_tracking_state(mock_device) == []

    def test_pending_rallies_included(self, mock_device):
        _quest_rallies_pending[(mock_device, QuestType.TITAN)] = 3
        _quest_pending_since[(mock_device, QuestType.TITAN)] = time.time() - 30
        _quest_last_seen[(mock_device, QuestType.TITAN)] = 5
        _quest_target[(mock_device, QuestType.TITAN)] = 15
        result = get_quest_tracking_state(mock_device)
        assert len(result) == 1
        assert result[0]["quest_type"] == str(QuestType.TITAN)
        assert result[0]["pending"] == 3
        assert result[0]["last_seen"] == 5
        assert result[0]["target"] == 15
        assert result[0]["pending_age"] is not None

    def test_last_seen_without_pending(self, mock_device):
        """Quest types with last_seen but no pending are included with pending=0."""
        _quest_last_seen[(mock_device, QuestType.GATHER)] = 100
        _quest_target[(mock_device, QuestType.GATHER)] = 200000
        result = get_quest_tracking_state(mock_device)
        assert len(result) == 1
        assert result[0]["pending"] == 0
        assert result[0]["pending_age"] is None
        assert result[0]["last_seen"] == 100

    def test_other_device_excluded(self, mock_device, mock_device_b):
        _quest_last_seen[(mock_device_b, QuestType.TITAN)] = 10
        result = get_quest_tracking_state(mock_device)
        assert result == []

    def test_mixed_pending_and_seen(self, mock_device):
        """Both pending and non-pending quest types for same device."""
        _quest_rallies_pending[(mock_device, QuestType.TITAN)] = 2
        _quest_pending_since[(mock_device, QuestType.TITAN)] = time.time()
        _quest_last_seen[(mock_device, QuestType.TITAN)] = 8
        _quest_last_seen[(mock_device, QuestType.GATHER)] = 50
        result = get_quest_tracking_state(mock_device)
        types = {r["quest_type"] for r in result}
        assert len(result) == 2
        assert str(QuestType.TITAN) in types
        assert str(QuestType.GATHER) in types


# ============================================================
# get_quest_last_checked
# ============================================================

class TestGetQuestLastChecked:
    def test_never_checked_returns_none(self, mock_device):
        assert get_quest_last_checked(mock_device) is None

    def test_recently_checked(self, mock_device):
        _quest_last_checked[mock_device] = time.time() - 30
        result = get_quest_last_checked(mock_device)
        assert result is not None
        assert 29 <= result <= 31

    def test_other_device_not_found(self, mock_device, mock_device_b):
        _quest_last_checked[mock_device_b] = time.time()
        assert get_quest_last_checked(mock_device) is None


# ============================================================
# _ocr_quest_rows
# ============================================================

class TestOcrQuestRows:
    def _make_screen(self):
        return np.zeros((1920, 1080, 3), dtype=np.uint8)

    @patch("actions.quests.load_screenshot", return_value=None)
    def test_no_screenshot_returns_none(self, mock_load, mock_device):
        assert _ocr_quest_rows(mock_device) is None

    @patch("actions.quests.cv2")
    @patch("actions.quests.load_screenshot")
    def test_empty_ocr_returns_none(self, mock_load, mock_cv2, mock_device):
        mock_load.return_value = self._make_screen()
        mock_cv2.cvtColor.return_value = np.zeros((100, 100), dtype=np.uint8)
        mock_cv2.resize.return_value = np.zeros((200, 200), dtype=np.uint8)
        mock_cv2.imwrite = MagicMock()
        with patch("vision.ocr_read", return_value=[""]):
            assert _ocr_quest_rows(mock_device) is None

    @patch("actions.quests.cv2")
    @patch("actions.quests.load_screenshot")
    def test_basic_titan_parse(self, mock_load, mock_cv2, mock_device):
        mock_load.return_value = self._make_screen()
        mock_cv2.cvtColor.return_value = np.zeros((100, 100), dtype=np.uint8)
        mock_cv2.resize.return_value = np.zeros((200, 200), dtype=np.uint8)
        mock_cv2.imwrite = MagicMock()
        with patch("vision.ocr_read", return_value=["Defeat Titans(3/5)"]):
            result = _ocr_quest_rows(mock_device)
        assert result is not None
        assert len(result) == 1
        assert result[0]["quest_type"] == QuestType.TITAN
        assert result[0]["current"] == 3
        # Target capped to 15 (OCR showed 5 but cap overrides)
        assert result[0]["target"] == 15
        assert result[0]["completed"] is False

    @patch("actions.quests.cv2")
    @patch("actions.quests.load_screenshot")
    def test_ocr_o_to_zero_fix(self, mock_load, mock_cv2, mock_device):
        """OCR reads 'o'/'O' as digits → corrected to '0'."""
        mock_load.return_value = self._make_screen()
        mock_cv2.cvtColor.return_value = np.zeros((100, 100), dtype=np.uint8)
        mock_cv2.resize.return_value = np.zeros((200, 200), dtype=np.uint8)
        mock_cv2.imwrite = MagicMock()
        with patch("vision.ocr_read", return_value=["Gather(o/2OO,OOO)"]):
            result = _ocr_quest_rows(mock_device)
        assert result is not None
        assert result[0]["quest_type"] == QuestType.GATHER
        assert result[0]["current"] == 0
        assert result[0]["target"] == 1000000  # cap override

    @patch("actions.quests.cv2")
    @patch("actions.quests.load_screenshot")
    def test_multiple_quests(self, mock_load, mock_cv2, mock_device):
        mock_load.return_value = self._make_screen()
        mock_cv2.cvtColor.return_value = np.zeros((100, 100), dtype=np.uint8)
        mock_cv2.resize.return_value = np.zeros((200, 200), dtype=np.uint8)
        mock_cv2.imwrite = MagicMock()
        with patch("vision.ocr_read", return_value=[
            "Defeat Titans(3/15) Evil Guard(1/3) Gather(50000/200,000)"
        ]):
            result = _ocr_quest_rows(mock_device)
        assert len(result) == 3
        types = [q["quest_type"] for q in result]
        assert QuestType.TITAN in types
        assert QuestType.EVIL_GUARD in types
        assert QuestType.GATHER in types

    @patch("actions.quests.cv2")
    @patch("actions.quests.load_screenshot")
    def test_target_cap_overrides(self, mock_load, mock_cv2, mock_device):
        """All target caps applied correctly."""
        mock_load.return_value = self._make_screen()
        mock_cv2.cvtColor.return_value = np.zeros((100, 100), dtype=np.uint8)
        mock_cv2.resize.return_value = np.zeros((200, 200), dtype=np.uint8)
        mock_cv2.imwrite = MagicMock()
        with patch("vision.ocr_read", return_value=[
            "Defeat Titans(3/5) Evil Guard(1/1) "
            "Occupy Fortress(100/300) Attack Enemy(0/1) Gather(0/5)"
        ]):
            result = _ocr_quest_rows(mock_device)
        caps = {q["quest_type"]: q["target"] for q in result}
        assert caps[QuestType.TITAN] == 15        # 5 < 15 → overridden
        assert caps[QuestType.EVIL_GUARD] == 3     # always 3
        assert caps[QuestType.FORTRESS] == 1800    # always 1800
        assert caps[QuestType.PVP] == 500000000    # always 500M
        assert caps[QuestType.GATHER] == 1000000   # always 1M

    @patch("actions.quests.cv2")
    @patch("actions.quests.load_screenshot")
    def test_completed_detection(self, mock_load, mock_cv2, mock_device):
        mock_load.return_value = self._make_screen()
        mock_cv2.cvtColor.return_value = np.zeros((100, 100), dtype=np.uint8)
        mock_cv2.resize.return_value = np.zeros((200, 200), dtype=np.uint8)
        mock_cv2.imwrite = MagicMock()
        with patch("vision.ocr_read", return_value=["Evil Guard(3/3)"]):
            result = _ocr_quest_rows(mock_device)
        assert result[0]["completed"] is True
        assert result[0]["current"] == 3
        assert result[0]["target"] == 3

    @patch("actions.quests.cv2")
    @patch("actions.quests.load_screenshot")
    def test_no_patterns_returns_none(self, mock_load, mock_cv2, mock_device):
        mock_load.return_value = self._make_screen()
        mock_cv2.cvtColor.return_value = np.zeros((100, 100), dtype=np.uint8)
        mock_cv2.resize.return_value = np.zeros((200, 200), dtype=np.uint8)
        mock_cv2.imwrite = MagicMock()
        with patch("vision.ocr_read", return_value=["some random text no quests here"]):
            assert _ocr_quest_rows(mock_device) is None

    @patch("actions.quests.cv2")
    @patch("actions.quests.load_screenshot")
    def test_titan_target_15_not_overridden(self, mock_load, mock_cv2, mock_device):
        """Titan target of 15 or higher is trusted (not overridden)."""
        mock_load.return_value = self._make_screen()
        mock_cv2.cvtColor.return_value = np.zeros((100, 100), dtype=np.uint8)
        mock_cv2.resize.return_value = np.zeros((200, 200), dtype=np.uint8)
        mock_cv2.imwrite = MagicMock()
        with patch("vision.ocr_read", return_value=["Defeat Titans(3/20)"]):
            result = _ocr_quest_rows(mock_device)
        assert result[0]["target"] == 20  # 20 >= 15, not overridden


# ============================================================
# _claim_quest_rewards
# ============================================================

class TestClaimQuestRewards:
    def test_no_claim_button(self, mock_device):
        with patch("actions.quests.tap_image", return_value=False):
            assert _claim_quest_rewards(mock_device) == 0

    def test_single_reward(self, mock_device):
        tap_calls = [0]
        def tap_side_effect(name, device):
            tap_calls[0] += 1
            return tap_calls[0] <= 1  # True once, then False
        with patch("actions.quests.tap_image", side_effect=tap_side_effect), \
             patch("actions.quests.timed_wait"), \
             patch("actions.quests.check_screen", return_value=Screen.ALLIANCE_QUEST):
            assert _claim_quest_rewards(mock_device) == 1

    def test_multiple_rewards(self, mock_device):
        tap_calls = [0]
        def tap_side_effect(name, device):
            tap_calls[0] += 1
            return tap_calls[0] <= 3
        with patch("actions.quests.tap_image", side_effect=tap_side_effect), \
             patch("actions.quests.timed_wait"), \
             patch("actions.quests.check_screen", return_value=Screen.ALLIANCE_QUEST):
            assert _claim_quest_rewards(mock_device) == 3

    def test_stop_check_aborts(self, mock_device):
        with patch("actions.quests.tap_image", return_value=True), \
             patch("actions.quests.timed_wait"), \
             patch("actions.quests.check_screen", return_value=Screen.ALLIANCE_QUEST):
            result = _claim_quest_rewards(mock_device, stop_check=lambda: True)
            assert result == -1


# ============================================================
# _is_troop_in_building_relaxed
# ============================================================

class TestIsTroopDefendingRelaxed:
    def test_fresh_snapshot_defending(self, mock_device):
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.DEFENDING)]
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)
        # Make snapshot fresh (age < 120s)
        with patch("actions.quests.get_troop_status", return_value=snapshot):
            assert _is_troop_in_building_relaxed(mock_device) is True

    def test_fresh_snapshot_not_defending(self, mock_device):
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.HOME)]
        snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)
        with patch("actions.quests.get_troop_status", return_value=snapshot):
            assert _is_troop_in_building_relaxed(mock_device) is False

    def test_stale_snapshot_falls_to_panel(self, mock_device):
        """Snapshot older than 120s falls through to panel read."""
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        troops = [TroopStatus(action=TroopAction.DEFENDING)]
        old_snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)
        # Force age > 120
        old_snapshot.timestamp = time.time() - 200

        fresh_snapshot = DeviceTroopSnapshot(device=mock_device, troops=troops)
        with patch("actions.quests.get_troop_status", return_value=old_snapshot), \
             patch("actions.quests.read_panel_statuses", return_value=fresh_snapshot):
            assert _is_troop_in_building_relaxed(mock_device) is True

    def test_no_snapshot_no_panel(self, mock_device):
        with patch("actions.quests.get_troop_status", return_value=None), \
             patch("actions.quests.read_panel_statuses", return_value=None):
            assert _is_troop_in_building_relaxed(mock_device) is False


# ============================================================
# _wait_for_rallies
# ============================================================

class TestWaitForRallies:
    def test_panel_read_fails(self, mock_device):
        """Panel read failure returns immediately."""
        with patch("actions.quests.read_panel_statuses", return_value=None), \
             patch("actions.quests.config") as mock_config:
            mock_config.set_device_status = MagicMock()
            _wait_for_rallies(mock_device, stop_check=None)

    def test_no_rallying_troops_clears_pending(self, mock_device):
        """No rallying troops → false positive, clears pending counts."""
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        # Set up phantom pending
        _quest_rallies_pending[(mock_device, QuestType.TITAN)] = 2
        _quest_pending_since[(mock_device, QuestType.TITAN)] = time.time()

        snapshot = DeviceTroopSnapshot(
            device=mock_device,
            troops=[TroopStatus(action=TroopAction.HOME) for _ in range(5)],
        )
        with patch("actions.quests.read_panel_statuses", return_value=snapshot), \
             patch("actions.quests._interruptible_sleep", return_value=False), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.stats") as mock_stats:
            mock_config.set_device_status = MagicMock()
            mock_config.RALLY_WAIT_POLL_INTERVAL = 5
            _wait_for_rallies(mock_device, stop_check=None)

        # Pending should be cleared
        assert _quest_rallies_pending[(mock_device, QuestType.TITAN)] == 0

    def test_rallying_drops_returns(self, mock_device):
        """Rallying count drops → completion detected, returns."""
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot

        snap_2rally = DeviceTroopSnapshot(
            device=mock_device,
            troops=[TroopStatus(action=TroopAction.RALLYING),
                    TroopStatus(action=TroopAction.RALLYING),
                    TroopStatus(action=TroopAction.HOME)],
        )
        snap_1rally = DeviceTroopSnapshot(
            device=mock_device,
            troops=[TroopStatus(action=TroopAction.RALLYING),
                    TroopStatus(action=TroopAction.HOME),
                    TroopStatus(action=TroopAction.HOME)],
        )
        call_count = [0]
        def panel_side_effect(device):
            call_count[0] += 1
            return snap_2rally if call_count[0] <= 1 else snap_1rally

        with patch("actions.quests.read_panel_statuses", side_effect=panel_side_effect), \
             patch("actions.quests._interruptible_sleep", return_value=False), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.stats") as mock_stats, \
             patch("actions.quests.time.time", return_value=1000.0):
            mock_config.set_device_status = MagicMock()
            mock_config.RALLY_WAIT_POLL_INTERVAL = 5
            _wait_for_rallies(mock_device, stop_check=None)

        mock_stats.record_action.assert_called()

    def test_stop_check_aborts(self, mock_device):
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        snapshot = DeviceTroopSnapshot(
            device=mock_device,
            troops=[TroopStatus(action=TroopAction.RALLYING)],
        )
        with patch("actions.quests.read_panel_statuses", return_value=snapshot), \
             patch("actions.quests.config") as mock_config:
            mock_config.set_device_status = MagicMock()
            _wait_for_rallies(mock_device, stop_check=lambda: True)

    def test_timeout_returns(self, mock_device):
        """Rally wait exceeds PENDING_TIMEOUT_S → returns."""
        from troops import TroopStatus, TroopAction, DeviceTroopSnapshot
        snapshot = DeviceTroopSnapshot(
            device=mock_device,
            troops=[TroopStatus(action=TroopAction.RALLYING)],
        )
        # time.time() starts past timeout
        times = [1000.0, 1000.0 + 400, 1000.0 + 400, 1000.0 + 400]
        time_iter = iter(times)
        with patch("actions.quests.read_panel_statuses", return_value=snapshot), \
             patch("actions.quests._interruptible_sleep", return_value=False), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.quests.time.time", side_effect=lambda: next(time_iter)):
            mock_config.set_device_status = MagicMock()
            mock_config.RALLY_WAIT_POLL_INTERVAL = 5
            _wait_for_rallies(mock_device, stop_check=None)


# ============================================================
# _run_rally_loop
# ============================================================

class TestRunRallyLoop:
    def test_stop_check_returns_true(self, mock_device):
        actionable = [
            {"quest_type": QuestType.TITAN, "current": 0, "target": 15,
             "completed": False},
        ]
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.config") as mock_config:
            mock_config.get_device_config.return_value = False
            mock_config.MAX_RALLY_ATTEMPTS = 15
            mock_config.set_device_status = MagicMock()
            result = _run_rally_loop(mock_device, actionable, stop_check=lambda: True)
        assert result is True

    def test_all_covered_breaks(self, mock_device):
        """All rally quests covered by pending → exits loop."""
        _quest_rallies_pending[(mock_device, QuestType.TITAN)] = 15
        actionable = [
            {"quest_type": QuestType.TITAN, "current": 0, "target": 15,
             "completed": False},
        ]
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.heal_all"), \
             patch("actions.quests.config") as mock_config, \
             patch("actions.rallies.join_rally") as mock_join:
            mock_config.get_device_config.return_value = False
            mock_config.MAX_RALLY_ATTEMPTS = 15
            mock_config.set_device_status = MagicMock()
            result = _run_rally_loop(mock_device, actionable)
        assert result is False
        mock_join.assert_not_called()

    @patch("actions.quests._record_rally_started")
    def test_joins_rally_records(self, mock_record, mock_device):
        """Successful join → records rally started."""
        actionable = [
            {"quest_type": QuestType.TITAN, "current": 0, "target": 15,
             "completed": False},
        ]
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.heal_all"), \
             patch("actions.rallies.join_rally", return_value=QuestType.TITAN) as mock_join, \
             patch("actions.quests.config") as mock_config:
            mock_config.get_device_config.return_value = False
            mock_config.MAX_RALLY_ATTEMPTS = 1  # 1 iteration
            mock_config.set_device_status = MagicMock()
            _run_rally_loop(mock_device, actionable)
        mock_record.assert_called_with(mock_device, QuestType.TITAN)

    @patch("actions.quests._record_rally_started")
    def test_starts_own_titan(self, mock_record, mock_device):
        """No rally to join + titan_rally_own enabled → starts own titan rally."""
        actionable = [
            {"quest_type": QuestType.TITAN, "current": 0, "target": 15,
             "completed": False},
        ]
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.heal_all"), \
             patch("actions.rallies.join_rally", return_value=None), \
             patch("actions.titans.rally_titan", return_value=True) as mock_rt, \
             patch("actions.quests.config") as mock_config:
            def cfg_side(dev, key):
                if key == "auto_heal":
                    return False
                if key == "titan_rally_own":
                    return True
                return False
            mock_config.get_device_config.side_effect = cfg_side
            mock_config.MAX_RALLY_ATTEMPTS = 1
            mock_config.set_device_status = MagicMock()
            _run_rally_loop(mock_device, actionable)
        mock_rt.assert_called_once()
        mock_record.assert_called_with(mock_device, QuestType.TITAN)

    def test_no_rally_own_disabled_breaks(self, mock_device):
        """No rally to join + own rally disabled → breaks."""
        actionable = [
            {"quest_type": QuestType.TITAN, "current": 0, "target": 15,
             "completed": False},
        ]
        with patch("actions.quests.navigate", return_value=True), \
             patch("actions.quests.heal_all"), \
             patch("actions.rallies.join_rally", return_value=None), \
             patch("actions.quests.config") as mock_config:
            mock_config.get_device_config.return_value = False
            mock_config.MAX_RALLY_ATTEMPTS = 5
            mock_config.set_device_status = MagicMock()
            result = _run_rally_loop(mock_device, actionable)
        assert result is False


# ============================================================
# _check_quests_legacy
# ============================================================

class TestCheckQuestsLegacy:
    @patch("actions.quests.load_screenshot", return_value=None)
    def test_no_screenshot_returns(self, mock_load, mock_device):
        _check_quests_legacy(mock_device, None)
        # Should return without error

    @patch("actions.quests.cv2")
    @patch("actions.quests.load_screenshot")
    def test_no_active_quests(self, mock_load, mock_cv2, mock_device):
        """No quest templates above threshold → no actions taken."""
        mock_load.return_value = np.zeros((1920, 1080, 3), dtype=np.uint8)
        mock_cv2.matchTemplate.return_value = np.array([[0.3]])
        mock_cv2.minMaxLoc.return_value = (0, 0.3, None, None)
        mock_cv2.TM_CCOEFF_NORMED = 5
        with patch("actions.quests.get_template", return_value=np.zeros((20, 20, 3), dtype=np.uint8)), \
             patch("actions.rallies.join_rally") as mock_join:
            _check_quests_legacy(mock_device, None)
            mock_join.assert_not_called()

    def test_stop_check_respected(self, mock_device):
        """Stop check fires before processing quests."""
        screen = np.zeros((1920, 1080, 3), dtype=np.uint8)
        with patch("actions.quests.load_screenshot", return_value=screen), \
             patch("actions.quests.cv2") as mock_cv2, \
             patch("actions.quests.get_template", return_value=np.zeros((20, 20, 3), dtype=np.uint8)):
            # Make all templates score 0.9 (above threshold)
            mock_cv2.matchTemplate.return_value = np.array([[0.9]])
            mock_cv2.minMaxLoc.return_value = (0, 0.9, None, None)
            mock_cv2.TM_CCOEFF_NORMED = 5
            with patch("actions.rallies.join_rally") as mock_join:
                _check_quests_legacy(mock_device, stop_check=lambda: True)
                mock_join.assert_not_called()


# ============================================================
# _recall_tap_sequence
# ============================================================

class TestRecallTapSequence:
    def test_happy_path(self, mock_device):
        """Full sequence completes."""
        from botlog import get_logger
        log = get_logger("actions", mock_device)
        with patch("actions.quests.logged_tap"), \
             patch("actions.quests.wait_for_image_and_tap", return_value=True), \
             patch("actions.quests.tap_image"), \
             patch("actions.quests.save_failure_screenshot"), \
             patch("actions.quests.time.sleep"):
            result = _recall_tap_sequence(mock_device, log)
        assert result is True

    def test_detail_not_found(self, mock_device):
        """detail_button.png not found → returns False."""
        from botlog import get_logger
        log = get_logger("actions", mock_device)
        with patch("actions.quests.logged_tap"), \
             patch("actions.quests.wait_for_image_and_tap", return_value=False), \
             patch("actions.quests.save_failure_screenshot") as mock_save, \
             patch("actions.quests.time.sleep"):
            result = _recall_tap_sequence(mock_device, log)
        assert result is False
        mock_save.assert_called_once()

    def test_stop_check_aborts(self, mock_device):
        """Stop check after initial tap → returns False."""
        from botlog import get_logger
        log = get_logger("actions", mock_device)
        with patch("actions.quests.logged_tap"), \
             patch("actions.quests.time.sleep"):
            result = _recall_tap_sequence(mock_device, log, stop_check=lambda: True)
        assert result is False
