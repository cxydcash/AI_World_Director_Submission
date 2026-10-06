"""Tests for the minimal quest system."""

import tempfile
import unittest
from pathlib import Path

from entities import Player
from persistence import load_world, save_world
from quest import Quest
from world import WorldState


def quest_world():
    return WorldState(
        player=Player(50.0, 50.0),
        quests=[
            Quest(
                quest_id="quest_001",
                title="Find Ren",
                giver_id="npc_1",
                description="Ren went to the forest and never returned.",
                target="find Ren",
                reward=50,
            )
        ],
    )


class QuestTests(unittest.TestCase):
    def test_create_accept_complete_and_reward(self):
        world = quest_world()

        accepted, _ = world.accept_quest("quest_001")
        self.assertTrue(accepted)
        self.assertEqual(world.get_quest("quest_001").status, "active")

        completed, _ = world.complete_quest("quest_001")
        self.assertTrue(completed)
        self.assertEqual(world.get_quest("quest_001").status, "completed")
        self.assertEqual(world.player.money, 150.0)

    def test_quest_state_survives_save_and_load(self):
        world = quest_world()
        world.accept_quest("quest_001")

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(loaded.get_quest("quest_001").status, "active")
        self.assertEqual(
            loaded.get_quest("quest_001").to_dict(),
            world.get_quest("quest_001").to_dict(),
        )


if __name__ == "__main__":
    unittest.main()