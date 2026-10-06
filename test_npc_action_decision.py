"""Step 8 phase 1 tests: AI NPC Action Decision (decision layer only)."""

import json
import os
import unittest
from unittest.mock import patch

from npc_action_decision import (
    FakeNPCGoalProvider,
    LLMNPCGoalProvider,
    NPCGoalProvider,
    apply_npc_goal,
    build_npc_decision_context,
    decide_npc_action,
    encode_goal_string,
    validate_npc_goal,
    NPCGoalDecisionError,
)
from open_world import OpenWorldEngine, create_open_world


def make_engine(decision=None, provider=None):
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    engine.npc_goal_provider = provider if provider is not None else FakeNPCGoalProvider(decision)
    return engine


class NPCGoalSchemaTests(unittest.TestCase):
    def test_valid_lead_goal_is_normalized(self):
        goal = validate_npc_goal({
            "action_required": True, "goal_type": "lead_player_to",
            "target": "mayor_house", "reason": "take them there", "confidence": 0.9,
        })
        self.assertEqual(goal["goal_type"], "lead_player_to")
        self.assertEqual(goal["target"], "mayor_house")

    def test_follow_defaults_to_player(self):
        goal = validate_npc_goal({"goal_type": "follow_player"})
        self.assertEqual(goal["target"], "player")

    def test_unknown_goal_type_is_rejected(self):
        with self.assertRaises(NPCGoalDecisionError):
            validate_npc_goal({"goal_type": "teleport_player"})

    def test_missing_target_is_rejected(self):
        with self.assertRaises(NPCGoalDecisionError):
            validate_npc_goal({"goal_type": "lead_player_to"})

    def test_low_confidence_is_no_action(self):
        self.assertIsNone(validate_npc_goal({"goal_type": "follow_player", "confidence": 0.1}))

    def test_no_action_flag_returns_none(self):
        self.assertIsNone(validate_npc_goal({"action_required": False, "reason": "chat"}))


class NPCActionDecisionTests(unittest.TestCase):
    def test_lead_player_to_is_produced_and_applied(self):
        engine = make_engine({
            "action_required": True, "goal_type": "lead_player_to", "target": "mayor_house",
            "reason": "玩家希望我带他去村长家", "confidence": 0.9,
        })
        result = engine.talk("npc_2", "你能带我去村长家看看吗？")
        decision = result["npc_goal_decision"]
        self.assertTrue(decision["action_required"])
        self.assertEqual(decision["goal"]["goal_type"], "lead_player_to")
        self.assertEqual(decision["goal"]["target"], "mayor_house")
        self.assertIn("lead_player_to:mayor_house", engine.world.get_npc("npc_2").goals)

    def test_follow_player_is_produced_and_applied(self):
        engine = make_engine({
            "action_required": True, "goal_type": "follow_player", "target": "player",
            "reason": "明确命令", "confidence": 0.9,
        })
        result = engine.talk("npc_2", "你跟我走。")
        decision = result["npc_goal_decision"]
        # The follow Action already added the goal, so the decision layer sees it
        # as already present and does not write a duplicate.
        self.assertIn("follow player", engine.world.get_npc("npc_2").goals)
        if decision["action_required"]:
            self.assertEqual(decision["goal"]["goal_type"], "follow_player")
            self.assertEqual(decision["applied_goal"], "follow player")
        else:
            self.assertTrue(decision.get("already_present"))

    def test_smalltalk_produces_no_goal(self):
        engine = make_engine({"action_required": False, "reason": "只是闲聊"})
        before = list(engine.world.get_npc("npc_2").goals)
        result = engine.talk("npc_2", "你最近怎么样？")
        decision = result["npc_goal_decision"]
        self.assertFalse(decision["action_required"])
        self.assertIsNone(decision["goal"])
        self.assertEqual(decision["reason"], "no_action")
        self.assertEqual(list(engine.world.get_npc("npc_2").goals), before)
        self.assertNotIn("wait", engine.world.get_npc("npc_2").goals)

    def test_join_dialogue_with_context_leads(self):
        def scripted(context):
            texts = [message["text"] for message in context.get("conversation_history", [])]
            if any("村长家" in text for text in texts):
                return {
                    "action_required": True, "goal_type": "lead_player_to",
                    "target": "mayor_house", "reason": "NPC already planned to go",
                    "confidence": 0.8,
                }
            return {"action_required": False, "reason": "not enough context"}

        engine = make_engine(provider=FakeNPCGoalProvider(scripted))
        engine.dialogue_system.memory.record_turn(
            engine.world.get_npc("npc_2"), "npc", "我要去村长家查账。", 0
        )
        result = engine.talk("npc_2", "我跟你一起去。")
        decision = result["npc_goal_decision"]
        self.assertTrue(decision["action_required"])
        self.assertEqual(decision["goal"]["target"], "mayor_house")
        self.assertIn("lead_player_to:mayor_house", engine.world.get_npc("npc_2").goals)

    def test_join_dialogue_without_context_does_not_guess(self):
        def scripted(context):
            return {"action_required": False, "reason": "not enough context"}

        engine = make_engine(provider=FakeNPCGoalProvider(scripted))
        before = list(engine.world.get_npc("npc_2").goals)
        result = engine.talk("npc_2", "我跟你一起去。")
        decision = result["npc_goal_decision"]
        self.assertFalse(decision["action_required"])
        self.assertIsNone(decision["goal"])
        self.assertEqual(list(engine.world.get_npc("npc_2").goals), before)

    def test_invalid_goal_type_is_rejected_without_mutation(self):
        engine = make_engine({"action_required": True, "goal_type": "teleport_player"})
        before = list(engine.world.get_npc("npc_2").goals)
        result = engine.talk("npc_2", "你带我瞬移过去。")
        decision = result["npc_goal_decision"]
        self.assertFalse(decision["action_required"])
        self.assertIn("unsupported goal_type", decision["error"])
        self.assertEqual(list(engine.world.get_npc("npc_2").goals), before)

    def test_provider_error_does_not_change_world(self):
        engine = make_engine(provider=FakeNPCGoalProvider(error=RuntimeError("boom")))
        before = list(engine.world.get_npc("npc_2").goals)
        result = engine.talk("npc_2", "你能带我去村长家吗？")
        decision = result["npc_goal_decision"]
        self.assertFalse(decision["action_required"])
        self.assertIn("provider_error", decision["reason"])
        self.assertEqual(list(engine.world.get_npc("npc_2").goals), before)

    def test_decision_layer_does_not_mutate_worldstate(self):
        world = create_open_world()
        npc = world.get_npc("npc_2")
        before = (list(npc.goals), list(npc.memory), world.time, len(world.event_history))
        provider = FakeNPCGoalProvider({
            "action_required": True, "goal_type": "lead_player_to", "target": "mayor_house",
        })
        decision = decide_npc_action(world, "npc_2", "你能带我去村长家吗？", provider)
        self.assertTrue(decision["action_required"])
        after = (list(npc.goals), list(npc.memory), world.time, len(world.event_history))
        self.assertEqual(before, after)

    def test_goal_is_committed_through_the_event_dsl(self):
        world = create_open_world()
        before = len(world.event_history)
        goal_string = apply_npc_goal(world, "npc_2", {
            "goal_type": "lead_player_to", "target": "mayor_house", "reason": "test", "confidence": 0.9,
        })
        self.assertEqual(goal_string, "lead_player_to:mayor_house")
        self.assertIn("lead_player_to:mayor_house", world.get_npc("npc_2").goals)
        self.assertEqual(len(world.event_history), before + 1)
        effects = world.event_history[-1]["effects"]
        self.assertEqual(effects[0]["type"], "add_goal")

    def test_context_carries_npc_player_dialogue_and_timeline(self):
        world = create_open_world()
        npc = world.get_npc("npc_2")
        npc.conversation_memory = {"player": {
            "messages": [{"speaker": "npc", "text": "我要去村长家查账。", "world_time": 0.0}],
            "known_facts": [], "unresolved_questions": [], "recent_topics": [],
            "player_claims": [], "last_turn": 1,
        }}
        context = build_npc_decision_context(world, "npc_2", "我跟你一起去。")
        for key in ("npc", "player", "world", "nearby_npcs", "nearby_objects", "conversation_history", "timeline"):
            self.assertIn(key, context)
        self.assertEqual(context["player"]["message"], "我跟你一起去。")
        texts = [message["text"] for message in context["conversation_history"]]
        self.assertIn("我要去村长家查账。", texts)
        self.assertEqual(context["npc"]["id"], "npc_2")
        self.assertIn("places", context["world"])

    def test_llm_provider_parses_json(self):
        provider = LLMNPCGoalProvider(api_key="test-key")
        payload = json.dumps({
            "action_required": True, "goal_type": "lead_player_to",
            "target": "mayor_house", "reason": "lead", "confidence": 0.9,
        })
        with patch.object(provider, "_request_decision", return_value=payload):
            world = create_open_world()
            decision = decide_npc_action(world, "npc_2", "你能带我去村长家吗？", provider)
        self.assertTrue(decision["action_required"])
        self.assertEqual(decision["goal"]["target"], "mayor_house")

    def test_llm_and_fake_providers_share_interface(self):
        fake = FakeNPCGoalProvider(None)
        llm = LLMNPCGoalProvider(api_key="test-key")
        self.assertIsInstance(fake, NPCGoalProvider)
        self.assertIsInstance(llm, NPCGoalProvider)
        self.assertTrue(callable(fake.decide_npc_goal))
        self.assertTrue(callable(llm.decide_npc_goal))


if __name__ == "__main__":
    unittest.main()
