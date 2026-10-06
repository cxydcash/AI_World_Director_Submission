"""Tests for dynamically created NPCs."""

import tempfile
import unittest
from pathlib import Path

from entities import Player
from event import EventValidationError, execute_event
from persistence import load_world, save_world
from world import WorldState


def empty_world():
    return WorldState(time=100.0, player=Player(10.0, 10.0))


def create_event():
    return {
        "event_id": "event_create_1",
        "time": 100.0,
        "effects": [
            {
                "type": "create_npc",
                "name": "Evan",
                "role": "travelling_clock_repairer",
                "personality": ["curious", "talkative"],
                "background": "Wanders between towns fixing old clocks.",
                "goal": "find_the_old_clock",
                "x": 10.0,
                "y": 10.0,
                "vision_range": 5.0,
            }
        ],
    }


class CreateNPCTests(unittest.TestCase):
    def test_create_npc_event_executes_and_npc_is_observable(self):
        world = empty_world()

        execute_event(world, create_event())
        npc = world.get_npc("NPC_001")

        self.assertIsNotNone(npc)
        self.assertEqual(npc.name, "Evan")
        self.assertEqual(npc.role, "travelling_clock_repairer")
        self.assertEqual(npc.personality, ["curious", "talkative"])
        self.assertEqual(npc.goals, ["find_the_old_clock"])

        observation = world.observe("NPC_001")
        self.assertIn(
            "player",
            {entry["type"] for entry in observation["observed"]},
        )

    def test_llm_cannot_specify_npc_id(self):
        world = empty_world()
        event = create_event()
        event["effects"][0]["id"] = "NPC_999"

        with self.assertRaises(EventValidationError):
            execute_event(world, event)
        self.assertEqual(world.npcs, [])

    def test_invalid_npc_data_is_rejected(self):
        world = empty_world()
        event = create_event()
        event["effects"][0]["personality"] = "curious"

        with self.assertRaises(EventValidationError):
            execute_event(world, event)

        world = empty_world()
        event = create_event()
        event["effects"][0]["x"] = 101.0

        with self.assertRaises(EventValidationError):
            execute_event(world, event)

    def test_auto_ids_are_unique(self):
        world = empty_world()

        execute_event(world, create_event())
        second = create_event()
        second["event_id"] = "event_create_2"
        execute_event(world, second)

        self.assertEqual(
            {npc.id for npc in world.npcs},
            {"NPC_001", "NPC_002"},
        )

    def test_created_npc_can_be_referenced_by_later_event(self):
        world = empty_world()
        execute_event(world, create_event())

        follow_up = {
            "event_id": "event_follow",
            "time": 101.0,
            "effects": [
                {
                    "type": "add_goal",
                    "npc_id": "NPC_001",
                    "goal": "visit_town",
                }
            ],
        }
        execute_event(world, follow_up)

        self.assertIn("visit_town", world.get_npc("NPC_001").goals)

    def test_created_npc_survives_save_and_load(self):
        world = empty_world()
        execute_event(world, create_event())
        world.observe("NPC_001")

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        npc = loaded.get_npc("NPC_001")
        self.assertIsNotNone(npc)
        self.assertEqual(npc.name, "Evan")
        self.assertEqual(npc.role, "travelling_clock_repairer")
        self.assertEqual(npc.personality, ["curious", "talkative"])
        self.assertEqual(npc.goals, ["find_the_old_clock"])
        self.assertEqual(npc.memory, world.npcs[0].memory)
        self.assertEqual(
            loaded.to_dict()["npcs"],
            world.to_dict()["npcs"],
        )


if __name__ == "__main__":
    unittest.main()