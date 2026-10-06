"""Step 7.5 tests: Intent semantic disambiguation + follow safety guard."""

import json
import os
import unittest
from unittest.mock import patch

from action_layer import Action, ActionValidator
from intent import Intent, IntentResolver, is_accompany_request, is_explicit_follow_command
from open_world import OpenWorldEngine


def runtime_engine():
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.world.player.x, engine.world.player.y = 20.0, 20.0
    engine.world.get_npc("npc_1").position_override = [20.0, 20.0]
    return engine


def rule_intent(text, target_id="npc_1", world=None):
    resolver = IntentResolver(api_key="")
    return resolver.resolve(
        text, source="dialogue", actor_id="player", target_id=target_id, world=world
    )


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class IntentSemanticsTests(unittest.TestCase):
    def test_follow_command_wo_gen_zou(self):
        self.assertTrue(is_explicit_follow_command("跟我走"))
        self.assertEqual(rule_intent("跟我走").intent_type, "follow")

    def test_follow_command_gen_zhe_wo(self):
        self.assertTrue(is_explicit_follow_command("跟着我"))
        self.assertEqual(rule_intent("跟着我").intent_type, "follow")

    def test_accompany_question_is_not_follow(self):
        self.assertFalse(is_explicit_follow_command("我能跟你一起去吗？"))
        self.assertEqual(rule_intent("我能跟你一起去吗？").intent_type, "conversation_only")

    def test_accompany_offer_is_not_follow(self):
        self.assertFalse(is_explicit_follow_command("我可以陪你去看看吗？"))
        self.assertEqual(rule_intent("我可以陪你去看看吗？").intent_type, "conversation_only")

    def test_willingness_question_is_not_follow(self):
        self.assertFalse(is_explicit_follow_command("你愿意跟我去村长家吗？"))
        self.assertEqual(rule_intent("你愿意跟我去村长家吗？").intent_type, "conversation_only")

    def test_bare_join_question_is_not_follow(self):
        self.assertFalse(is_explicit_follow_command("那我能一起去看看吗？"))
        self.assertEqual(rule_intent("那我能一起去看看吗？").intent_type, "conversation_only")

    def test_join_request_with_context_is_not_follow(self):
        engine = runtime_engine()
        mayor = engine.world.get_npc("npc_1")
        mayor.conversation_memory = {"player": {
            "messages": [
                {"speaker": "npc", "text": "我要去村长家。", "world_time": 1.0, "intent_type": None, "topic": "ledger"},
            ],
            "known_facts": [], "unresolved_questions": [], "recent_topics": ["ledger"],
            "player_claims": [], "last_turn": 1,
        }}
        intent = rule_intent("那我能跟你一起去吗？", world=engine.world)
        self.assertNotEqual(intent.intent_type, "follow")

    def test_command_with_context_is_follow(self):
        engine = runtime_engine()
        intent = rule_intent("跟我去村长家。", world=engine.world)
        self.assertEqual(intent.intent_type, "follow")

    def test_join_request_does_not_set_following_player(self):
        engine = runtime_engine()
        engine.talk("npc_1", "我能跟你一起去吗？")
        self.assertFalse(engine.world.characters_state["npc_1"].get("following_player"))

    def test_join_request_does_not_add_follow_goal(self):
        engine = runtime_engine()
        engine.talk("npc_1", "我可以陪你去看看吗？")
        self.assertNotIn("follow player", engine.world.get_npc("npc_1").goals)

    def test_llm_follow_intent_is_downgraded_for_a_request(self):
        engine = runtime_engine()
        def forced(text, **kwargs):
            return Intent(source="dialogue", actor_id="player", target_id="npc_1",
                          intent_type="follow", raw_text=text, confidence=0.9)
        engine.intent_resolver.resolve = forced
        result = engine.talk("npc_1", "那我能跟你一起去看看吗？")
        self.assertNotEqual(result["intent"], "request_follow")
        self.assertEqual(result["resolved_intent"]["intent_type"], "conversation_only")
        self.assertFalse(engine.world.characters_state["npc_1"].get("following_player"))
        self.assertNotIn("follow player", engine.world.get_npc("npc_1").goals)

    def test_llm_follow_intent_executes_for_a_command(self):
        engine = runtime_engine()
        def forced(text, **kwargs):
            return Intent(source="dialogue", actor_id="player", target_id="npc_1",
                          intent_type="follow", raw_text=text, confidence=0.9)
        engine.intent_resolver.resolve = forced
        result = engine.talk("npc_1", "那你跟我走吧。")
        self.assertEqual(result["intent"], "request_follow")
        self.assertTrue(engine.world.characters_state["npc_1"]["following_player"])
        self.assertIn("follow player", engine.world.get_npc("npc_1").goals)

    def test_action_validator_follow_guard(self):
        engine = runtime_engine()
        validator = ActionValidator()
        request = validator.validate(
            engine.world, Action("follow", target_id="npc_1", raw_text="我能跟你一起去吗？")
        )
        command = validator.validate(
            engine.world, Action("follow", target_id="npc_1", raw_text="跟我走")
        )
        programmatic = validator.validate(engine.world, Action("follow", target_id="npc_1"))
        self.assertFalse(request.valid)
        self.assertTrue(command.valid)
        self.assertTrue(programmatic.valid)

    def test_conversation_history_enters_intent_context(self):
        engine = runtime_engine()
        mayor = engine.world.get_npc("npc_1")
        mayor.conversation_memory = {"player": {
            "messages": [
                {"speaker": "player", "text": "你准备去哪？", "world_time": 1.0, "intent_type": "conversation_only", "topic": None},
                {"speaker": "npc", "text": "我要去村长家查账本。", "world_time": 1.0, "intent_type": None, "topic": "ledger"},
            ],
            "known_facts": [], "unresolved_questions": [], "recent_topics": ["ledger"],
            "player_claims": [], "last_turn": 2,
        }}
        captured = {}
        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse({"choices": [{"message": {"content": json.dumps(
                {"intent_type": "conversation_only", "actor_id": "player", "target_id": "npc_1",
                 "parameters": {}, "confidence": 0.9, "reason": "asking to come along"})}}]})
        resolver = IntentResolver(api_key="test-key")
        with patch("intent.urllib.request.urlopen", fake_urlopen):
            resolver.resolve("那我能跟你一起去看看吗？", source="dialogue", actor_id="player",
                             target_id="npc_1", world=engine.world)
        context = json.loads(captured["body"]["messages"][1]["content"])["context"]
        self.assertIn("conversation_history", context)
        texts = [message["text"] for message in context["conversation_history"]]
        self.assertIn("我要去村长家查账本。", texts)
        system = captured["body"]["messages"][0]["content"]
        self.assertIn("禁止使用 follow", system)

    def test_intent_resolver_does_not_mutate_worldstate(self):
        engine = runtime_engine()
        mayor = engine.world.get_npc("npc_1")
        mayor.conversation_memory = {"player": {
            "messages": [{"speaker": "player", "text": "你好", "world_time": 1.0}],
            "known_facts": [], "unresolved_questions": [], "recent_topics": [],
            "player_claims": [], "last_turn": 1,
        }}
        before = json.dumps(mayor.conversation_memory, sort_keys=True)
        before_goals = list(mayor.goals)
        resolver = IntentResolver(api_key="")
        resolver.resolve("跟我走", source="dialogue", actor_id="player", target_id="npc_1", world=engine.world)
        self.assertEqual(json.dumps(mayor.conversation_memory, sort_keys=True), before)
        self.assertEqual(list(mayor.goals), before_goals)
        # An old save without conversation_memory must not be mutated either.
        other = engine.world.get_npc("npc_2")
        other.conversation_memory = {}
        resolver.resolve("你好", source="dialogue", actor_id="player", target_id="npc_2", world=engine.world)
        self.assertEqual(other.conversation_memory, {})

    def test_user_reported_message_is_not_follow(self):
        message = "那你是要去村长家吗，正好我是新来的，我能一起去看看吗？"
        self.assertFalse(is_explicit_follow_command(message))
        self.assertTrue(is_accompany_request(message))
        self.assertEqual(rule_intent(message).intent_type, "conversation_only")

    def test_web_runtime_two_turn_acceptance(self):
        engine = runtime_engine()
        mayor = engine.world.get_npc("npc_1")

        first = engine.talk("npc_1", "你准备去哪？")
        self.assertTrue(first["success"])
        self.assertNotEqual(first["intent"], "request_follow")

        second = engine.talk("npc_1", "那我能跟你一起去看看吗？")
        self.assertTrue(second["success"])
        self.assertNotEqual(second["intent"], "request_follow")
        self.assertNotEqual(second["resolved_intent"]["intent_type"], "follow")
        self.assertFalse(engine.world.characters_state["npc_1"].get("following_player"))
        self.assertNotIn("follow player", mayor.goals)

        third = engine.talk("npc_1", "那你跟我走吧。")
        self.assertEqual(third["intent"], "request_follow")
        self.assertTrue(engine.world.characters_state["npc_1"]["following_player"])
        self.assertIn("follow player", mayor.goals)


if __name__ == "__main__":
    unittest.main()
