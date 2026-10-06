# -*- coding: utf-8 -*-
"""Step 12.3 tests: Goal lifecycle, preemption, interrupt and resume.

Director decides goals (and their urgency); GoalManager owns lifecycle state;
NPCPlanner only executes the current active goal.
"""

import tempfile
import unittest
from pathlib import Path

from director_input import (
    GOAL_CHANGE_ADD,
    GoalChange,
    split_goal_string,
)
from entities import NPC
from npc_planner import NPCPlanner
from open_world import OpenWorldEngine
from persistence import load_world, save_world


LEAD = "lead_player_to:mayor_house"
URGENT = "reach_location:forest"
CRITICAL = "reach_location:stone_road"


def lifecycle_engine():
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.tick_seconds = 3600.0
    # Avoid the daily Director adding unrelated goals during these tests.
    engine._last_director_day = 0
    return engine


def add_goal(engine, npc_id, goal_string, urgency="normal", priority=None):
    goal_type, target = split_goal_string(goal_string)
    change = GoalChange(
        change_type=GOAL_CHANGE_ADD,
        npc_id=npc_id,
        goal_type=goal_type,
        target=target,
        goal_string=goal_string,
        urgency=urgency,
        priority=priority,
        source="ai_director",
        reason="test change",
    )
    return engine.goal_manager.apply(engine.world, change)


class ActiveGoalTests(unittest.TestCase):
    def test_1_active_goal_executes(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", "reach_location:forest")
        self.assertEqual(ren.active_goal, "reach_location:forest")
        self.assertEqual(ren.goal_states["reach_location:forest"]["status"], "active")
        action = engine.npc_planner.plan_next_action(engine.world, "npc_2")
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 40.0, "y": 40.0})

    def test_1b_goal_manager_is_the_only_lifecycle_writer(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        # A raw npc.add_goal() creates no lifecycle state...
        ren.add_goal("reach_location:forest")
        self.assertIsNone(ren.active_goal)
        self.assertNotIn("reach_location:forest", ren.goal_states)
        ren.remove_goal("reach_location:forest")
        # ...GoalManager writes both the goal and its lifecycle state.
        add_goal(engine, "npc_2", "reach_location:forest")
        self.assertIn("reach_location:forest", ren.goal_states)
        self.assertEqual(ren.active_goal, "reach_location:forest")


class NoPreemptionTests(unittest.TestCase):
    def test_2_normal_goal_does_not_preempt(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", "investigate:some_clue", urgency="normal")

        self.assertEqual(ren.active_goal, LEAD)
        self.assertEqual(ren.goal_states[LEAD]["status"], "active")
        self.assertEqual(ren.goal_states["investigate:some_clue"]["status"], "pending")
        action = engine.npc_planner.plan_next_action(engine.world, "npc_2")
        self.assertEqual(action.source_intent["raw"], LEAD)

    def test_2b_priority_alone_does_not_preempt(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="normal", priority=0.99)
        # Higher priority but same urgency -> still queued.
        self.assertEqual(ren.active_goal, LEAD)
        self.assertEqual(ren.goal_states[URGENT]["status"], "pending")


class PreemptionTests(unittest.TestCase):
    def test_3_urgent_preempts_normal(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")

        self.assertEqual(ren.active_goal, URGENT)
        self.assertEqual(ren.goal_states[LEAD]["status"], "suspended")
        self.assertEqual(ren.goal_states[URGENT]["status"], "active")

    def test_4_critical_preempts_urgent(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")
        add_goal(engine, "npc_2", CRITICAL, urgency="critical")

        self.assertEqual(ren.active_goal, CRITICAL)
        self.assertEqual(ren.goal_states[URGENT]["status"], "suspended")
        self.assertEqual(ren.goal_states[LEAD]["status"], "suspended")

    def test_5_suspended_goal_is_kept_not_failed(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")

        self.assertIn(LEAD, ren.goals)
        self.assertEqual(ren.goal_states[LEAD]["status"], "suspended")
        self.assertNotEqual(ren.goal_states[LEAD]["status"], "failed")
        self.assertEqual(ren.goal_states[LEAD]["interrupted_by"], URGENT)
        self.assertEqual(ren.suspended_goal_stack[-1]["goal"], LEAD)
        self.assertEqual(ren.suspended_goal_stack[-1]["interrupted_by"], URGENT)

    def test_6_suspended_goal_is_not_planned(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")
        self.assertEqual(engine.npc_planner.active_goal_of(ren), URGENT)
        self.assertNotEqual(engine.npc_planner.active_goal_of(ren), LEAD)
        action = engine.npc_planner.plan_next_action(engine.world, "npc_2")
        self.assertEqual(action.source_intent["raw"], URGENT)


class ResumeTests(unittest.TestCase):
    def test_7_urgent_completion_resumes_original(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")
        ren.position_override = [40.0, 40.0]
        engine._apply_goal_lifecycle(ren)

        self.assertEqual(ren.goal_states[URGENT]["status"], "completed")
        self.assertEqual(ren.active_goal, LEAD)
        self.assertEqual(ren.goal_states[LEAD]["status"], "active")
        action = engine.npc_planner.plan_next_action(engine.world, "npc_2")
        self.assertEqual(action.source_intent["raw"], LEAD)

    def test_8_urgent_failure_resumes_original(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")
        engine._resolve_npc_goal(ren, URGENT, "failed", "test failure")

        self.assertEqual(ren.goal_states[URGENT]["status"], "failed")
        self.assertEqual(ren.active_goal, LEAD)
        self.assertEqual(ren.goal_states[LEAD]["status"], "active")

    def test_9_completed_suspended_goal_is_not_resumed(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")
        engine._resolve_npc_goal(ren, LEAD, "completed", "already done")
        engine._resolve_npc_goal(ren, URGENT, "completed", "done")

        self.assertIsNone(ren.active_goal)
        self.assertEqual(ren.goal_states[LEAD]["status"], "completed")

    def test_10_failed_suspended_goal_is_not_resumed(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")
        engine._resolve_npc_goal(ren, LEAD, "failed", "invalid")
        engine._resolve_npc_goal(ren, URGENT, "failed", "done")

        self.assertIsNone(ren.active_goal)
        self.assertEqual(ren.goal_states[LEAD]["status"], "failed")

    def test_11_two_level_suspended_stack(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")
        add_goal(engine, "npc_2", CRITICAL, urgency="critical")
        self.assertEqual([r["goal"] for r in ren.suspended_goal_stack], [LEAD, URGENT])

        engine._resolve_npc_goal(ren, CRITICAL, "completed", "done C")
        self.assertEqual(ren.active_goal, URGENT)
        engine._resolve_npc_goal(ren, URGENT, "completed", "done B")
        self.assertEqual(ren.active_goal, LEAD)
        self.assertEqual(ren.suspended_goal_stack, [])

    def test_14_follow_lead_suspended_and_restored(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine._set_follow_state("npc_2", True)
        add_goal(engine, "npc_2", URGENT, urgency="urgent")

        self.assertEqual(ren.active_goal, URGENT)
        self.assertEqual(ren.goal_states["follow player"]["status"], "suspended")
        engine._resolve_npc_goal(ren, URGENT, "completed", "done")
        self.assertEqual(ren.active_goal, "follow player")
        self.assertTrue(engine.world.characters_state["npc_2"]["following_player"])


class MemoryAndHistoryTests(unittest.TestCase):
    def test_12_preemption_leaves_memory_and_history(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")

        memories = [m for m in ren.memory if m.get("goal") == LEAD and "suspended" in str(m.get("content", ""))]
        self.assertTrue(memories)
        history_actions = [h.get("action") for h in engine.world.history if isinstance(h, dict)]
        self.assertIn("npc_goal_suspended", history_actions)

    def test_13_leaving_player_is_recorded(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")

        departure = [m for m in ren.memory if m.get("interrupted_goal") == LEAD]
        self.assertTrue(departure)
        self.assertEqual(departure[0]["new_goal"], URGENT)
        history_actions = [h.get("action") for h in engine.world.history if isinstance(h, dict)]
        self.assertIn("npc_left_player", history_actions)


class PersistenceAndIntegrityTests(unittest.TestCase):
    def test_15_suspended_state_survives_save_load(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        add_goal(engine, "npc_2", URGENT, urgency="urgent")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lifecycle.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        loaded_ren = loaded.get_npc("npc_2")
        self.assertEqual(loaded_ren.active_goal, URGENT)
        self.assertEqual(loaded_ren.goal_states[LEAD]["status"], "suspended")
        self.assertEqual(loaded_ren.suspended_goal_stack[-1]["goal"], LEAD)
        self.assertEqual(loaded_ren.suspended_goal_stack[-1]["interrupted_by"], URGENT)

    def test_16_no_duplicate_goal(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", URGENT, urgency="normal")
        second = add_goal(engine, "npc_2", URGENT, urgency="urgent")

        self.assertFalse(second["applied"])
        self.assertEqual(second["reason"], "already_present")
        self.assertEqual(ren.goals.count(URGENT), 1)
        self.assertEqual(ren.active_goal, URGENT)

    def test_17_18_planner_does_not_create_or_decide_goals(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD, urgency="normal")
        before_goals = list(ren.goals)
        before_active = ren.active_goal
        before_states = {k: dict(v) for k, v in ren.goal_states.items()}

        engine.npc_planner.plan_next_action(engine.world, "npc_2")

        self.assertEqual(list(ren.goals), before_goals)
        self.assertEqual(ren.active_goal, before_active)
        self.assertEqual(ren.goal_states, before_states)


if __name__ == "__main__":
    unittest.main()
