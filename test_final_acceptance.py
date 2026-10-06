# -*- coding: utf-8 -*-
"""Step 12.8 — final playability acceptance.

Scenarios A-E exercise the real OpenWorldEngine / WorldState entry points and
assert the causal loop, not isolated helpers. No network / LLM is required.
"""

import unittest
from unittest.mock import patch

import night_director
from director_input import GOAL_CHANGE_ADD, GoalChange, split_goal_string
from goal_manager import goal_manager_for
from npc_action_decision import FakeNPCGoalProvider
from open_world import OpenWorldDirector, OpenWorldEngine
from plot_deviation import detect_deviations
from story_timeline import StoryTimeline


def make_engine(provider=None):
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = provider
    engine.npc_goal_provider = None
    engine.tick_seconds = 3600.0
    engine._last_director_day = 0
    return engine


def add_goal(world, npc_id, goal_string, urgency="normal", source="ai_director"):
    goal_type, target = split_goal_string(goal_string)
    return goal_manager_for(world).apply(world, GoalChange(
        change_type=GOAL_CHANGE_ADD, npc_id=npc_id, goal_type=goal_type,
        target=target, goal_string=goal_string, urgency=urgency,
        source=source, reason="acceptance"))


class FakeNightProvider:
    def __init__(self, report):
        self.report = report
        self.calls = 0
        self.context = None

    def plan_next_day(self, context):
        self.calls += 1
        self.context = context
        return dict(self.report)


class ScenarioA_NormalExploration(unittest.TestCase):
    def test_normal_exploration_loop(self):
        engine = make_engine()
        world = engine.world
        ren = world.get_npc("npc_2")

        # 1. move — time advances a little, never hours
        before = world.time
        moved = engine.move("w")
        self.assertTrue(moved["success"])
        self.assertGreater(world.time - before, 0.0)
        self.assertLess(world.time - before, 3600.0)

        # 2. observe
        view = engine.snapshot()
        self.assertIn("observation", view)
        self.assertIn("npcs", view)

        # 3. talk to Ren
        world.player.x, world.player.y = 70.0, 70.0
        ren.position_override = [70.0, 70.0]
        talk = engine.talk("npc_2", "你好，Ren。")
        self.assertTrue(talk["success"])
        self.assertTrue(any(
            entry.get("kind") == "dialogue" or entry.get("type") == "dialogue"
            for entry in world.player.memory
        ))

        # 4. investigate a clue (player inspects the mayor's bookshelf)
        world.player.x, world.player.y = 20.0, 20.0
        before_evidence = len(world.evidence)
        inspect = engine.inspect_object("mayor_bookshelf")
        self.assertTrue(inspect["success"])
        self.assertGreater(len(world.evidence), before_evidence)

        # NPCs keep planning and never teleport
        ren_before = world.npc_position("npc_2")
        engine.tick()
        moved_distance = world.distance_between(ren_before, world.npc_position("npc_2"))
        self.assertLessEqual(moved_distance, 2.01)

        # no illegal (unparseable) goals were produced
        for npc in world.npcs:
            for goal in npc.goals:
                parsed = engine.npc_planner.parse_goal(goal)
                self.assertNotEqual(parsed.goal_type, "unknown", f"{npc.id}: {goal}")


class ScenarioB_PlayerChangesNpcPlan(unittest.TestCase):
    def test_dialogue_goal_is_decided_applied_and_executed(self):
        engine = make_engine()
        world = engine.world
        world.player.x, world.player.y = 69.0, 70.0
        ren = world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        engine.npc_goal_provider = FakeNPCGoalProvider({
            "action_required": True, "goal_type": "lead_player_to",
            "target": "mayor_house", "reason": "player asked", "confidence": 0.9,
        })

        director_inputs = []
        real_decide = OpenWorldDirector.decide_goals

        def spy_decide(self, world, director_input):
            director_inputs.append(director_input)
            return real_decide(self, world, director_input)

        with patch.object(OpenWorldDirector, "decide_goals", spy_decide):
            result = engine.talk("npc_2", "你能带我去村长家看看吗？")

        decision = result["npc_goal_decision"]
        self.assertTrue(decision["action_required"])
        self.assertTrue(director_inputs)
        self.assertEqual(director_inputs[0].source, "player_dialogue")

        # GoalManager wrote it through the Event DSL
        self.assertIn("lead_player_to:mayor_house", ren.goals)
        self.assertEqual(ren.active_goal, "lead_player_to:mayor_house")
        self.assertEqual(ren.goal_states["lead_player_to:mayor_house"]["source"], "ai_director")
        self.assertTrue(any(
            effect.get("type") == "add_goal" and effect.get("goal") == "lead_player_to:mayor_house"
            for event in world.event_history for effect in event.get("effects", [])
        ))

        # NPC Planner executes it
        engine.end_dialogue_session()
        action = engine.npc_planner.plan_next_action(world, "npc_2")
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 20.0, "y": 20.0})


class ScenarioC_PlayerChangesPlotPremise(unittest.TestCase):
    def test_ledger_and_interrupted_goal_reach_night_director(self):
        provider = FakeNightProvider({
            "goal_changes": [{"npc_id": "npc_1", "action": "add",
                              "goal": "find_item:item_ledger", "urgency": "normal",
                              "reason": "recover the missing ledger"}],
            "story_events": [{
                "event_id": "ledger_fallout", "title": "Ledger fallout",
                "description": "the ledger moved before Ren found it",
                "trigger_at": 30 * 3600,
                "effects": [{"type": "set_belief", "npc_id": "npc_1",
                             "key": "ledger_taken", "value": 1.0}],
            }],
            "story_updates": [{"event_id": "ren_lost", "status": "cancelled"}],
            "reasoning_summary": "player moved the ledger",
        })
        engine = make_engine(provider)
        world = engine.world

        # 1. player takes the ledger early
        world.player.x, world.player.y = 22.0, 22.0
        world.pick("item_ledger")
        self.assertIn("item_ledger", world.player.inventory)

        # 2. Ren's plan gets interrupted -> the timeline can detect a deviation
        add_goal(world, "npc_2", "lead_player_to:mayor_house")
        add_goal(world, "npc_2", "reach_location:forest", urgency="urgent")
        deviations = detect_deviations(world)
        self.assertTrue(any(d.kind == "goal_suspended" and d.npc_id == "npc_2" for d in deviations))

        # 3-4. the Night Director sees the deviation and re-plans
        world.time = 22 * 3600
        world.phase = "night_director"
        world.night_director_day = None
        engine.tick()

        self.assertTrue(any(
            d["kind"] == "goal_suspended" for d in provider.context["story"]["deviations"]
        ))
        # 5. replacement StoryEvent + GoalChange applied through the frozen paths
        self.assertIsNotNone(world.story_timeline.get_event("ledger_fallout"))
        self.assertIn("find_item:item_ledger", world.get_npc("npc_1").goals)
        self.assertEqual(world.story_timeline.get_event("ren_lost")["status"], "cancelled")


class ScenarioD_GoalInterruptAndResume(unittest.TestCase):
    def test_urgent_goal_suspends_and_resumes(self):
        engine = make_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine.world, "npc_2", "lead_player_to:mayor_house")
        add_goal(engine.world, "npc_2", "reach_location:forest", urgency="urgent")

        self.assertEqual(ren.active_goal, "reach_location:forest")
        self.assertIn("lead_player_to:mayor_house", ren.goals)
        self.assertEqual(ren.goal_states["lead_player_to:mayor_house"]["status"], "suspended")
        self.assertEqual(ren.suspended_goal_stack[-1]["goal"], "lead_player_to:mayor_house")
        self.assertTrue(any(
            m.get("goal") == "lead_player_to:mayor_house" for m in ren.memory
        ))

        engine._resolve_npc_goal(ren, "reach_location:forest", "completed", "done")

        self.assertEqual(ren.active_goal, "lead_player_to:mayor_house")
        self.assertEqual(ren.goal_states["lead_player_to:mayor_house"]["status"], "active")
        self.assertEqual(ren.goals.count("lead_player_to:mayor_house"), 1)
        action = engine.npc_planner.plan_next_action(engine.world, "npc_2")
        self.assertEqual(action.source_intent["raw"], "lead_player_to:mayor_house")


class ScenarioE_NightDirector(unittest.TestCase):
    def test_night_director_runs_once_and_applies_next_day_plan(self):
        provider = FakeNightProvider({
            "goal_changes": [{"npc_id": "npc_4", "action": "add",
                              "goal": "investigate:mayor", "urgency": "normal",
                              "reason": "next day plan"}],
            "story_events": [{
                "event_id": "next_day_beat", "title": "Next day", "description": "planned",
                "trigger_at": 30 * 3600,
                "effects": [{"type": "set_belief", "npc_id": "npc_4",
                             "key": "audit_step", "value": 0.5}],
            }],
            "reasoning_summary": "plan tomorrow",
        })
        engine = make_engine(provider)
        world = engine.world
        world.time = 21 * 3600
        world.phase = "day"

        with patch.object(OpenWorldEngine, "_maybe_daily_director") as legacy:
            engine.tick()   # 22:00 -> Night Director runs once
            engine.tick()   # 23:00
            engine.tick()   # 24:00 -> day
        legacy.assert_not_called()

        self.assertEqual(provider.calls, 1)
        self.assertEqual(world.daily_orchestration_day, 1)
        self.assertIsNotNone(world.story_timeline.get_event("next_day_beat"))
        self.assertIn("investigate:mayor", world.get_npc("npc_4").goals)
        self.assertEqual(world.get_npc("npc_4").goal_states["investigate:mayor"]["source"], "night_director")
        self.assertEqual(world.get_npc("npc_4").goals.count("investigate:mayor"), 1)

        # next day: the NPC can still plan, and orchestration does not repeat
        engine.tick()
        self.assertEqual(provider.calls, 1)
        self.assertEqual(world.phase, "day")


if __name__ == "__main__":
    unittest.main()
