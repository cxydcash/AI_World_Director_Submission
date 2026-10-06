"""Tests for the constrained Event DSL."""

import tempfile
import unittest
from pathlib import Path

from entities import Item, NPC, Player
from event import EventValidationError, execute_event
from persistence import load_world, save_world
from world import WorldState


def event_world():
    return WorldState(
        time=100.0,
        player=Player(20.0, 20.0),
        npcs=[
            NPC(
                id="npc_1",
                name="Mira",
                x=20.0,
                y=20.0,
                vision_range=5.0,
                relationships={"player": 0.5},
            )
        ],
        items=[Item(id="item_1", type="key", x=30.0, y=30.0)],
    )


class EventDSLTests(unittest.TestCase):
    def test_legal_event_executes_all_cognitive_effects(self):
        world = event_world()
        event = {
            "event_id": "event_001",
            "time": 100.0,
            "effects": [
                {
                    "type": "add_memory",
                    "npc_id": "npc_1",
                    "content": "The player took the key.",
                },
                {
                    "type": "set_belief",
                    "npc_id": "npc_1",
                    "key": "player_has_item_1",
                    "value": 1.0,
                },
                {
                    "type": "change_relationship",
                    "npc_id": "npc_1",
                    "target_id": "player",
                    "delta": -0.2,
                },
                {
                    "type": "add_goal",
                    "npc_id": "npc_1",
                    "goal": "find_item_1",
                },
            ],
        }

        execute_event(world, event)
        npc = world.get_npc("npc_1")

        self.assertEqual(npc.memory[-1]["content"], "The player took the key.")
        self.assertEqual(npc.beliefs["player_has_item_1"], 1.0)
        self.assertEqual(npc.relationships["player"], 0.3)
        self.assertIn("find_item_1", npc.goals)
        self.assertEqual(len(world.event_history), 1)

    def test_unknown_effect_is_rejected(self):
        world = event_world()
        event = {
            "event_id": "event_bad",
            "time": 100.0,
            "effects": [{"type": "run_python", "code": "print(1)"}],
        }

        with self.assertRaises(EventValidationError):
            execute_event(world, event)
        self.assertEqual(world.event_history, [])

    def test_unknown_event_field_is_rejected(self):
        world = event_world()
        event = {
            "event_id": "event_bad",
            "time": 100.0,
            "unexpected": True,
            "effects": [
                {
                    "type": "add_goal",
                    "npc_id": "npc_1",
                    "goal": "find_key",
                }
            ],
        }

        with self.assertRaises(EventValidationError):
            execute_event(world, event)

    def test_nonexistent_npc_is_rejected(self):
        world = event_world()
        event = {
            "event_id": "event_bad",
            "time": 100.0,
            "effects": [
                {
                    "type": "add_goal",
                    "npc_id": "npc_missing",
                    "goal": "find_key",
                }
            ],
        }

        with self.assertRaises(EventValidationError):
            execute_event(world, event)

    def test_out_of_bounds_coordinate_is_rejected(self):
        world = event_world()
        event = {
            "event_id": "event_bad",
            "time": 100.0,
            "effects": [
                {
                    "type": "move_npc",
                    "npc_id": "npc_1",
                    "x": 101.0,
                    "y": 10.0,
                }
            ],
        }

        with self.assertRaises(EventValidationError):
            execute_event(world, event)

    def test_invalid_relationship_value_is_rejected(self):
        world = event_world()
        belief_event = {
            "event_id": "event_bad",
            "time": 100.0,
            "effects": [
                {
                    "type": "set_belief",
                    "npc_id": "npc_1",
                    "key": "player_has_item_1",
                    "value": 1.5,
                }
            ],
        }
        relationship_event = {
            "event_id": "event_bad",
            "time": 100.0,
            "effects": [
                {
                    "type": "change_relationship",
                    "npc_id": "npc_1",
                    "target_id": "player",
                    "delta": "lots",
                }
            ],
        }

        with self.assertRaises(EventValidationError):
            execute_event(world, belief_event)
        with self.assertRaises(EventValidationError):
            execute_event(world, relationship_event)

    def test_spawn_item_and_move_npc(self):
        world = event_world()
        event = {
            "event_id": "event_002",
            "time": 100.0,
            "effects": [
                {
                    "type": "spawn_item",
                    "item_id": "item_2",
                    "item_type": "tool",
                    "x": 12.5,
                    "y": 33.25,
                },
                {
                    "type": "move_npc",
                    "npc_id": "npc_1",
                    "x": 10.0,
                    "y": 90.0,
                },
            ],
        }

        execute_event(world, event)

        spawned = world.get_item("item_2")
        self.assertIsNotNone(spawned)
        self.assertEqual((spawned.x, spawned.y), (12.5, 33.25))
        self.assertEqual(world.npc_position("npc_1"), (10.0, 90.0))

    def test_dialogue_effect_is_recorded(self):
        world = event_world()
        event = {
            "event_id": "event_003",
            "time": 100.0,
            "effects": [
                {
                    "type": "dialogue",
                    "speaker_id": "npc_1",
                    "content": "Did you take the key?",
                }
            ],
        }

        execute_event(world, event)

        self.assertEqual(len(world.dialogues), 1)
        self.assertEqual(world.dialogues[0]["content"], "Did you take the key?")

    def test_event_history_survives_save_and_load(self):
        world = event_world()
        event = {
            "event_id": "event_004",
            "time": 100.0,
            "effects": [
                {
                    "type": "add_goal",
                    "npc_id": "npc_1",
                    "goal": "find_item_1",
                }
            ],
        }
        execute_event(world, event)

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(loaded.event_history, world.event_history)
        self.assertIn("find_item_1", loaded.npcs[0].goals)


if __name__ == "__main__":
    unittest.main()