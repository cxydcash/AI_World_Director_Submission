"""Tests for the unified Intent Layer."""

import json
import unittest
from unittest.mock import patch

from intent import Intent, IntentResolver
from open_world import OpenWorldEngine, create_open_world


class IntentResolverTests(unittest.TestCase):
    def setUp(self):
        self.world = create_open_world()
        self.world.object_state = {}
        self.ren = self.world.get_npc("npc_2")
        self.ren.position_override = [69.0, 70.0]
        self.world.player.x, self.world.player.y = 69.0, 70.0
        self.context = {
            "nearby_npcs": [
                {"id": "npc_2", "name": "Ren", "role": "accountant", "goals": list(self.ren.goals)}
            ],
            "nearby_objects": [
                {"id": "mayor_desk", "description": "Mayor's desk"},
                {"id": "ledger", "description": "The hidden ledger"},
                {"id": "footprints", "description": "Old footprints"},
            ],
        }

    def resolve(self, text, source, target_id=None):
        resolver = IntentResolver(api_key="")
        return resolver.resolve(
            text,
            source=source,
            actor_id="player",
            target_id=target_id,
            world=self.world,
            context=self.context,
        )

    def test_follow_dialogue(self):
        intent = self.resolve("Ren，你可以跟着我吗？", "dialogue", "npc_2")
        self.assertEqual(intent.intent_type, "follow")
        self.assertEqual(intent.target_id, "npc_2")

    def test_follow_or_move_phrase(self):
        intent = self.resolve("跟我去森林看看", "thought")
        self.assertIn(intent.intent_type, {"follow", "move"})
        self.assertIsInstance(intent.parameters, dict)

    def test_inspect_desk(self):
        intent = self.resolve("我想看看市长办公室的桌子", "thought")
        self.assertEqual(intent.intent_type, "inspect")
        self.assertEqual(intent.target_id, "mayor_desk")

    def test_take_object_from_context(self):
        intent = self.resolve("把桌上的东西拿起来", "thought")
        self.assertEqual(intent.intent_type, "take")
        self.assertIn(intent.target_id, {"mayor_desk", "ledger"})

    def test_investigate_ren_location(self):
        intent = self.resolve("我想调查 Ren 消失的地方", "thought")
        self.assertEqual(intent.intent_type, "investigate")
        self.assertEqual(intent.target_id, "footprints")

    def test_conversation_only(self):
        intent = self.resolve("今天天气不错。", "dialogue", "npc_2")
        self.assertEqual(intent.intent_type, "conversation_only")

    def test_dialogue_and_thought_are_compatible(self):
        dialogue = self.resolve("你可以跟着我吗？", "dialogue", "npc_2")
        thought = self.resolve("你可以跟着我吗？", "thought", "npc_2")
        self.assertEqual(dialogue.intent_type, thought.intent_type)
        self.assertEqual(dialogue.target_id, thought.target_id)

    def test_resolver_does_not_mutate_world(self):
        before = {
            "time": self.world.time,
            "player": [self.world.player.x, self.world.player.y],
            "ren_goals": list(self.ren.goals),
            "history": len(self.world.history),
        }
        resolver = IntentResolver(api_key="")
        resolver.resolve(
            "Ren，你可以跟着我吗？",
            source="dialogue",
            actor_id="player",
            target_id="npc_2",
            world=self.world,
            context=self.context,
        )
        after = {
            "time": self.world.time,
            "player": [self.world.player.x, self.world.player.y],
            "ren_goals": list(self.ren.goals),
            "history": len(self.world.history),
        }
        self.assertEqual(before, after)

    @patch.object(IntentResolver, "_request_llm_intent")
    def test_llm_is_primary_when_available(self, request_llm):
        request_llm.return_value = Intent(
            source="dialogue",
            actor_id="player",
            target_id="npc_2",
            intent_type="follow",
            parameters={},
            raw_text="你可以跟着我吗？",
            confidence=0.95,
            context={},
            reason="LLM result",
        )
        resolver = IntentResolver(api_key="test-key")
        intent = resolver.resolve(
            "你可以跟着我吗？",
            source="dialogue",
            target_id="npc_2",
            world=self.world,
            context=self.context,
        )
        self.assertEqual(resolver.last_source, "LLM")
        self.assertEqual(intent.intent_type, "follow")
        request_llm.assert_called_once()



    def test_dialogue_resolver_precedes_action_layer(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 50.0, 50.0
        engine.world.get_npc("npc_2").position_override = [50.0, 50.0]
        order = []
        resolved = Intent(
            source="dialogue", actor_id="player", target_id="npc_2",
            intent_type="follow", parameters={}, raw_text="你可以跟着我吗？",
            confidence=1.0, context={}, reason="test",
        )

        def fake_resolve(*args, **kwargs):
            order.append("resolver")
            return resolved

        def fake_execute(*args, **kwargs):
            order.append("action_executor")
            raise RuntimeError("stop after order check")

        with patch.object(engine.intent_resolver, "resolve", side_effect=fake_resolve),              patch.object(engine.action_executor, "execute", side_effect=fake_execute):
            try:
                engine.talk("npc_2", "你可以跟着我吗？")
            except RuntimeError:
                pass

        self.assertEqual(order, ["resolver", "action_executor"])

    def test_thought_resolver_precedes_legacy_adapter(self):
        engine = OpenWorldEngine()
        order = []
        resolved = Intent(
            source="thought", actor_id="player", target_id="mayor_desk",
            intent_type="inspect", parameters={}, raw_text="我想检查市长办公室的桌子",
            confidence=1.0, context={}, reason="test",
        )

        def fake_resolve(*args, **kwargs):
            order.append("resolver")
            return resolved

        def fake_adapter(*args, **kwargs):
            order.append("legacy_adapter")
            return [], [], []

        with patch.object(engine.intent_resolver, "resolve", side_effect=fake_resolve),              patch.object(engine.legacy_thought_adapter, "analyze", side_effect=fake_adapter):
            engine.analyze_thought("我想检查市长办公室的桌子")

        self.assertEqual(order, ["resolver", "legacy_adapter"])

    def test_follow_no_longer_calls_legacy_dialogue_keyword_detector(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        resolved = Intent(
            source="dialogue", actor_id="player", target_id="npc_2",
            intent_type="follow", parameters={}, raw_text="你可以跟着我吗？",
            confidence=1.0, context={}, reason="test",
        )
        with patch.object(engine.intent_resolver, "resolve", return_value=resolved),              patch.object(engine.dialogue_system, "detect_intent", side_effect=AssertionError("legacy detector called")):
            result = engine.talk("npc_2", "你可以跟着我吗？")
        self.assertTrue(result["following"])
        self.assertTrue(engine.world.characters_state["npc_2"]["following_player"])


if __name__ == "__main__":
    unittest.main()
