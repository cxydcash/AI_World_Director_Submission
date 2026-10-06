"""Tests for the reset command and backup behavior."""

import tempfile
import unittest
from pathlib import Path

import main
from persistence import load_world, save_world


class ResetTests(unittest.TestCase):
    def test_reset_restores_initial_state_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            dirty_world = main.create_demo_world()
            dirty_world.time = 12345.0
            dirty_world.player.money = 7.0
            dirty_world.player.energy = 4.0
            dirty_world.history.append(
                {"time": 12345.0, "actor": "player", "action": "test"}
            )
            dirty_world.story_seeds.append({"type": "rumor", "status": "unresolved"})
            dirty_world.quests[0].status = "completed"
            dirty_world.items.pop()
            dirty_world.npcs.pop()
            save_world(dirty_world, state_file)

            reset_world, backup_path = main.reset_game_state(state_file=state_file)
            loaded = load_world(state_file)
            backup_world = load_world(backup_path)

        self.assertIsNotNone(backup_path)
        self.assertEqual(backup_world.time, 12345.0)
        self.assertEqual(loaded.time, 0.0)
        self.assertEqual(loaded.player.money, 100.0)
        self.assertEqual(loaded.player.energy, 100.0)
        self.assertEqual(loaded.history, [])
        self.assertEqual(loaded.story_seeds, [])
        self.assertEqual(len(loaded.npcs), 3)
        self.assertEqual(len(loaded.items), 3)
        self.assertEqual(loaded.quests[0].status, "available")
        self.assertEqual(reset_world.time, 0.0)

    def test_reset_state_can_continue_gameplay(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(main.create_demo_world(), state_file)

            world, _ = main.reset_game_state(state_file=state_file)
            accepted, _ = world.accept_quest("quest_001")
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertTrue(accepted)
        self.assertEqual(loaded.get_quest("quest_001").status, "active")


if __name__ == "__main__":
    unittest.main()