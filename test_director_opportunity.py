"""Tests for optional AI opportunity checks."""

import json
import unittest
from unittest.mock import patch

from director import LLMDirector
from entities import NPC, Player
from event import execute_event
from world import WorldState


def opportunity_world():
    return WorldState(
        time=1000.0,
        player=Player(20.0, 20.0),
        npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0, vision_range=5.0)],
    )


class DirectorOpportunityTests(unittest.TestCase):
    def test_should_trigger_false_returns_no_event(self):
        world = opportunity_world()
        director = LLMDirector(api_key="test-key")
        decision = json.dumps(
            {"should_trigger": False, "reason": "Nothing interesting yet.", "event": None}
        )

        with patch.object(director, "_should_check_opportunity", return_value=True):
            with patch.object(director, "_request_opportunity", return_value=decision):
                result = director.check_opportunity(world)

        self.assertFalse(result["should_trigger"])
        self.assertIsNone(result["event"])

    def test_should_trigger_true_returns_valid_executable_event(self):
        world = opportunity_world()
        director = LLMDirector(api_key="test-key")
        event = {
            "event_id": "event_opportunity",
            "time": 1000.0,
            "effects": [
                {
                    "type": "add_memory",
                    "npc_id": "npc_1",
                    "content": "I noticed the player nearby.",
                }
            ],
        }
        decision = json.dumps(
            {"should_trigger": True, "reason": "Player is nearby.", "event": event}
        )

        with patch.object(director, "_should_check_opportunity", return_value=True):
            with patch.object(director, "_request_opportunity", return_value=decision):
                result = director.check_opportunity(world)

        self.assertTrue(result["should_trigger"])
        execute_event(world, result["event"])
        self.assertEqual(len(world.event_history), 1)

    def test_api_failure_returns_false(self):
        world = opportunity_world()
        director = LLMDirector(api_key="test-key")

        with patch.object(director, "_should_check_opportunity", return_value=True):
            with patch.object(
                director,
                "_request_opportunity",
                side_effect=RuntimeError("LLM API request failed"),
            ):
                result = director.check_opportunity(world)

        self.assertFalse(result["should_trigger"])
        self.assertIsNone(result["event"])

    def test_cooldown_skips_llm_call(self):
        world = opportunity_world()
        director = LLMDirector(api_key="test-key")
        director._last_opportunity_time = world.time

        with patch.object(director, "_request_opportunity") as request:
            result = director.check_opportunity(world)

        request.assert_not_called()
        self.assertFalse(result["should_trigger"])


    def test_known_belief_alias_is_normalized(self):
        world = opportunity_world()
        director = LLMDirector(api_key="test-key")
        decision = json.dumps(
            {
                "should_trigger": True,
                "reason": "belief changed",
                "event": {
                    "event_id": "event_alias",
                    "time": 1000.0,
                    "effects": [
                        {
                            "type": "add_belief",
                            "npc_id": "npc_1",
                            "key": "player_saved_Ren",
                            "value": 1.0,
                        }
                    ],
                },
            }
        )

        with patch.object(director, "_should_check_opportunity", return_value=True):
            with patch.object(director, "_request_opportunity", return_value=decision):
                result = director.check_opportunity(world)

        self.assertTrue(result["should_trigger"])
        self.assertEqual(result["event"]["effects"][0]["type"], "set_belief")

if __name__ == "__main__":
    unittest.main()