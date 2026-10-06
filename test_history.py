"""Tests for player behavior history and persistence."""

import tempfile
import unittest
from pathlib import Path

from entities import Player
from persistence import load_world, save_world
from world import WorldState


class HistoryTests(unittest.TestCase):
    def test_player_actions_enter_history(self):
        world = WorldState(player=Player(20.0, 20.0))

        world.explore()
        world.work()

        actions = {entry["action"] for entry in world.history}
        self.assertIn("player_explored", actions)
        self.assertIn("player_worked", actions)

    def test_history_survives_save_and_load(self):
        world = WorldState(player=Player(20.0, 20.0))
        world.work()

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(loaded.history, world.history)


if __name__ == "__main__":
    unittest.main()