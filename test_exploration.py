"""Tests for exploration behavior and discovery."""

import unittest

from entities import NPC, Player
from quest import Quest
from world import WorldState


def forest_world():
    return WorldState(
        time=0.0,
        player=Player(40.0, 40.0),
        npcs=[
            NPC(
                id="npc_2",
                name="Ren",
                x=40.0,
                y=40.0,
                vision_range=5.0,
                position_override=[40.0, 40.0],
            )
        ],
        quests=[
            Quest(
                quest_id="quest_001",
                title="Find Ren",
                giver_id="npc_1",
                description="Ren went to the forest and never returned.",
                target="find Ren",
                reward=50,
                status="active",
            )
        ],
    )


class ExplorationTests(unittest.TestCase):
    def test_explore_advances_time_and_records_history(self):
        world = WorldState(player=Player(20.0, 20.0))

        success, _ = world.explore()

        self.assertTrue(success)
        self.assertEqual(world.time, 3600)
        self.assertTrue(
            any(entry["action"] == "player_explored" for entry in world.history)
        )

    def test_explore_fails_outside_explorable_location(self):
        world = WorldState(player=Player(50.0, 60.0))

        success, _ = world.explore()

        self.assertFalse(success)

    def test_explore_discovers_ren_and_completes_quest(self):
        world = forest_world()

        success, message = world.explore()

        self.assertTrue(success)
        self.assertIn("Ren", message)
        self.assertEqual(world.get_quest("quest_001").status, "completed")
        self.assertEqual(world.player.money, 150.0)
        self.assertTrue(
            any(entry["action"] == "player_met_npc" for entry in world.history)
        )


if __name__ == "__main__":
    unittest.main()