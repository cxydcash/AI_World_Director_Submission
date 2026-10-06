# -*- coding: utf-8 -*-
"""Step 12.4 tests: StoryEvent goals enter the shared Goal Lifecycle.

StoryEvent -> EventExecutor -> GoalManager -> Goal Lifecycle -> Event DSL.
"""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from entities import NPC
from open_world import OpenWorldEngine
from persistence import load_world, save_world
from world import WorldState


LEAD = "lead_player_to:mayor_house"
URGENT = "reach_location:forest"
CRITICAL = "reach_location:stone_road"


def lifecycle_engine():
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.tick_seconds = 60.0
    engine._last_director_day = 0
    return engine


def add_story_goal(engine, npc_id, goal, urgency="normal", source="scenario",
                   event_id=None, effects=None):
    event_id = event_id or f"story_{npc_id}_{goal}_{urgency}".replace(":", "_")
    engine.world.story_timeline.add_event({
        "event_id": event_id,
        "trigger_at": float(engine.world.time),
        "source": source,
        "effects": effects or [
            {"type": "add_goal", "npc_id": npc_id, "goal": goal, "urgency": urgency},
        ],
    })
    engine.world.check_story_events()
    return event_id


class StoryEventGoalRoutingTests(unittest.TestCase):
    def test_1_add_goal_goes_through_goal_manager(self):
        engine = lifecycle_engine()
        ivy = engine.world.get_npc("npc_4")
        before_history = len(engine.world.event_history)
        add_story_goal(engine, "npc_4", "reach_location:forest")
        self.assertIn("reach_location:forest", ivy.goals)
        # GoalManager wrote the lifecycle state.
        entry = ivy.goal_states["reach_location:forest"]
        self.assertEqual(entry["status"], "active")
        self.assertEqual(entry["source"], "story_event")
        # No duplicate history from the inner write.
        self.assertEqual(len(engine.world.event_history) - before_history, 1)

    def test_2_story_goal_becomes_active_goal(self):
        engine = lifecycle_engine()
        ivy = engine.world.get_npc("npc_4")
        add_story_goal(engine, "npc_4", "reach_location:forest")
        self.assertEqual(ivy.active_goal, "reach_location:forest")

    def test_3_normal_story_goal_does_not_preempt(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        engine.goal_manager.apply(engine.world, _goal_change(LEAD))
        add_story_goal(engine, "npc_2", "investigate:some_clue", urgency="normal")
        self.assertEqual(ren.active_goal, LEAD)
        self.assertEqual(ren.goal_states["investigate:some_clue"]["status"], "pending")

    def test_4_urgent_story_goal_preempts_normal(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        engine.goal_manager.apply(engine.world, _goal_change(LEAD))
        add_story_goal(engine, "npc_2", URGENT, urgency="urgent")
        self.assertEqual(ren.active_goal, URGENT)
        self.assertEqual(ren.goal_states[LEAD]["status"], "suspended")

    def test_5_critical_story_goal_preempts_urgent(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        engine.goal_manager.apply(engine.world, _goal_change(LEAD))
        add_story_goal(engine, "npc_2", URGENT, urgency="urgent", event_id="story_urgent")
        add_story_goal(engine, "npc_2", CRITICAL, urgency="critical", event_id="story_critical")
        self.assertEqual(ren.active_goal, CRITICAL)
        self.assertEqual(ren.goal_states[URGENT]["status"], "suspended")
        self.assertEqual(ren.goal_states[LEAD]["status"], "suspended")

    def test_12_story_goal_saves_source(self):
        engine = lifecycle_engine()
        ivy = engine.world.get_npc("npc_4")
        add_story_goal(engine, "npc_4", "reach_location:forest", source="scenario")
        self.assertEqual(ivy.goal_states["reach_location:forest"]["source"], "story_event")
        add_story_goal(engine, "npc_4", "reach_location:stone_road",
                       source="ai_director", event_id="ai_story")
        self.assertEqual(ivy.goal_states["reach_location:stone_road"]["source"], "ai_director")

    def test_13_story_goal_saves_urgency(self):
        engine = lifecycle_engine()
        ivy = engine.world.get_npc("npc_4")
        add_story_goal(engine, "npc_4", URGENT, urgency="urgent")
        self.assertEqual(ivy.goal_states[URGENT]["urgency"], "urgent")


class StoryEventResumeTests(unittest.TestCase):
    def test_6_suspended_story_goal_recovers_original(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        engine.goal_manager.apply(engine.world, _goal_change(LEAD))
        add_story_goal(engine, "npc_2", URGENT, urgency="urgent")
        self.assertEqual(ren.active_goal, URGENT)

        ren.position_override = [40.0, 40.0]
        engine._apply_goal_lifecycle(ren)
        self.assertEqual(ren.goal_states[URGENT]["status"], "completed")
        self.assertEqual(ren.active_goal, LEAD)

    def test_7_story_goal_completed_recovers_original(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        engine.goal_manager.apply(engine.world, _goal_change(LEAD))
        add_story_goal(engine, "npc_2", URGENT, urgency="urgent")
        engine._resolve_npc_goal(ren, URGENT, "completed", "done")
        self.assertEqual(ren.active_goal, LEAD)
        self.assertEqual(ren.goal_states[LEAD]["status"], "active")

    def test_8_story_goal_failed_recovers_original(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        engine.goal_manager.apply(engine.world, _goal_change(LEAD))
        add_story_goal(engine, "npc_2", URGENT, urgency="urgent")
        engine._resolve_npc_goal(ren, URGENT, "failed", "failed")
        self.assertEqual(ren.active_goal, LEAD)

    def test_9_story_remove_goal_enters_lifecycle(self):
        engine = lifecycle_engine()
        ivy = engine.world.get_npc("npc_4")
        add_story_goal(engine, "npc_4", "reach_location:forest")
        self.assertEqual(ivy.active_goal, "reach_location:forest")
        add_story_goal(engine, "npc_4", "reach_location:forest", event_id="remove_forest",
                       effects=[{"type": "remove_goal", "npc_id": "npc_4",
                                 "goal": "reach_location:forest"}])
        self.assertNotIn("reach_location:forest", ivy.goals)
        self.assertEqual(ivy.goal_states["reach_location:forest"]["status"], "cancelled")
        self.assertIsNone(ivy.active_goal)

    def test_10_story_set_follow_target_goes_through_lifecycle(self):
        engine = lifecycle_engine()
        ivy = engine.world.get_npc("npc_4")
        add_story_goal(
            engine, "npc_4", None, event_id="follow_event",
            effects=[{"type": "set_follow_target", "npc_id": "npc_4",
                      "follow_target": "player"}],
        )
        self.assertIn("follow player", ivy.goals)
        self.assertEqual(ivy.goal_states["follow player"]["source"], "story_event")
        self.assertTrue(engine.world.characters_state["npc_4"]["following_player"])

    def test_11_story_goal_has_no_duplicate_write(self):
        engine = lifecycle_engine()
        ivy = engine.world.get_npc("npc_4")
        before = len(engine.world.event_history)
        add_story_goal(engine, "npc_4", "reach_location:forest")
        self.assertEqual(ivy.goals.count("reach_location:forest"), 1)
        self.assertEqual(len(engine.world.event_history) - before, 1)

    def test_14_story_suspended_state_survives_save_load(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        engine.goal_manager.apply(engine.world, _goal_change(LEAD))
        add_story_goal(engine, "npc_2", URGENT, urgency="urgent")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "story_lifecycle.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        loaded_ren = loaded.get_npc("npc_2")
        self.assertEqual(loaded_ren.active_goal, URGENT)
        self.assertEqual(loaded_ren.goal_states[LEAD]["status"], "suspended")
        self.assertEqual(loaded_ren.goal_states[URGENT]["urgency"], "urgent")
        self.assertEqual(loaded_ren.suspended_goal_stack[-1]["goal"], LEAD)

    def test_15_follow_interrupted_by_story_event_and_restored(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine._set_follow_state("npc_2", True)
        add_story_goal(engine, "npc_2", URGENT, urgency="urgent")

        self.assertEqual(ren.active_goal, URGENT)
        self.assertEqual(ren.goal_states["follow player"]["status"], "suspended")
        self.assertTrue(engine.world.characters_state["npc_2"]["following_player"])
        self.assertNotEqual(engine.npc_planner.active_goal_of(ren), "follow player")

        engine._resolve_npc_goal(ren, URGENT, "completed", "done")
        self.assertEqual(ren.active_goal, "follow player")
        self.assertEqual(ren.goal_states["follow player"]["status"], "active")


class StoryEventDepartureTests(unittest.TestCase):
    def test_16_leaving_player_is_recorded(self):
        engine = lifecycle_engine()
        ren = engine.world.get_npc("npc_2")
        engine.goal_manager.apply(engine.world, _goal_change(LEAD))
        add_story_goal(engine, "npc_2", URGENT, urgency="urgent")

        history_actions = [h.get("action") for h in engine.world.history if isinstance(h, dict)]
        self.assertIn("npc_goal_suspended", history_actions)
        self.assertIn("npc_left_player", history_actions)
        departure = [m for m in ren.memory if m.get("interrupted_goal") == LEAD]
        self.assertTrue(departure)
        self.assertEqual(departure[0]["new_goal"], URGENT)


class StoryEventCompatibilityTests(unittest.TestCase):
    def test_17_planner_does_not_create_story_goals(self):
        engine = lifecycle_engine()
        ivy = engine.world.get_npc("npc_4")
        add_story_goal(engine, "npc_4", "reach_location:forest")
        before_goals = list(ivy.goals)
        before_active = ivy.active_goal
        before_states = {k: dict(v) for k, v in ivy.goal_states.items()}
        engine.npc_planner.plan_next_action(engine.world, "npc_4")
        self.assertEqual(list(ivy.goals), before_goals)
        self.assertEqual(ivy.active_goal, before_active)
        self.assertEqual(ivy.goal_states, before_states)

    def test_18_corruption_timeline_still_works(self):
        engine = lifecycle_engine()
        engine.tick_seconds = 8 * 3600.0
        engine.tick()
        ids = [e.get("event_id") for e in engine.world.story_event_history]
        self.assertIn("ren_discovers_ledger", ids)
        ren = engine.world.get_npc("npc_2")
        self.assertGreater(ren.beliefs.get("mayor_is_corrupt", 0.0), 0.0)

    def test_19_ai_generated_story_event_still_works(self):
        engine = lifecycle_engine()
        ivy = engine.world.get_npc("npc_4")
        add_story_goal(engine, "npc_4", "reach_location:forest",
                       source="ai_director", event_id="ai_generated")
        self.assertIn("reach_location:forest", ivy.goals)
        self.assertEqual(ivy.goal_states["reach_location:forest"]["source"], "ai_director")


class ArchitectureStoryGoalTests(unittest.TestCase):
    """StoryEvent related goal writes must pass through GoalManager."""

    def test_npc_and_world_goal_writes_go_through_goal_manager(self):
        engine = lifecycle_engine()
        records = []

        real_npc_add = NPC.add_goal
        real_npc_remove = NPC.remove_goal
        real_world_add = WorldState.add_goal
        real_world_remove = WorldState.remove_goal

        def spy_npc_add(self, goal):
            records.append(("npc.add_goal", self.id, str(goal), [f.function for f in inspect.stack()]))
            return real_npc_add(self, goal)

        def spy_npc_remove(self, goal):
            records.append(("npc.remove_goal", self.id, str(goal), [f.function for f in inspect.stack()]))
            return real_npc_remove(self, goal)

        def spy_world_add(self, npc_id, goal):
            records.append(("world.add_goal", npc_id, str(goal), [f.function for f in inspect.stack()]))
            return real_world_add(self, npc_id, goal)

        def spy_world_remove(self, npc_id, goal):
            records.append(("world.remove_goal", npc_id, str(goal), [f.function for f in inspect.stack()]))
            return real_world_remove(self, npc_id, goal)

        with patch.object(NPC, "add_goal", spy_npc_add), \
                patch.object(NPC, "remove_goal", spy_npc_remove), \
                patch.object(WorldState, "add_goal", spy_world_add), \
                patch.object(WorldState, "remove_goal", spy_world_remove):
            add_story_goal(engine, "npc_4", "reach_location:forest", event_id="arch_add")
            add_story_goal(engine, "npc_4", "reach_location:forest", event_id="arch_remove",
                           effects=[{"type": "remove_goal", "npc_id": "npc_4",
                                     "goal": "reach_location:forest"}])
            add_story_goal(engine, "npc_4", None, event_id="arch_follow",
                           effects=[{"type": "set_follow_target", "npc_id": "npc_4",
                                     "follow_target": "player"}])

        self.assertTrue(records)
        for kind, npc_id, goal, stack in records:
            self.assertTrue(
                {"_apply_add", "_apply_remove"} & set(stack),
                f"{kind} for {goal} did not go through GoalManager: {stack}",
            )


def _goal_change(goal_string, urgency="normal"):
    from director_input import GOAL_CHANGE_ADD, GoalChange, split_goal_string
    goal_type, target = split_goal_string(goal_string)
    return GoalChange(
        change_type=GOAL_CHANGE_ADD, npc_id="npc_2", goal_type=goal_type,
        target=target, goal_string=goal_string, urgency=urgency,
        source="ai_director", reason="test setup",
    )


if __name__ == "__main__":
    unittest.main()
