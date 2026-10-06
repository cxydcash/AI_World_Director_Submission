"""Tests for AI Director -> dynamic StoryEvent creation (Step 5)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from action_layer import ActionExecutor, ActionValidator
from director import DirectorProvider, FakeDirectorProvider, LLMDirectorProvider
from entities import Item, NPC, Player
from npc_planner import NPCPlanner
from open_world import OpenWorldDirector
from persistence import load_world, save_world
from scenario import default_timeline
from story_director import (
    MAX_PENDING_EVENTS,
    build_story_director_context,
    propose_story_event,
)
from world import WorldState


def story_world():
    world = WorldState(
        time=0.0,
        player=Player(50.0, 50.0, inventory=[]),
        npcs=[
            NPC(id="npc_1", name="Mira", x=20.0, y=20.0, position_override=[20.0, 20.0]),
            NPC(id="npc_2", name="Ren", x=40.0, y=40.0, position_override=[40.0, 40.0]),
        ],
        items=[Item(id="item_ledger", type="ledger", x=40.0, y=40.0)],
        story_timeline=default_timeline(),
        characters_state={
            "npc_1": {"alive": True, "location": "home", "rescued": False},
            "npc_2": {"alive": True, "location": "forest", "rescued": False},
        },
    )
    world.story_timeline.clear()
    return world


def proposal(**overrides):
    base = {
        "event_id": "ai_event_1",
        "title": "Mira searches the mine",
        "description": "If Mira reaches the forest, she finds a clue about the ledger.",
        "trigger_at": 100.0,
        "conditions": [{"type": "npc_at_location", "npc_id": "npc_1", "location": "forest"}],
        "cancel_conditions": [],
        "effects": [{"type": "add_goal", "npc_id": "npc_1", "goal": "search the mine"}],
        "priority": 1.0,
        "metadata": {"generated_by": "ai_director"},
    }
    base.update(overrides)
    return base


class StoryDirectorTests(unittest.TestCase):
    def test_legal_ai_json_becomes_story_event(self):
        world = story_world()
        result = propose_story_event(world, FakeDirectorProvider(proposal()))

        self.assertTrue(result["success"])
        self.assertTrue(result["added"])
        event = result["event"]
        self.assertEqual(event["event_id"], "ai_event_1")
        self.assertEqual(event["status"], "pending")
        self.assertEqual(event["source"], "ai_director")
        self.assertEqual(event["effects"][0]["type"], "add_goal")
        self.assertIsNotNone(world.story_timeline.get_event("ai_event_1"))

    def test_invalid_json_rejected(self):
        world = story_world()
        result = propose_story_event(world, FakeDirectorProvider("{bad json"))

        self.assertFalse(result["success"])
        self.assertIn("rejected", result["reason"])
        self.assertEqual(len(world.story_timeline), 0)

    def test_missing_event_id_rejected(self):
        world = story_world()
        broken = proposal()
        del broken["event_id"]
        result = propose_story_event(world, FakeDirectorProvider(broken))

        self.assertFalse(result["success"])
        self.assertIn("event_id", result["reason"])
        self.assertEqual(len(world.story_timeline), 0)

    def test_duplicate_event_id_rejected(self):
        world = story_world()
        world.story_timeline.add_event({
            "event_id": "ai_event_1",
            "trigger_at": 500,
            "effects": [{"type": "add_goal", "npc_id": "npc_1", "goal": "existing"}],
        })
        result = propose_story_event(world, FakeDirectorProvider(proposal()))

        self.assertFalse(result["success"])
        self.assertIn("duplicate event_id", result["reason"])
        self.assertEqual(world.story_timeline.get_event("ai_event_1")["effects"][0]["goal"], "existing")

    def test_invalid_condition_rejected(self):
        world = story_world()
        broken = proposal(conditions=[{"type": "special_plot_condition", "npc_id": "npc_1"}])
        result = propose_story_event(world, FakeDirectorProvider(broken))

        self.assertFalse(result["success"])
        self.assertIn("unsupported condition type", result["reason"])
        self.assertEqual(len(world.story_timeline), 0)

    def test_invalid_effect_rejected(self):
        world = story_world()
        broken = proposal(effects=[{"type": "ren_reveal_secret", "npc_id": "npc_1"}])
        result = propose_story_event(world, FakeDirectorProvider(broken))

        self.assertFalse(result["success"])
        self.assertIn("unsupported effect type", result["reason"])
        self.assertEqual(len(world.story_timeline), 0)

    def test_unknown_npc_reference_in_effect_rejected(self):
        world = story_world()
        broken = proposal(effects=[{"type": "add_goal", "npc_id": "npc_ghost", "goal": "x"}])
        result = propose_story_event(world, FakeDirectorProvider(broken))

        self.assertFalse(result["success"])
        self.assertEqual(len(world.story_timeline), 0)

    def test_director_does_not_directly_modify_worldstate(self):
        world = story_world()
        npc = world.get_npc("npc_1")
        before = (list(npc.goals), dict(npc.beliefs), world.npc_position("npc_1"))

        result = propose_story_event(world, FakeDirectorProvider(proposal()))

        self.assertTrue(result["added"])
        after = (list(npc.goals), dict(npc.beliefs), world.npc_position("npc_1"))
        self.assertEqual(before, after)
        self.assertFalse(world.event_history)

    def test_proposal_is_added_to_timeline(self):
        world = story_world()
        propose_story_event(world, FakeDirectorProvider(proposal()))
        event = world.story_timeline.get_event("ai_event_1")
        self.assertEqual(event["trigger_at"], 100.0)
        self.assertEqual(event["metadata"]["generated_by"], "ai_director")
        self.assertIn("ai_event_1", [item["event_id"] for item in world.story_timeline.list_pending()])

    def test_ai_event_time_trigger(self):
        world = story_world()
        propose_story_event(
            world,
            FakeDirectorProvider(proposal(
                conditions=[],
                effects=[{"type": "set_belief", "npc_id": "npc_1", "key": "time_fired", "value": 1.0}],
            )),
        )
        world.advance_time(100)
        self.assertEqual(world.story_timeline.get_event("ai_event_1")["status"], "triggered")
        self.assertEqual(world.get_npc("npc_1").beliefs["time_fired"], 1.0)

    def test_ai_event_condition_trigger(self):
        world = story_world()
        propose_story_event(
            world,
            FakeDirectorProvider(proposal(
                trigger_at=0,
                conditions=[{"type": "player_has_item", "item_id": "item_ledger"}],
                effects=[{"type": "set_belief", "npc_id": "npc_2", "key": "clue", "value": 1.0}],
            )),
        )
        world.advance_time(1)
        self.assertEqual(world.story_timeline.get_event("ai_event_1")["status"], "pending")

        world.player.inventory.append("item_ledger")
        world.advance_time(1)
        self.assertEqual(world.story_timeline.get_event("ai_event_1")["status"], "triggered")
        self.assertEqual(world.get_npc("npc_2").beliefs["clue"], 1.0)

    def test_ai_event_effects_go_through_event_executor(self):
        world = story_world()
        propose_story_event(
            world,
            FakeDirectorProvider(proposal(
                conditions=[],
                effects=[
                    {"type": "set_belief", "npc_id": "npc_2", "key": "ai_clue", "value": 0.9},
                    {"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"},
                ],
            )),
        )
        world.advance_time(100)
        self.assertEqual(world.get_npc("npc_2").beliefs["ai_clue"], 0.9)
        self.assertTrue(any(entry.get("event_id") == "ai_event_1" for entry in world.event_history))

    def test_ai_event_creates_npc_goal(self):
        world = story_world()
        propose_story_event(
            world,
            FakeDirectorProvider(proposal(
                conditions=[],
                effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "reach town"}],
            )),
        )
        world.advance_time(100)
        self.assertIn("reach town", world.get_npc("npc_2").goals)

    def test_npc_planner_takes_over_generated_goal(self):
        world = story_world()
        propose_story_event(
            world,
            FakeDirectorProvider(proposal(
                conditions=[],
                effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "reach town"}],
            )),
        )
        world.advance_time(100)
        before = world.npc_position("npc_2")

        action = NPCPlanner().plan_next_action(world, "npc_2")
        self.assertEqual(action.action_type, "move")
        validator = ActionValidator()
        executor = ActionExecutor(validator)
        validation = validator.validate(world, action)
        self.assertTrue(validation.valid)
        executor.execute(world, action, validation=validation)
        self.assertNotEqual(world.npc_position("npc_2"), before)

    def test_ai_event_survives_save_and_load(self):
        world = story_world()
        propose_story_event(
            world,
            FakeDirectorProvider(proposal(
                trigger_at=500,
                conditions=[],
                effects=[{"type": "set_belief", "npc_id": "npc_1", "key": "persisted", "value": 1.0}],
            )),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "director_state.json"
            save_world(world, path)
            loaded = load_world(path)

        self.assertIsNotNone(loaded.story_timeline.get_event("ai_event_1"))
        loaded.advance_time(500)
        self.assertEqual(loaded.story_timeline.get_event("ai_event_1")["status"], "triggered")
        self.assertEqual(loaded.get_npc("npc_1").beliefs["persisted"], 1.0)

    def test_fake_provider_full_chain(self):
        world = story_world()
        world.player.x, world.player.y = 40.0, 40.0
        picked, _ = world.pick("item_ledger")
        self.assertTrue(picked)

        provider = FakeDirectorProvider(proposal(
            event_id="ai_followup",
            trigger_at=0,
            conditions=[{"type": "player_has_item", "item_id": "item_ledger"}],
            effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "reach town"}],
        ))
        result = propose_story_event(world, provider)
        self.assertTrue(result["added"])
        self.assertEqual(provider.calls, 1)

        world.advance_time(1)
        self.assertEqual(world.story_timeline.get_event("ai_followup")["status"], "triggered")

        action = NPCPlanner().plan_next_action(world, "npc_2")
        self.assertEqual(action.action_type, "move")

    def test_max_events_per_call(self):
        world = story_world()
        payload = {"events": [proposal(), proposal(event_id="ai_event_2")]}
        result = propose_story_event(world, FakeDirectorProvider(payload))

        self.assertFalse(result["success"])
        self.assertIn("at most", result["reason"])
        self.assertEqual(len(world.story_timeline), 0)

    def test_pending_event_limit(self):
        world = story_world()
        for index in range(MAX_PENDING_EVENTS):
            world.story_timeline.add_event({
                "event_id": f"pending_{index}",
                "trigger_at": 10 ** 9,
                "effects": [{"type": "add_goal", "npc_id": "npc_1", "goal": f"goal_{index}"}],
            })
        provider = FakeDirectorProvider(proposal())
        result = propose_story_event(world, provider)

        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "pending_event_limit_reached")
        self.assertEqual(provider.calls, 0)
        self.assertIsNone(world.story_timeline.get_event("ai_event_1"))

    def test_duplicate_conditions_effects_rejected(self):
        world = story_world()
        first = propose_story_event(world, FakeDirectorProvider(proposal()))
        self.assertTrue(first["added"])

        reworded = proposal(
            event_id="ai_event_other",
            title="A different title",
            description="A different description",
            trigger_at=999.0,
        )
        second = propose_story_event(world, FakeDirectorProvider(reworded))

        self.assertFalse(second["success"])
        self.assertIn("duplicate story event", second["reason"])
        self.assertIsNone(world.story_timeline.get_event("ai_event_other"))

    def test_no_api_key_still_works(self):
        with patch.dict(os.environ, {"LLM_API_KEY": ""}, clear=False):
            director = OpenWorldDirector()
            self.assertIsNone(director.provider)
            blocked = director.propose_story_event(story_world())
            self.assertFalse(blocked["success"])
            self.assertEqual(blocked["reason"], "no_provider")

        world = story_world()
        result = propose_story_event(world, FakeDirectorProvider(proposal()))
        self.assertTrue(result["success"])

    def test_llm_and_fake_provider_share_interface(self):
        fake = FakeDirectorProvider(proposal())
        llm = LLMDirectorProvider(api_key="test-key")
        self.assertIsInstance(fake, DirectorProvider)
        self.assertIsInstance(llm, DirectorProvider)
        self.assertTrue(callable(fake.generate_story_event))
        self.assertTrue(callable(llm.generate_story_event))

        with patch.object(llm, "_request_story_event", return_value=json.dumps(proposal())):
            world = story_world()
            result = propose_story_event(world, llm)
        self.assertTrue(result["success"])
        self.assertEqual(result["event"]["event_id"], "ai_event_1")

    def test_context_contains_story_state(self):
        world = story_world()
        world.story_timeline.add_event({"event_id": "pending_x", "trigger_at": 10, "effects": []})
        context = build_story_director_context(world)

        self.assertIn("time", context)
        self.assertIn("player", context)
        self.assertIn("npcs", context)
        self.assertIn("items", context)
        self.assertIn("story_state", context)
        self.assertEqual(context["story_state"]["pending_count"], 1)
        self.assertIn("pending_x", context["story_state"]["existing_event_ids"])

    def test_daily_tick_generates_future_event(self):
        from open_world import OpenWorldEngine

        engine = OpenWorldEngine()
        engine.director.provider = FakeDirectorProvider(
            proposal(
                event_id="daily_ai_event",
                trigger_at=engine.world.time + 100000,
                conditions=[],
                effects=[{"type": "set_belief", "npc_id": "npc_1", "key": "daily_ai", "value": 1.0}],
            )
        )
        # Step 12.5: the daily orchestration entry point is now the Night
        # Director; exercise the legacy compatibility method directly.
        engine._maybe_daily_director()

        self.assertIsNotNone(engine.world.story_timeline.get_event("daily_ai_event"))
        report = engine.last_director_report
        self.assertIsNotNone(report)
        self.assertTrue(report["story_proposal"]["added"])


    def test_director_cycle_entry_point(self):
        from open_world import OpenWorldEngine

        engine = OpenWorldEngine()
        engine.director.provider = FakeDirectorProvider(
            proposal(trigger_at=engine.world.time + 1000, conditions=[], effects=[
                {"type": "set_belief", "npc_id": "npc_1", "key": "cycle", "value": 1.0},
            ])
        )
        result = engine.run_director_cycle()

        self.assertTrue(result["added"])
        self.assertIsNotNone(engine.world.story_timeline.get_event("ai_event_1"))
        self.assertEqual(engine.last_story_proposal["event"]["event_id"], "ai_event_1")


if __name__ == "__main__":
    unittest.main()
