"""Tests for NPC schedules and schedule-driven observation."""

import tempfile
import unittest
from pathlib import Path

from entities import NPC, Player
from persistence import load_world, save_world
from world import WorldState


def single_npc_world(time, player=None):
    return WorldState(
        time=time,
        player=player or Player(50.0, 50.0),
        npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0, vision_range=5.0)],
    )


class NPCScheduleTests(unittest.TestCase):
    def test_npc_position_changes_with_world_time(self):
        morning = single_npc_world(9 * 3600)
        evening = single_npc_world(20 * 3600)

        self.assertEqual(morning.npc_position("npc_1"), (50.0, 50.0))
        self.assertEqual(evening.npc_position("npc_1"), (20.0, 20.0))

    def test_schedule_rolls_over_to_the_next_day(self):
        next_midnight = single_npc_world(86400)
        next_morning = single_npc_world(86400 + 9 * 3600)

        self.assertEqual(next_midnight.npc_location("npc_1"), "home")
        self.assertEqual(next_midnight.npc_position("npc_1"), (20.0, 20.0))
        self.assertEqual(next_morning.npc_position("npc_1"), (50.0, 50.0))

    def test_same_player_position_changes_observation_by_time(self):
        player = Player(20.0, 20.0)
        daytime = single_npc_world(9 * 3600, player=player)
        evening = single_npc_world(20 * 3600, player=player)

        daytime_observation = daytime.observe("npc_1")
        evening_observation = evening.observe("npc_1")

        self.assertEqual(daytime_observation["observed"], [])
        evening_types = {
            entry["type"] for entry in evening_observation["observed"]
        }
        self.assertIn("player", evening_types)

    def test_next_schedule_change_is_computed(self):
        world = single_npc_world(9 * 3600)

        next_time, next_location = world.next_npc_schedule_change("npc_1")

        self.assertEqual(next_location, "town")
        self.assertEqual(next_time, 12 * 3600)

    def test_schedule_and_memory_survive_save_and_load(self):
        world = single_npc_world(20 * 3600, player=Player(20.0, 20.0))
        world.observe("npc_1")

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(
            loaded.npcs[0].schedule,
            world.npcs[0].schedule,
        )
        self.assertEqual(
            loaded.npc_position("npc_1"),
            world.npc_position("npc_1"),
        )
        self.assertEqual(loaded.npcs[0].memory, world.npcs[0].memory)


if __name__ == "__main__":
    unittest.main()