"""Tests that story events produce one history entry only."""

import tempfile
import unittest
from pathlib import Path

from entities import NPC, Player
from persistence import load_world, save_world
from scenario import default_timeline
from world import WorldState


class StoryHistoryDuplicateTests(unittest.TestCase):
    def _world(self):
        return WorldState(
            player=Player(50.0, 50.0),
            npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0), NPC(id="npc_2", name="Ren", x=40.0, y=40.0)],
            story_timeline=default_timeline(),
            characters_state={
                "npc_1": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "village", "rescued": False},
                "npc_2": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "forest", "rescued": False},
            },
        )

    def test_continuous_advance_does_not_duplicate_history(self):
        world = self._world()

        world.wait(18 * 3600)
        first_ids = [entry["event_id"] for entry in world.story_event_history]
        world.wait(6 * 3600)
        second_ids = [entry["event_id"] for entry in world.story_event_history]

        self.assertEqual(first_ids, second_ids)
        self.assertEqual(len(second_ids), len(set(second_ids)))

    def test_history_state_survives_save_and_load(self):
        world = self._world()
        world.wait(18 * 3600)

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(loaded.story_event_history, world.story_event_history)
        loaded.wait(6 * 3600)
        self.assertEqual(loaded.story_event_history, world.story_event_history)


if __name__ == "__main__":
    unittest.main()