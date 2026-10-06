"""Step 11.5 tests: LLM Story Director reliability / schema alignment."""

import json
import unittest
from unittest.mock import patch

from director import FakeDirectorProvider, LLM_STORY_EVENT_PROMPT, LLM_SYSTEM_PROMPT
from effect_normalizer import (
    condition_schema_text,
    effect_schema_text,
    goal_rules_text,
    normalize_effect,
    normalize_effects,
    unsupported_goals,
)
from entities import Item, NPC, Player
from event import EventValidationError, EventValidator, SUPPORTED_EFFECT_TYPES
from npc_planner import NPCPlanner
from open_world import OpenWorldEngine
from story_director import propose_story_event
from world import WorldState


def norm(raw):
    return normalize_effect(json.loads(json.dumps(raw)))


class EffectNormalizerTests(unittest.TestCase):
    def test_effect_key_becomes_type(self):
        effect = norm({"effect": "add_memory", "npc_id": "npc_2", "content": "hi"})
        self.assertEqual(effect["type"], "add_memory")
        self.assertNotIn("effect", effect)

    def test_belief_alias_becomes_key(self):
        effect = norm({"type": "set_belief", "npc_id": "npc_2", "belief": "mayor_is_corrupt", "value": 0.5})
        self.assertEqual(effect["key"], "mayor_is_corrupt")
        self.assertNotIn("belief", effect)

    def test_npc_alias_becomes_npc_id(self):
        effect = norm({"type": "add_goal", "npc": "npc_2", "goal": "wait"})
        self.assertEqual(effect["npc_id"], "npc_2")
        self.assertNotIn("npc", effect)

    def test_memory_and_text_aliases_become_content(self):
        memory = norm({"type": "add_memory", "npc_id": "npc_2", "memory": "remembered"})
        dialogue = norm({"type": "dialogue", "speaker_id": "npc_2", "text": "spoken"})
        self.assertEqual(memory["content"], "remembered")
        self.assertEqual(dialogue["content"], "spoken")

    def test_nested_effect_is_unwrapped(self):
        effect = norm({"add_memory": {"npc_id": "npc_2", "content": "nested"}})
        self.assertEqual(effect["type"], "add_memory")
        self.assertEqual(effect["content"], "nested")

    def test_parameters_block_is_flattened(self):
        effect = norm({"type": "set_belief", "parameters": {"npc_id": "npc_2", "key": "k", "value": 0.4}})
        self.assertEqual(effect["npc_id"], "npc_2")
        self.assertEqual(effect["key"], "k")

    def test_redundant_reason_is_dropped(self):
        effect = norm({"type": "add_goal", "npc_id": "npc_2", "goal": "wait", "reason": "because"})
        self.assertNotIn("reason", effect)
        self.assertNotIn("reason", EventValidator().validate(
            WorldState(player=Player(), npcs=[NPC(id="npc_2", name="Ren", x=0.0, y=0.0)]),
            {"event_id": "e", "time": 0.0, "effects": [effect]},
        )["effects"][0])

    def test_missing_core_fields_are_never_invented(self):
        effect = norm({"type": "add_evidence", "description": "发现一些东西"})
        # Format repair must not guess x/y/evidence_id.
        self.assertNotIn("x", effect)
        self.assertNotIn("y", effect)
        world = WorldState(player=Player(), npcs=[NPC(id="npc_2", name="Ren", x=0.0, y=0.0)])
        with self.assertRaises(EventValidationError):
            EventValidator().validate(
                world, {"event_id": "e", "time": 0.0, "effects": [effect]}
            )

    def test_known_location_is_mapped_to_coordinates(self):
        effect = norm({
            "type": "add_evidence",
            "evidence_id": "ev_1",
            "evidence_type": "ledger_copy",
            "location": "home",
            "description": "d",
        })
        self.assertEqual((effect["x"], effect["y"]), (20.0, 20.0))
        self.assertNotIn("location", effect)

    def test_unknown_location_is_not_guessed(self):
        effect = norm({
            "type": "add_evidence",
            "evidence_id": "ev_1",
            "evidence_type": "ledger_copy",
            "location": "atlantis",
            "description": "d",
        })
        self.assertNotIn("x", effect)
        self.assertNotIn("y", effect)
        world = WorldState(player=Player(), npcs=[NPC(id="npc_2", name="Ren", x=0.0, y=0.0)])
        with self.assertRaises(EventValidationError):
            EventValidator().validate(
                world, {"event_id": "e", "time": 0.0, "effects": [effect]}
            )


class PromptSchemaTests(unittest.TestCase):
    def test_every_supported_effect_has_a_prompt_example(self):
        schema = effect_schema_text()
        for effect_type in sorted(SUPPORTED_EFFECT_TYPES):
            self.assertIn(f"\n{effect_type}\n", schema)
            self.assertIn(f'"type": "{effect_type}"', schema)

    def test_prompts_embed_the_strict_schema_and_goal_rules(self):
        for prompt in (LLM_SYSTEM_PROMPT, LLM_STORY_EVENT_PROMPT):
            self.assertIn("EFFECT SCHEMA (STRICT)", prompt)
            self.assertIn('Never use "effect" instead of "type"', prompt)
            self.assertIn("GOAL FORMAT", prompt)
            self.assertIn("add_evidence", prompt)

    def test_goal_rules_list_planner_supported_formats(self):
        rules = goal_rules_text()
        for fragment in ("reach_location:<location>", "find_item:<item_id>", "wait"):
            self.assertIn(fragment, rules)

    def test_condition_schema_forbids_inventing_event_ids(self):
        schema = condition_schema_text()
        for condition_type in ("character_state", "npc_at_location", "belief_true",
                               "event_completed", "event_cancelled", "time_reached"):
            self.assertIn(condition_type, schema)
        self.assertIn("MUST already exist in story_state.existing_event_ids", schema)
        self.assertIn("Never invent an event_id", schema)
        self.assertIn("CONDITION SCHEMA (STRICT)", LLM_STORY_EVENT_PROMPT)


class GoalAlignmentTests(unittest.TestCase):
    SUPPORTED = [
        "reach_location:forest",
        "find_item:item_ledger",
        "find_npc:npc_2",
        "talk_to:npc_2",
        "follow_npc:npc_2",
        "investigate:ev_ledger_copy",
        "help_npc:npc_2",
        "escape",
        "work",
        "wait",
    ]

    def test_supported_goal_formats_are_planner_readable(self):
        planner = NPCPlanner()
        for goal in self.SUPPORTED:
            self.assertTrue(planner.can_plan_goal(goal), f"not planned: {goal}")

    def test_legacy_alias_goals_still_plan(self):
        planner = NPCPlanner()
        for goal in ("find ledger", "recover ledger", "follow player", "reach town", "stop Ren"):
            self.assertTrue(planner.can_plan_goal(goal), f"not planned: {goal}")

    def test_natural_language_goal_is_rejected_by_the_ai_path(self):
        planner = NPCPlanner()
        self.assertFalse(planner.can_plan_goal("bring ledger copy to Ren"))
        rejected = unsupported_goals([
            {"type": "add_goal", "npc_id": "npc_2", "goal": "bring ledger copy to Ren"}
        ])
        self.assertEqual(rejected, ["bring ledger copy to Ren"])
        self.assertEqual(unsupported_goals([
            {"type": "add_goal", "npc_id": "npc_2", "goal": "help_npc:npc_2"}
        ]), [])

    def test_unsupported_goal_never_stays_active(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        ren.goals = ["bring ledger copy to Ren"]
        engine._update_npcs_with_planner()
        state = ren.goal_states["bring ledger copy to Ren"]
        self.assertEqual(state["status"], "failed")
        self.assertIn("unsupported goal", state["reason"])


class ProposalRejectionVisibilityTests(unittest.TestCase):
    def story_world(self):
        return WorldState(
            time=0.0,
            player=Player(50.0, 50.0, inventory=[]),
            npcs=[NPC(id="npc_2", name="Ren", x=40.0, y=40.0, position_override=[40.0, 40.0])],
            items=[Item(id="item_ledger", type="ledger", x=40.0, y=40.0)],
        )

    def valid_proposal(self, effects):
        return {
            "event_id": "ai_drifted_event",
            "title": "Drifted event",
            "description": "An event produced with drifted field names.",
            "trigger_at": 100.0,
            "conditions": [],
            "cancel_conditions": [],
            "effects": effects,
            "priority": 0.0,
        }

    def test_drifted_effects_are_normalized_then_added(self):
        world = self.story_world()
        proposal = self.valid_proposal([
            {"effect": "add_memory", "npc": "npc_2", "memory": "Mira told me about the ledger.", "reason": "flavor"},
            {"type": "set_belief", "npc_id": "npc_2", "belief": "mayor_suspicious", "value": 0.6},
        ])
        result = propose_story_event(world, FakeDirectorProvider(proposal))

        self.assertTrue(result["added"], result["reason"])
        self.assertTrue(result["normalization"])
        event = world.story_timeline.get_event("ai_drifted_event")
        self.assertEqual(event["effects"][0]["type"], "add_memory")
        self.assertEqual(event["effects"][0]["npc_id"], "npc_2")
        self.assertEqual(event["effects"][0]["content"], "Mira told me about the ledger.")
        self.assertEqual(event["effects"][1]["key"], "mayor_suspicious")

    def test_rejection_records_reason_and_original(self):
        world = self.story_world()
        proposal = self.valid_proposal([{"type": "add_evidence", "description": "no coordinates"}])
        result = propose_story_event(world, FakeDirectorProvider(proposal))

        self.assertFalse(result["success"])
        rejection = result["rejection"]
        self.assertEqual(rejection["source"], "ai_director")
        self.assertEqual(rejection["status"], "rejected")
        self.assertTrue(rejection["reason"])
        self.assertEqual(rejection["validation_error"], "EventValidationError")
        self.assertEqual(rejection["original"], proposal)
        self.assertIsNotNone(rejection["normalized"])


class LLMPathReliabilityTests(unittest.TestCase):
    def fake_body(self, content):
        return json.dumps({
            "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]
        }).encode("utf-8")

    def fake_response(self, body):
        class _Response:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response()

    def engine(self):
        engine = OpenWorldEngine()
        engine.director.api_key = "test-key"
        return engine

    def test_daily_llm_event_effects_are_normalized(self):
        engine = self.engine()
        content = {
            "event_id": "llm_drift",
            "effects": [{"effect": "add_memory", "npc": "npc_2", "memory": "hi", "reason": "x"}],
        }
        with patch("urllib.request.urlopen", return_value=self.fake_response(self.fake_body(content))):
            event = engine.director._request_llm_event(engine.world)

        self.assertIsNotNone(event)
        effect = event["effects"][0]
        self.assertEqual(effect["type"], "add_memory")
        self.assertEqual(effect["npc_id"], "npc_2")
        self.assertEqual(effect["content"], "hi")
        self.assertNotIn("reason", effect)
        self.assertTrue(engine.director.last_llm_normalization)

    def test_daily_llm_event_with_unsupported_goal_is_rejected(self):
        engine = self.engine()
        content = {
            "event_id": "llm_bad_goal",
            "effects": [{"type": "add_goal", "npc_id": "npc_2", "goal": "bring ledger copy to Ren"}],
        }
        with patch("urllib.request.urlopen", return_value=self.fake_response(self.fake_body(content))):
            event = engine.director._request_llm_event(engine.world)

        self.assertIsNone(event)
        rejection = engine.director.last_llm_rejection
        self.assertEqual(rejection["status"], "rejected")
        self.assertEqual(rejection["validation_error"], "UnsupportedGoal")
        self.assertIn("bring ledger copy to Ren", rejection["reason"])

    def test_daily_llm_event_rejects_effect_the_validator_would_reject(self):
        engine = self.engine()
        content = {
            "event_id": "llm_missing_fields",
            "effects": [{"type": "add_evidence", "description": "no coordinates"}],
        }
        with patch("urllib.request.urlopen", return_value=self.fake_response(self.fake_body(content))):
            event = engine.director._request_llm_event(engine.world)

        # Normalizer must not invent x/y, so the strict validator still rejects it.
        self.assertIsNotNone(event)
        with self.assertRaises(EventValidationError):
            EventValidator().validate(engine.world, event)

    def test_free_action_effects_are_normalized(self):
        engine = self.engine()
        content = {
            "success": True,
            "narrative": "你递出干粮。",
            "effects": [
                {"type": "change_relationship", "npc": "npc_2", "delta": 0.2, "reason": "trust"},
                {"type": "add_memory", "npc_id": "npc_2", "memory": "玩家信任我。"},
            ],
        }
        with patch("urllib.request.urlopen", return_value=self.fake_response(self.fake_body(content))):
            result = engine.director._request_llm_free_action(engine.world, {"description": "share food"})

        self.assertTrue(result["success"])
        self.assertEqual(result["effects"][0]["npc_id"], "npc_2")
        self.assertNotIn("reason", result["effects"][0])
        self.assertEqual(result["effects"][1]["content"], "玩家信任我。")

    def test_free_action_with_unsupported_goal_is_rejected_visibly(self):
        engine = self.engine()
        content = {
            "success": True,
            "narrative": "n",
            "effects": [{"type": "add_goal", "npc_id": "npc_2", "goal": "trust the player"}],
        }
        with patch("urllib.request.urlopen", return_value=self.fake_response(self.fake_body(content))):
            result = engine.director._request_llm_free_action(engine.world, {"description": "be friends"})

        self.assertFalse(result["success"])
        self.assertEqual(result["effects"], [])
        self.assertEqual(result["rejected"]["status"], "rejected")


class FallbackVisibilityTests(unittest.TestCase):
    def test_daily_fallback_is_marked_and_visible(self):
        engine = OpenWorldEngine()
        engine.director.api_key = "test-key"
        engine.director.provider = FakeDirectorProvider({
            "event_id": "ai_proposal_ok",
            "title": "ok",
            "description": "ok",
            "trigger_at": 100000.0,
            "conditions": [],
            "cancel_conditions": [],
            "effects": [{"type": "set_belief", "npc_id": "npc_2", "key": "k", "value": 0.5}],
        })
        # The daily LLM event is structurally invalid, so the engine must fall back
        # to the deterministic event instead of pretending the LLM succeeded.
        engine.director._request_llm_event = lambda world, context=None: {
            "event_id": "bad_daily",
            "time": world.time,
            "effects": [{"effect": "not_a_real_effect"}],
        }

        # Step 12.5: daily orchestration is now the Night Director entry point.
        engine._maybe_daily_director()

        report = engine.last_director_report
        self.assertEqual(report["event_source"], "deterministic_fallback")
        self.assertTrue(report["fallback_reason"])
        self.assertIsNotNone(report["llm_rejection"])
        self.assertEqual(report["llm_rejection"]["source"], "ai_director")
        self.assertTrue(any("deterministic fallback" in entry["text"] for entry in engine.director_feed))
        self.assertIsNotNone(report["event"])


if __name__ == "__main__":
    unittest.main()

