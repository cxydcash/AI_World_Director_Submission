"""Tests for NPC local observation and private memory."""

import tempfile
import unittest
from pathlib import Path

from entities import Item, NPC, Player
from persistence import load_world, save_world
from world import WorldState


def observed_types(observation):
    return {entry["type"] for entry in observation["observed"]}


class NPCObservationTests(unittest.TestCase):
    def test_npc_observes_entities_inside_vision_range(self):
        world = WorldState(
            time=0,
            player=Player(22.0, 20.0),
            npcs=[
                NPC(id="npc_1", name="Mira", x=20.0, y=20.0, vision_range=5.0),
                NPC(id="npc_2", name="Ren", x=20.0, y=22.0, vision_range=5.0),
            ],
            items=[Item(id="item_1", type="food", x=20.0, y=24.0)],
        )

        observation = world.observe("npc_1")

        self.assertEqual(observation["time"], 0)
        self.assertEqual(observation["observer"], "npc_1")
        self.assertEqual(
            observed_types(observation),
            {"player", "item", "npc"},
        )
        self.assertEqual(len(world.npcs[0].memory), 1)

    def test_npc_does_not_observe_entities_outside_vision_range(self):
        world = WorldState(
            time=0,
            player=Player(50.0, 50.0),
            npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0, vision_range=5.0)],
        )

        observation = world.observe("npc_1")

        self.assertNotIn("player", observed_types(observation))
        self.assertEqual(observation["observed"], [])

    def test_old_observation_remains_after_player_leaves(self):
        npc = NPC(id="npc_1", name="Mira", x=20.0, y=20.0, vision_range=5.0)
        world = WorldState(
            time=0,
            player=Player(22.0, 20.0),
            npcs=[npc],
        )

        world.observe("npc_1")
        world.time = 9 * 3600
        world.move(80.0, 80.0)
        current = world.observe("npc_1")

        self.assertNotIn("player", observed_types(current))
        remembered_player = [
            entry
            for memory in npc.memory
            for entry in memory.get("observed", [])
            if entry["type"] == "player"
        ]
        self.assertEqual(len(remembered_player), 1)
        self.assertEqual(remembered_player[0]["position"], [22.0, 20.0])

    def test_npc_observation_cannot_see_far_world_info(self):
        world = WorldState(
            time=0,
            player=Player(50.0, 50.0),
            npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0, vision_range=5.0)],
            items=[Item(id="item_1", type="tool", x=90.0, y=90.0)],
        )

        observation = world.observe("npc_1")

        self.assertEqual(observation["observed"], [])
        self.assertNotIn("item_1", str(observation))

    def test_memory_and_vision_range_survive_save_and_load(self):
        world = WorldState(
            time=0,
            player=Player(22.0, 20.0),
            npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0, vision_range=5.0)],
        )
        world.observe("npc_1")

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(
            loaded.to_dict()["npcs"],
            world.to_dict()["npcs"],
        )
        self.assertEqual(loaded.npcs[0].vision_range, 5.0)
        self.assertEqual(loaded.npcs[0].memory, world.npcs[0].memory)


if __name__ == "__main__":
    unittest.main()