"""Tests for LLM and Mock event directors."""

import json
import os
import unittest
from unittest.mock import patch

from director import LLMDirector, MockDirector, load_environment
from entities import Item, NPC, Player
from event import execute_event
from world import WorldState


def director_world():
    return WorldState(
        time=100.0,
        player=Player(20.0, 20.0, inventory=["item_1"]),
        npcs=[
            NPC(
                id="npc_1",
                name="Mira",
                x=20.0,
                y=20.0,
                vision_range=5.0,
                relationships={"player": 0.5},
                goals=["find_key"],
            )
        ],
        items=[Item(id="item_1", type="key", x=20.0, y=20.0, holder="player")],
    )


def llm_api_key_is_set():
    load_environment()
    return bool(os.getenv("LLM_API_KEY"))


class DirectorTests(unittest.TestCase):
    def test_mock_director_runs_full_pipeline_offline(self):
        world = director_world()
        director = MockDirector()

        event = director.generate_event(world)
        execute_event(world, event)

        self.assertEqual(director.last_log["source"], "MockDirector")
        self.assertEqual(len(world.event_history), 1)
        self.assertIn("effects", event)

    def test_llm_legal_event_executes_successfully(self):
        world = director_world()
        raw_event = json.dumps(
            {
                "event_id": "event_llm_1",
                "time": 100.0,
                "effects": [
                    {
                        "type": "add_goal",
                        "npc_id": "npc_1",
                        "goal": "search_for_item",
                    }
                ],
            }
        )
        director = LLMDirector(api_key="test-key")

        with patch.object(director, "_request_event_text", return_value=raw_event):
            event = director.generate_event(world)

        self.assertFalse(director.used_fallback)
        self.assertEqual(director.last_log["source"], "LLM")
        execute_event(world, event)
        self.assertEqual(len(world.event_history), 1)

    def test_llm_invalid_json_falls_back_without_executing(self):
        world = director_world()
        director = LLMDirector(api_key="test-key")

        with patch.object(director, "_request_event_text", return_value="{bad json"):
            event = director.generate_event(world)

        self.assertTrue(director.used_fallback)
        self.assertEqual(director.last_log["source"], "RuleBased")
        self.assertEqual(world.event_history, [])

    def test_llm_unknown_effect_falls_back(self):
        world = director_world()
        raw_event = json.dumps(
            {
                "event_id": "event_bad",
                "time": 100.0,
                "effects": [{"type": "execute_python", "code": "x=1"}],
            }
        )
        director = LLMDirector(api_key="test-key")

        with patch.object(director, "_request_event_text", return_value=raw_event):
            event = director.generate_event(world)

        self.assertTrue(director.used_fallback)
        self.assertEqual(world.event_history, [])

    def test_llm_nonexistent_npc_falls_back(self):
        world = director_world()
        raw_event = json.dumps(
            {
                "event_id": "event_bad",
                "time": 100.0,
                "effects": [
                    {
                        "type": "add_goal",
                        "npc_id": "npc_missing",
                        "goal": "find_item",
                    }
                ],
            }
        )
        director = LLMDirector(api_key="test-key")

        with patch.object(director, "_request_event_text", return_value=raw_event):
            event = director.generate_event(world)

        self.assertTrue(director.used_fallback)

    def test_api_failure_falls_back_safely(self):
        world = director_world()
        director = LLMDirector(api_key="test-key")

        with patch.object(
            director,
            "_request_event_text",
            side_effect=RuntimeError("LLM API request failed"),
        ):
            event = director.generate_event(world)

        self.assertTrue(director.used_fallback)
        self.assertEqual(director.last_log["source"], "RuleBased")
        execute_event(world, event)
        self.assertEqual(len(world.event_history), 1)

    def test_llm_can_generate_create_npc(self):
        world = director_world()
        raw_event = json.dumps(
            {
                "event_id": "event_create_npc",
                "time": 100.0,
                "effects": [
                    {
                        "type": "create_npc",
                        "name": "Evan",
                        "role": "clock_repairer",
                        "personality": ["curious"],
                        "background": "Repairs clocks.",
                        "goal": "find_clock",
                        "x": 15.0,
                        "y": 15.0,
                    }
                ],
            }
        )
        director = LLMDirector(api_key="test-key")

        with patch.object(director, "_request_event_text", return_value=raw_event):
            event = director.generate_event(world)

        self.assertFalse(director.used_fallback)
        execute_event(world, event)
        created = [npc for npc in world.npcs if npc.id.startswith("NPC_")]
        self.assertTrue(created)
        self.assertEqual(created[0].name, "Evan")

    @unittest.skipUnless(llm_api_key_is_set(), "LLM_API_KEY is not set")
    def test_real_llm_api_generates_and_executes_event(self):
        world = director_world()
        director = LLMDirector()

        event = director.generate_event(world)

        self.assertFalse(
            director.used_fallback,
            "The configured LLM API request did not succeed.",
        )
        self.assertEqual(director.last_log["source"], "LLM")
        execute_event(world, event)
        self.assertEqual(len(world.event_history), 1)


if __name__ == "__main__":
    unittest.main()