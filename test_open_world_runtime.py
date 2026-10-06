"""Step 6 tests: unified Open World runtime integration and demo closure."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import story_timeline as story_timeline_module
from action_layer import Action
from demo_flow import run_open_world_runtime_demo
from director import FakeDirectorProvider
from open_world import OpenWorldEngine
from persistence import load_world, save_world


def runtime_engine():
    engine = OpenWorldEngine()
    engine.tick_seconds = 3600.0
    engine.director.api_key = ""
    engine.director.provider = None
    return engine


def proposal(**overrides):
    base = {
        "event_id": "ai_runtime_event",
        "title": "Runtime AI event",
        "description": "A deterministic future event used by the runtime tests.",
        "trigger_at": 0.0,
        "conditions": [],
        "cancel_conditions": [],
        "effects": [{"type": "set_belief", "npc_id": "npc_2", "key": "ai_runtime", "value": 0.9}],
        "priority": 1.0,
        "metadata": {"generated_by": "ai_director"},
    }
    base.update(overrides)
    return base


class OpenWorldRuntimeTests(unittest.TestCase):
    def test_engine_uses_a_single_worldstate(self):
        engine = runtime_engine()
        self.assertIs(engine.world, engine._world)
        self.assertIs(engine.world.story_director, engine.director)
        snapshot = engine.snapshot()
        self.assertEqual(snapshot["player"]["position"], [engine.world.player.x, engine.world.player.y])
        engine.move("d")
        self.assertIs(engine.world, engine._world)
        self.assertEqual(engine.world.player.x, 55.0)

    def test_player_move_goes_through_action_layer(self):
        engine = runtime_engine()
        with patch.object(engine.action_validator, "validate", wraps=engine.action_validator.validate) as validate, \
             patch.object(engine.action_executor, "execute", wraps=engine.action_executor.execute) as execute:
            result = engine.move("d")
        self.assertTrue(result["success"])
        self.assertTrue(validate.called)
        self.assertTrue(execute.called)
        self.assertEqual(engine.world.player.x, 55.0)

    def test_player_talk_goes_through_intent_then_action(self):
        engine = runtime_engine()
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
        with patch.object(engine.intent_resolver, "resolve", wraps=engine.intent_resolver.resolve) as resolve, \
             patch.object(engine.action_validator, "validate", wraps=engine.action_validator.validate) as validate:
            result = engine.talk("npc_2", "你好，我在调查账本。")
        self.assertTrue(result["success"])
        self.assertTrue(resolve.called)
        self.assertTrue(validate.called)
        self.assertEqual(engine.last_intent.source, "dialogue")

    def test_npc_planner_goes_through_action_layer(self):
        engine = runtime_engine()
        mira = engine.world.get_npc("npc_3")
        mira.position_override = [68.0, 68.0]
        mira.goals = ["reach town"]
        with patch.object(engine.action_validator, "validate", wraps=engine.action_validator.validate) as validate, \
             patch.object(engine.action_executor, "execute", wraps=engine.action_executor.execute) as execute:
            engine._update_npcs_with_planner()
        self.assertTrue(validate.called)
        self.assertTrue(execute.called)
        self.assertNotEqual(engine.world.npc_position("npc_3"), (68.0, 68.0))

    def test_story_event_trigger_goes_through_event_executor(self):
        engine = runtime_engine()
        engine.world.story_timeline.add_event({
            "event_id": "runtime_story_event",
            "trigger_at": 0,
            "effects": [{"type": "set_belief", "npc_id": "npc_2", "key": "runtime_story", "value": 0.7}],
        })
        with patch.object(story_timeline_module, "EventExecutor", wraps=story_timeline_module.EventExecutor) as spy:
            engine.world.advance_time(10)
        self.assertTrue(spy.called)
        self.assertEqual(engine.world.get_npc("npc_2").beliefs["runtime_story"], 0.7)
        self.assertTrue(any(entry.get("event_id") == "runtime_story_event" for entry in engine.world.event_history))

    def test_director_event_enters_timeline(self):
        engine = runtime_engine()
        engine.director.provider = FakeDirectorProvider(proposal(trigger_at=engine.world.time + 7200))
        result = engine.run_director_cycle()
        self.assertTrue(result["added"])
        event = engine.world.story_timeline.get_event("ai_runtime_event")
        self.assertIsNotNone(event)
        self.assertEqual(event["source"], "ai_director")
        self.assertEqual(event["status"], "pending")

    def test_director_does_not_modify_worldstate_directly(self):
        engine = runtime_engine()
        npc = engine.world.get_npc("npc_2")
        before = (list(npc.goals), dict(npc.beliefs), engine.world.npc_position("npc_2"), engine.world.time)
        engine.director.provider = FakeDirectorProvider(
            proposal(trigger_at=engine.world.time + 7200, effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "ai_only"}])
        )
        result = engine.run_director_cycle()
        self.assertTrue(result["added"])
        after = (list(npc.goals), dict(npc.beliefs), engine.world.npc_position("npc_2"), engine.world.time)
        self.assertEqual(before, after)

    def test_ai_event_trigger_modifies_worldstate(self):
        engine = runtime_engine()
        engine.director.provider = FakeDirectorProvider(proposal(trigger_at=0))
        engine.run_director_cycle()
        engine.tick()
        self.assertEqual(engine.world.get_npc("npc_2").beliefs["ai_runtime"], 0.9)
        self.assertEqual(engine.world.story_timeline.get_event("ai_runtime_event")["status"], "triggered")

    def test_ai_event_creates_npc_goal(self):
        engine = runtime_engine()
        engine.director.provider = FakeDirectorProvider(
            proposal(trigger_at=0, effects=[{"type": "add_goal", "npc_id": "npc_3", "goal": "reach town"}])
        )
        engine.run_director_cycle()
        engine.tick()
        self.assertIn("reach town", engine.world.get_npc("npc_3").goals)

    def test_planner_takes_over_ai_goal(self):
        engine = runtime_engine()
        mira = engine.world.get_npc("npc_3")
        mira.position_override = [68.0, 68.0]
        mira.goals = []
        engine.director.provider = FakeDirectorProvider(
            proposal(trigger_at=0, effects=[{"type": "add_goal", "npc_id": "npc_3", "goal": "reach town"}])
        )
        engine.run_director_cycle()
        engine.tick()
        self.assertIn("reach town", mira.goals)
        self.assertNotEqual(engine.world.npc_position("npc_3"), (68.0, 68.0))

    def test_save_load_keeps_timeline(self):
        engine = runtime_engine()
        engine.director.provider = FakeDirectorProvider(proposal(trigger_at=engine.world.time + 99999))
        engine.run_director_cycle()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        self.assertIsNotNone(loaded.story_timeline.get_event("ai_runtime_event"))
        self.assertEqual(
            [entry["id"] for entry in loaded.story_timeline],
            [entry["id"] for entry in engine.world.story_timeline],
        )

    def test_save_load_keeps_npc_goals(self):
        engine = runtime_engine()
        engine.world.get_npc("npc_3").goals = ["reach forest", "keep watch"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        self.assertEqual(loaded.get_npc("npc_3").goals, ["reach forest", "keep watch"])

    def test_save_load_keeps_npc_current_action(self):
        engine = runtime_engine()
        npc = engine.world.get_npc("npc_2")
        npc.current_action = {"action_type": "move", "parameters": {"x": 40.0, "y": 40.0}, "complete": False}
        npc.action_progress = {"action": "move", "success": True, "goal_type": "reach_location"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        loaded_npc = loaded.get_npc("npc_2")
        self.assertEqual(loaded_npc.current_action, npc.current_action)
        self.assertEqual(loaded_npc.action_progress, npc.action_progress)

    def test_save_load_keeps_time(self):
        engine = runtime_engine()
        engine.world.advance_time(12345)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        self.assertEqual(loaded.time, 12345.0)

    def test_save_load_keeps_object_state(self):
        engine = runtime_engine()
        engine.object_state["drawer_open"] = True
        engine.object_state["ledger_revealed"] = True
        engine.world.object_state = engine.object_state
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        self.assertTrue(loaded.object_state["drawer_open"])
        self.assertTrue(loaded.object_state["ledger_revealed"])

    def test_save_load_ai_event_still_triggers(self):
        engine = runtime_engine()
        engine.director.provider = FakeDirectorProvider(proposal(trigger_at=engine.world.time + 7200))
        engine.run_director_cycle()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            save_world(engine.world, path)
            loaded = load_world(path)

        resumed = runtime_engine()
        resumed.director.provider = FakeDirectorProvider(proposal())
        resumed.adopt_world(loaded)
        resumed.tick_seconds = 7200.0
        resumed.tick()
        self.assertEqual(resumed.world.story_timeline.get_event("ai_runtime_event")["status"], "triggered")
        self.assertEqual(resumed.world.get_npc("npc_2").beliefs["ai_runtime"], 0.9)

    def test_fake_provider_full_chain(self):
        engine = runtime_engine()
        engine.world.player.x, engine.world.player.y = 22.0, 22.0
        taken = engine.execute_via_action_layer(Action("take", target_id="item_ledger"))
        self.assertTrue(taken["success"])
        self.assertIn("item_ledger", engine.world.player.inventory)

        mira = engine.world.get_npc("npc_3")
        mira.position_override = [68.0, 68.0]
        mira.goals = []
        engine.director.provider = FakeDirectorProvider(proposal(
            trigger_at=0,
            conditions=[{"type": "player_has_item", "item_id": "item_ledger"}],
            effects=[{"type": "add_goal", "npc_id": "npc_3", "goal": "reach town"}],
        ))
        proposed = engine.run_director_cycle()
        self.assertTrue(proposed["added"])

        engine.tick()
        self.assertEqual(engine.world.story_timeline.get_event("ai_runtime_event")["status"], "triggered")
        self.assertIn("reach town", mira.goals)
        self.assertNotEqual(engine.world.npc_position("npc_3"), (68.0, 68.0))

    def test_demo_flow_runs_offline_and_is_deterministic(self):
        with patch.dict(os.environ, {"LLM_API_KEY": ""}, clear=False):
            first_buffer = io.StringIO()
            with contextlib.redirect_stdout(first_buffer):
                first = run_open_world_runtime_demo()
            second_buffer = io.StringIO()
            with contextlib.redirect_stdout(second_buffer):
                second = run_open_world_runtime_demo()

        self.assertEqual(first, second)
        self.assertEqual(first["ai_event_status"], "triggered")
        self.assertIn("reach forest", first["mira_goals"])
        output = first_buffer.getvalue()
        self.assertIn("Stage 9", output)
        self.assertIn("ai_mira_followup", output)


if __name__ == "__main__":
    unittest.main()
