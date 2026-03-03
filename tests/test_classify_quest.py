"""Tests for _classify_quest_text (actions/quests.py)."""

from unittest.mock import patch

from actions.quests import _classify_quest_text
from config import QuestType


class TestClassifyQuestText:
    def test_titan(self):
        assert _classify_quest_text("Defeat Titans") == QuestType.TITAN
        assert _classify_quest_text("defeat titans") == QuestType.TITAN
        assert _classify_quest_text("TITAN rally") == QuestType.TITAN

    def test_eg(self):
        assert _classify_quest_text("Evil Guard") == QuestType.EVIL_GUARD
        assert _classify_quest_text("evil guard rally") == QuestType.EVIL_GUARD
        assert _classify_quest_text("Guard") == QuestType.EVIL_GUARD

    def test_pvp(self):
        assert _classify_quest_text("PvP Battle") == QuestType.PVP
        assert _classify_quest_text("Attack enemies") == QuestType.PVP
        assert _classify_quest_text("Defeat the Enemy") == QuestType.PVP

    def test_defeat_without_enemy_not_pvp(self):
        """'defeat' alone should NOT classify as PVP — too broad.
        'Defeat Frost Giants' or similar event quests would false-positive."""
        assert _classify_quest_text("Defeat the Enemv") == QuestType.PVP  # "enem" prefix matches
        assert _classify_quest_text("Defeat Something") is None

    def test_gather(self):
        assert _classify_quest_text("Gather Resources") == QuestType.GATHER

    def test_fortress(self):
        assert _classify_quest_text("Occupy Fortress") == QuestType.FORTRESS
        assert _classify_quest_text("fortress defense") == QuestType.FORTRESS

    def test_tower(self):
        assert _classify_quest_text("Tower defense") == QuestType.TOWER

    def test_unknown(self):
        assert _classify_quest_text("something else entirely") is None
        assert _classify_quest_text("") is None

    @patch("actions.quests.sys")
    def test_long_name_trimmed_to_tail_mac(self, mock_sys):
        """When Apple Vision drops '(' and regex captures multi-quest bleed,
        classification should use only the tail (last 30 chars)."""
        mock_sys.platform = "darwin"
        # Real example from logs: Titans text bleeds into Gather match
        long_name = (
            "Your faction s UnlocKitan foslueS auns a le/Goblin Lab "
            "17:29:10 10000 ALLIANCE Defeat Titans 14/15) 5000 "
            "0126.34 GO SIDE QUEST Gather"
        )
        # Should classify as GATHER (tail), not TITAN (earlier in string)
        assert _classify_quest_text(long_name) == QuestType.GATHER

    @patch("actions.quests.sys")
    def test_long_name_not_trimmed_on_pc(self, mock_sys):
        """On Windows the trim is disabled — long names classify normally."""
        mock_sys.platform = "win32"
        long_name = "x" * 40 + " Defeat Titans and Gather"
        # Without trim, "titan" is found first
        assert _classify_quest_text(long_name) == QuestType.TITAN

    def test_short_name_not_trimmed(self):
        """Normal short names (< 30 chars) are unaffected by trimming."""
        assert _classify_quest_text("Defeat Titans") == QuestType.TITAN
        assert _classify_quest_text("Gather") == QuestType.GATHER
        assert _classify_quest_text("Evil Guard") == QuestType.EVIL_GUARD
