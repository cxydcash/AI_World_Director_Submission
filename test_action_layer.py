"""Tests for the unified Action Layer."""

import unittest
from unittest.mock import patch

from action_layer import Action, ActionBuilder, ActionExecutor, ActionValidator
from intent import Intent
from open_world import OpenWorldEngine, create_open_world


class ActionLayerTests(unittest.TestCase):
    def setUp(self):
        self.world = create_open_world()
        self.world.object_state = {}
        self.world.player.x, self.world.player.y = 20.0, 20.0
        self.ren = self.world.get_npc("npc_2")
        self.ren.position_override = [21.0, 20.0]
        self.builder = ActionBuilder()
        self.validator = ActionValidator()
        self.executor = ActionExecutor(self.validator)

    def test_intent_to_action(self):
        intent = Intent(
            source="dialogue", actor_id="player", target_id="npc_2",
            intent_type="follow", parameters={"mode": "walk"}, raw_text="跟着我",
            confidence=0.9, context={}, reason="test",
        )
        action = self.builder.build(intent)
        self.assertIsInstance(action, Action)
        self.assertEqual(action.action_type, "follow")
        self.assertEqual(action.target_id, "npc_2")
        self.assertEqual(action.parameters, {"mode": "walk"})

    def test_building_and_validating_does_not_modify_world(self):
        before = (self.world.time, self.world.player.x, self.world.player.y, len(self.world.event_history))
        intent = Intent(
            source="thought", actor_id="player", target_id="mayor_desk",
            intent_type="inspect", parameters={}, raw_text="看看桌子",
            confidence=0.8, context={}, reason="test",
        )
        action = self.builder.build(intent)
        self.validator.validate(self.world, action)
        after = (self.world.time, self.world.player.x, self.world.player.y, len(self.world.event_history))
        self.assertEqual(before, after)

    def test_validator_rejects_invalid_move(self):
        action = Action("move", parameters={"x": 999, "y": 20})
        result = self.validator.validate(self.world, action)
        self.assertFalse(result.valid)
        self.assertIn("between 0 and 100", result.reason)

    def test_validator_accepts_valid_move(self):
        action = Action("move", parameters={"x": 40, "y": 40})
        result = self.validator.validate(self.world, action)
        self.assertTrue(result.valid)
        self.assertEqual(result.normalized_parameters, {"x": 40.0, "y": 40.0})

    def test_executor_modifies_world_after_validation(self):
        action = Action("move", parameters={"x": 25, "y": 20})
        result = self.executor.execute(self.world, action)
        self.assertTrue(result.success)
        self.assertEqual((self.world.player.x, self.world.player.y), (25.0, 20.0))

    def test_follow_only_changes_target_npc(self):
        action = Action("follow", target_id="npc_2")
        result = self.executor.execute(self.world, action)
        self.assertTrue(result.success)
        self.assertTrue(self.world.characters_state["npc_2"]["following_player"])
        self.assertFalse(self.world.characters_state["npc_1"].get("following_player", False))
        self.assertFalse(self.world.characters_state["npc_3"].get("following_player", False))

    def test_inspect_does_not_modify_world(self):
        before = (self.world.time, len(self.world.evidence), len(self.world.event_history))
        action = Action("inspect", target_id="mayor_desk")
        result = self.executor.execute(self.world, action)
        after = (self.world.time, len(self.world.evidence), len(self.world.event_history))
        self.assertTrue(result.success)
        self.assertEqual(before, after)

    def test_search_returns_discoverable_objects(self):
        action = Action("search", target_id="__location__")
        result = self.executor.execute(self.world, action)
        self.assertTrue(result.success)
        self.assertIn("objects", result.data)
        self.assertTrue(any(obj["id"] == "mayor_desk" for obj in result.data["objects"]))

    def test_take_requires_existing_nearby_item(self):
        self.world.player.x, self.world.player.y = 70.0, 70.0
        action = Action("take", target_id="item_ledger")
        invalid = self.validator.validate(self.world, action)
        self.assertFalse(invalid.valid)
        self.world.player.x, self.world.player.y = 22.0, 22.0
        valid = self.validator.validate(self.world, action)
        self.assertTrue(valid.valid)

    def test_conversation_only_does_not_execute_follow_or_move(self):
        class DialogueStub:
            def generate_reply(self, world, npc_id, message):
                return "今天天气不错。"

        class RuntimeStub:
            dialogue_system = DialogueStub()

        action = Action("conversation_only", target_id="npc_2", raw_text="今天天气不错。")
        before = (self.world.player.x, self.world.player.y, self.ren.position_override[:])
        result = self.executor.execute(self.world, action, runtime=RuntimeStub())
        after = (self.world.player.x, self.world.player.y, self.ren.position_override[:])
        self.assertTrue(result.success)
        self.assertEqual(result.data["reply"], "今天天气不错。")
        self.assertEqual(before, after)
        self.assertFalse(self.world.characters_state["npc_2"].get("following_player", False))

    def test_thought_builds_action_but_does_not_execute(self):
        engine = OpenWorldEngine()
        before = (engine.world.player.x, engine.world.player.y, len(engine.world.event_history))
        result = engine.analyze_thought("我想检查市长办公室的桌子")
        after = (engine.world.player.x, engine.world.player.y, len(engine.world.event_history))
        self.assertIn("action_candidate", result)
        self.assertEqual(result["action_candidate"]["action_type"], "inspect")
        self.assertEqual(before, after)

    def test_dialogue_follow_uses_action_layer_not_legacy_adapter(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine.world.get_npc("npc_2").position_override = [21.0, 20.0]
        with patch.object(engine.legacy_dialogue_adapter, "execute_talk", side_effect=AssertionError("legacy adapter called")):
            result = engine.talk("npc_2", "你可以跟着我吗？")
        self.assertTrue(result["success"])
        self.assertTrue(result["following"])
        self.assertTrue(engine.world.characters_state["npc_2"]["following_player"])



    def test_ask_and_investigate_have_executor_handlers(self):
        class DialogueStub:
            def generate_reply(self, world, npc_id, message):
                return "I am listening."

        class RuntimeStub:
            dialogue_system = DialogueStub()

        ask = Action("ask", target_id="npc_2", raw_text="我可以问你吗？")
        ask_result = self.executor.execute(self.world, ask, runtime=RuntimeStub())
        self.assertTrue(ask_result.success)
        investigate = Action("investigate", target_id="footprints")
        investigate_result = self.executor.execute(self.world, investigate)
        self.assertTrue(investigate_result.success)


if __name__ == "__main__":
    unittest.main()
