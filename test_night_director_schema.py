# -*- coding: utf-8 -*-
"""Web-Fix-1 regression: Night Director StoryEvent schema contract.

Guards against the Step 12.5 regression where LLM_NIGHT_PLAN_PROMPT omitted the
Event DSL effect schema, so DeepSeek returned description/effects-less summaries
that validate_story_event_proposal rejected.
"""

import unittest

import director
import night_director
from open_world import OpenWorldEngine


class NightPromptSchemaTests(unittest.TestCase):
    def test_night_prompt_embeds_the_event_dsl_schema(self):
        prompt = director.LLM_NIGHT_PLAN_PROMPT
        self.assertIn("EFFECT SCHEMA", prompt)
        self.assertIn("CONDITION SCHEMA", prompt)
        self.assertIn("create_npc", prompt)
        self.assertIn("add_evidence", prompt)
        self.assertIn('"description" is REQUIRED', prompt)
        self.assertIn('"effects" is REQUIRED', prompt)


class NightStoryEventSchemaTests(unittest.TestCase):
    class SchemaControlProvider:
        def plan_next_day(self, context):
            now = context["time"]["now"]
            return {
                "goal_changes": [],
                "story_updates": [],
                "new_mysteries": [],
                "reasoning_summary": "schema regression control",
                "story_events": [
                    {
                        "event_id": "ev_schema_npc", "title": "A traveller",
                        "description": "A traveller arrives in the village.",
                        "trigger_at": now, "conditions": [], "cancel_conditions": [],
                        "effects": [{
                            "type": "create_npc", "name": "Evan",
                            "role": "clock_repairer", "personality": ["curious"],
                            "goal": "reach_location:town", "x": 5.0, "y": 95.0,
                        }],
                    },
                    {
                        "event_id": "ev_schema_evidence", "title": "A trace",
                        "description": "A fresh trace is left behind.",
                        "trigger_at": now, "conditions": [], "cancel_conditions": [],
                        "effects": [{
                            "type": "add_evidence", "evidence_id": "ev_schema_trace",
                            "evidence_type": "trace", "x": 50.0, "y": 50.0,
                            "description": "a fresh physical trace",
                        }],
                    },
                ],
            }

    def test_create_npc_and_add_evidence_are_accepted_and_execute(self):
        engine = OpenWorldEngine()
        engine.director.api_key = ""
        engine.director.provider = self.SchemaControlProvider()
        world = engine.world
        world.time = 22 * 3600

        report = night_director.run_night_director(world, engine.director)
        applied = report["applied"]["story_events"]
        self.assertEqual(len(applied), 2)
        self.assertTrue(all(item["added"] for item in applied))
        self.assertIsNotNone(world.story_timeline.get_event("ev_schema_npc"))
        self.assertIsNotNone(world.story_timeline.get_event("ev_schema_evidence"))

        before_npcs, before_evidence = len(world.npcs), len(world.evidence)
        world.time += 1
        world.check_story_events()
        self.assertEqual(len(world.npcs) - before_npcs, 1)
        self.assertEqual(len(world.evidence) - before_evidence, 1)


if __name__ == "__main__":
    unittest.main()
