"""Tests for check_quests helper functions extracted in Phase 2.

Tests _deduplicate_quests (pure function) and _get_actionable_quests.
"""
import time
from unittest.mock import patch, MagicMock, call

from config import QuestType
from actions.quests import (_deduplicate_quests, _get_actionable_quests,
                            _all_quests_visually_complete, _quest_rallies_pending,
                            check_quests, _quest_last_seen, _quest_target,
                            _attack_pvp_tower, _pvp_last_dispatch, _PVP_COOLDOWN_S,
                            _marker_errors)


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

    @patch("actions.quests._is_troop_defending_relaxed", return_value=True)
    def test_tower_ok_if_defending(self, mock_defending, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": False},
            {"quest_type": QuestType.TOWER, "current": 10, "target": 30, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is True

    @patch("actions.quests._is_troop_defending_relaxed", return_value=False)
    def test_tower_blocks_if_not_defending(self, mock_defending, mock_device):
        quests = [
            {"quest_type": QuestType.TITAN, "current": 15, "target": 15, "completed": False},
            {"quest_type": QuestType.TOWER, "current": 10, "target": 30, "completed": False},
        ]
        assert _all_quests_visually_complete(mock_device, quests) is False

    @patch("actions.quests._is_troop_defending_relaxed", return_value=True)
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
            if image == "reinforce_button.png":
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
