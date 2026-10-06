"""Tests for LLM natural-language player action parsing."""

import json
import unittest
from unittest.mock import patch

from action import execute_parsed_action
from entities import Item, NPC, Player
from player_action_parser import PlayerActionParser, PlayerIntentError
from scenario import apply_timeline_change
from world import WorldState


def parser_world():
    return WorldState(
        player=Player(20.0, 20.0, inventory=["item_1"]),
        npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0)],
        items=[Item(id="item_1", type="medicine", x=20.0, y=20.0, holder="player")],
    )


class PlayerIntentTests(unittest.TestCase):
    def test_natural_language_creates_evidence(self):
        world = parser_world()
        action = {
            "type": "create_evidence",
            "evidence_type": "clothing_trace",
            "location": "forest",
            "description": "A coat left in the forest.",
            "target_belief": "Ren escaped this way",
        }
        parser = PlayerActionParser(api_key="test-key")

        with patch.object(parser, "_request_json", return_value=json.dumps(action)):
            parsed = parser.parse("我把外套丢在森林里", world)

        success, _ = execute_parsed_action(world, parsed, "我把外套丢在森林里")
        self.assertTrue(success)
        self.assertEqual(world.evidence[0]["type"], "clothing_trace")
        self.assertIn("parsed_action", world.actions[-1])

    def test_destroy_village_is_rejected(self):
        world = parser_world()
        parser = PlayerActionParser(api_key="test-key")

        with patch.object(
            parser,
            "_request_json",
            return_value=json.dumps({"type": "destroy_village"}),
        ):
            with self.assertRaises(PlayerIntentError):
                parser.parse("我毁灭整个村庄", world)

    def test_give_item_parsing_and_execution(self):
        world = parser_world()
        action = {"type": "give_item", "item_id": "item_1", "target": "npc_1"}
        parser = PlayerActionParser(api_key="test-key")

        with patch.object(parser, "_request_json", return_value=json.dumps(action)):
            parsed = parser.parse("我把药给Mira", world)

        execute_parsed_action(world, parsed, "我把药给Mira")
        self.assertNotIn("item_1", world.player.inventory)
        self.assertEqual(world.get_item("item_1").holder, "npc_1")
        self.assertTrue(
            any("player_gave_me_item_1" in memory.get("content", "") for memory in world.npcs[0].memory)
        )

    def test_rumor_can_be_reinterpreted_as_npc_belief(self):
        world = parser_world()
        action = {
            "type": "create_rumor",
            "target": "npc_1",
            "content": "Ren went north",
        }
        parser = PlayerActionParser(api_key="test-key")

        with patch.object(parser, "_request_json", return_value=json.dumps(action)):
            parsed = parser.parse("我告诉Mira Ren去了北方", world)

        execute_parsed_action(world, parsed, "我告诉Mira Ren去了北方")
        apply_timeline_change(
            world,
            {
                "type": "timeline_change",
                "changes": [],
                "npc_changes": [
                    {
                        "npc_id": "npc_1",
                        "belief": "Ren_location_north",
                        "value": 1.0,
                    }
                ],
            },
        )
        self.assertEqual(world.npcs[0].beliefs["Ren_location_north"], 1.0)


if __name__ == "__main__":
    unittest.main()