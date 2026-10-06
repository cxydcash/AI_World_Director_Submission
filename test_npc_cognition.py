"""Tests for NPC memory, beliefs, relationships, and goals."""

import tempfile
import unittest
from pathlib import Path

from entities import Item, NPC, Player
from persistence import load_world, save_world
from world import WorldState


def cognition_world(**npc_kwargs):
    return WorldState(
        time=20 * 3600,
        player=Player(20.0, 20.0),
        npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0, vision_range=5.0, **npc_kwargs)],
    )


class NPCCognitionTests(unittest.TestCase):
    def test_add_memory_saves_record(self):
        world = cognition_world()
        record = {"time": 10, "observer": "npc_1", "type": "player", "position": [1.0, 2.0]}

        world.add_memory("npc_1", record)

        self.assertEqual(world.npcs[0].memory, [record])

    def test_belief_is_independent_from_world_truth(self):
        world = WorldState(
            time=20 * 3600,
            player=Player(20.0, 20.0, inventory=["item_1"]),
            npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0, vision_range=5.0)],
            items=[Item(id="item_1", type="key", x=20.0, y=20.0, holder="player")],
        )

        world.observe("npc_1")

        self.assertEqual(world.npcs[0].beliefs["player_has_item_1"], 1.0)

        world.player.inventory = []
        world.observe("npc_1")

        self.assertEqual(world.npcs[0].beliefs["player_has_item_1"], 1.0)

    def test_relationship_is_clamped_between_zero_and_one(self):
        world = cognition_world(relationships={"player": 0.5})

        self.assertEqual(world.change_relationship("npc_1", "player", -0.2), 0.3)
        self.assertEqual(world.change_relationship("npc_1", "player", 1.0), 1.0)
        self.assertEqual(world.change_relationship("npc_1", "player", -5.0), 0.0)

    def test_goals_can_be_added_and_removed(self):
        world = cognition_world()

        self.assertTrue(world.add_goal("npc_1", "find_key"))
        self.assertFalse(world.add_goal("npc_1", "find_key"))
        self.assertIn("find_key", world.npcs[0].goals)

        self.assertTrue(world.remove_goal("npc_1", "find_key"))
        self.assertFalse(world.remove_goal("npc_1", "find_key"))

    def test_cognitive_state_survives_save_and_load(self):
        world = cognition_world(
            beliefs={"player_has_item_1": 0.8},
            relationships={"player": 0.2, "npc_2": 0.7},
            goals=["find_key", "talk_to_mira"],
        )
        world.add_memory(
            "npc_1",
            {"time": 20, "observer": "npc_1", "type": "player", "position": [20.0, 20.0]},
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(
            loaded.to_dict()["npcs"],
            world.to_dict()["npcs"],
        )
        self.assertEqual(loaded.npcs[0].beliefs, world.npcs[0].beliefs)
        self.assertEqual(loaded.npcs[0].relationships, world.npcs[0].relationships)
        self.assertEqual(loaded.npcs[0].goals, world.npcs[0].goals)
        self.assertEqual(loaded.npcs[0].memory, world.npcs[0].memory)

    def test_invalid_belief_value_is_rejected(self):
        world = cognition_world()

        with self.assertRaises(ValueError):
            world.set_belief("npc_1", "player_has_item_1", 1.5)


if __name__ == "__main__":
    unittest.main()