"""Tests for centralized action time costs and unified time advance."""

import tempfile
import unittest
from pathlib import Path

from action_time import move_time_cost
from entities import NPC, Player
from persistence import load_world, save_world
from world import WorldState


class ActionTimeTests(unittest.TestCase):
    def test_move_time_depends_on_distance(self):
        world = WorldState(player=Player(20.0, 20.0))
        short_start = world.time

        world.move(21.0, 20.0)
        short_cost = world.time - short_start

        world.move(40.0, 40.0)
        long_cost = world.time - (short_start + short_cost)

        self.assertGreater(long_cost, short_cost)
        self.assertAlmostEqual(short_cost, move_time_cost(1.0))

    def test_talk_advances_time(self):
        world = WorldState(
            player=Player(20.0, 20.0),
            npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0)],
        )
        from action import talk

        before = world.time
        talk(world, "npc_1", "hello")
        self.assertEqual(world.time - before, 600)

    def test_wait_still_advances_time(self):
        world = WorldState()
        world.wait(10)
        self.assertEqual(world.time, 10.0)

    def test_time_advance_updates_npc_schedule(self):
        world = WorldState(
            npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0)],
        )

        world.advance_time(9 * 3600)

        self.assertEqual(world.npc_position("npc_1"), (50.0, 50.0))

    def test_temporary_environment_change_expires(self):
        world = WorldState()
        world.environment_state["temporary_changes"].append(
            {
                "location": "stone_road",
                "change": {"moisture": 0.5, "duration": 3600},
                "created_at": world.time,
            }
        )

        world.advance_time(3601)

        self.assertEqual(world.environment_state["temporary_changes"], [])

    def test_time_survives_save_and_load(self):
        world = WorldState()
        world.advance_time(1234)

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(loaded.time, 1234.0)


if __name__ == "__main__":
    unittest.main()