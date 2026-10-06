"""Step 11.75 tests: AI Director goal execution priority / scheduling."""

import tempfile
import unittest
from pathlib import Path

from director_input import GOAL_CHANGE_ADD, GoalChange, split_goal_string
from npc_planner import AI_GOAL_PRIORITY, NPCPlanner
from open_world import OpenWorldEngine
from persistence import load_world, save_world


def scheduler_engine():
    """A fresh engine isolated from the daily Director for scheduler tests."""
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.tick_seconds = 60.0
    engine._last_director_day = 0  # pretend today's Director cycle already ran
    return engine


def add_story_event(engine, effects, event_id="ai_goal_event", source="ai_director", trigger_at=0.0):
    engine.world.story_timeline.add_event({
        "event_id": event_id,
        "trigger_at": trigger_at,
        "effects": effects,
        "source": source,
    })


def add_goal(npc_id, goal):
    return {"type": "add_goal", "npc_id": npc_id, "goal": goal}


def add_lifecycle_goal(engine, npc_id, goal_string, urgency="normal", source="scenario"):
    """Make an existing goal the active goal (Step 12.4 lifecycle)."""
    goal_type, target = split_goal_string(goal_string)
    return engine.goal_manager.apply(engine.world, GoalChange(
        change_type=GOAL_CHANGE_ADD, npc_id=npc_id, goal_type=goal_type,
        target=target, goal_string=goal_string, urgency=urgency,
        source=source, reason="test setup"))


class AIGoalPriorityTests(unittest.TestCase):
    def test_ai_goal_outranks_a_normal_goal(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["observe village"]  # normal goal -> reach_location:town (70,70)

        add_story_event(engine, [add_goal("npc_4", "reach_location:forest")])
        engine.tick()

        self.assertIn("reach_location:forest", ivy.goals)
        self.assertEqual(ivy.goal_states["reach_location:forest"]["source"], "ai_director")
        # The NPC walks to the AI goal (forest), not its own goal (town).
        self.assertEqual(ivy.current_action.get("parameters"), {"x": 40.0, "y": 40.0})

    def test_completed_ai_goal_hands_the_npc_back_to_its_original_goal(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [40.0, 40.0]  # already standing at the forest
        ivy.goals = ["observe village"]

        add_story_event(engine, [add_goal("npc_4", "reach_location:forest")])
        engine.tick()

        self.assertEqual(ivy.goal_states["reach_location:forest"]["status"], "completed")
        # Original goal resumes: Ivy heads to town.
        self.assertEqual(ivy.current_action.get("parameters"), {"x": 70.0, "y": 70.0})

    def test_ai_goal_is_not_repeated_once_it_is_completed(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [40.0, 40.0]
        ivy.goals = ["observe village"]
        add_story_event(engine, [add_goal("npc_4", "reach_location:forest")])

        engine.tick()
        engine.tick()
        planner = NPCPlanner()
        action = planner.plan_next_action(engine.world, "npc_4")
        self.assertNotEqual((action.source_intent or {}).get("raw"), "reach_location:forest")

    def test_unproductive_ai_goal_fails_and_is_not_retried(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["reach_location:forest"]
        # No evidence exists near Ivy, so this investigation can never progress.
        add_story_event(engine, [add_goal("npc_4", "investigate:ev_nowhere")])

        for _ in range(4):
            engine.tick()

        state = ivy.goal_states["investigate:ev_nowhere"]
        self.assertEqual(state["status"], "failed")
        self.assertIn("no progress", state["reason"])

        planner = NPCPlanner()
        action = planner.plan_next_action(engine.world, "npc_4")
        self.assertNotEqual((action.source_intent or {}).get("raw"), "investigate:ev_nowhere")

    def test_ai_goal_does_not_hold_the_planner_forever(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["reach_location:forest"]
        add_story_event(engine, [add_goal("npc_4", "investigate:ev_nowhere")])

        for _ in range(4):
            engine.tick()

        action = NPCPlanner().plan_next_action(engine.world, "npc_4")
        self.assertIsNotNone(action)
        self.assertEqual((action.source_intent or {}).get("raw"), "reach_location:forest")

    def test_ai_goal_preempts_an_in_progress_move(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["observe village"]

        engine.tick()  # Ivy starts the long walk to town
        self.assertEqual(ivy.current_action.get("parameters"), {"x": 70.0, "y": 70.0})

        add_story_event(engine, [add_goal("npc_4", "reach_location:forest")])
        engine.tick()  # the fresh AI goal must take over the in-progress walk

        self.assertEqual(ivy.current_action.get("parameters"), {"x": 40.0, "y": 40.0})

    def test_blocked_ai_goal_enters_the_failed_lifecycle(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [5.0, 95.0]
        ivy.goals = ["reach_location:forest"]
        # talk_to needs the target in vision; Ren is on the far side of the map.
        add_story_event(engine, [add_goal("npc_4", "talk_to:npc_2")])

        for _ in range(4):
            engine.tick()

        state = ivy.goal_states["talk_to:npc_2"]
        self.assertEqual(state["status"], "failed")
        self.assertIn("blocked AI goal", state["reason"])
        action = NPCPlanner().plan_next_action(engine.world, "npc_4")
        self.assertNotEqual((action.source_intent or {}).get("raw"), "talk_to:npc_2")

    def test_two_ai_goals_resolve_deterministically(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["observe village"]
        add_story_event(engine, [
            add_goal("npc_4", "reach_location:forest"),
            add_goal("npc_4", "reach_location:town"),
        ])

        engine.tick()

        planner = NPCPlanner()
        forest = planner._apply_source_priority(ivy, planner.parse_goal("reach_location:forest"))
        town = planner._apply_source_priority(ivy, planner.parse_goal("reach_location:town"))
        self.assertEqual((forest.priority, town.priority), (AI_GOAL_PRIORITY, AI_GOAL_PRIORITY))
        # Equal boosted priority -> stable original order decides (forest first).
        self.assertEqual(ivy.current_action.get("parameters"), {"x": 40.0, "y": 40.0})

    def test_explicit_lead_player_to_still_outranks_an_ai_goal(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        # Step 12.4: the pre-existing plan is an active goal; the normal AI goal
        # must queue behind it instead of taking the tick.
        add_lifecycle_goal(engine, "npc_4", "lead_player_to:home")
        add_story_event(engine, [add_goal("npc_4", "reach_location:forest")])

        engine.tick()

        self.assertEqual(ivy.current_action.get("parameters"), {"x": 20.0, "y": 20.0})

    def test_goal_priority_does_not_creep_from_tick_to_tick(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["observe village"]
        add_story_event(engine, [add_goal("npc_4", "investigate:ev_nowhere")])

        engine.tick()
        planner = NPCPlanner()
        first = planner._apply_source_priority(ivy, planner.parse_goal("investigate:ev_nowhere"))
        engine.tick()
        second = planner._apply_source_priority(ivy, planner.parse_goal("investigate:ev_nowhere"))

        self.assertEqual(first.priority, second.priority)
        self.assertEqual(second.priority, AI_GOAL_PRIORITY)
        self.assertLess(second.priority, 1.0)
        # The boost is derived from the tag, never stored as a growing number.
        self.assertNotIn("priority", ivy.goal_states["investigate:ev_nowhere"])


class GoalSourceIsolationTests(unittest.TestCase):
    def test_scenario_event_goals_are_not_tagged(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["observe village"]
        add_story_event(engine, [add_goal("npc_4", "reach_location:forest")], source="scenario")

        engine.tick()

        # Step 12.4: scenario StoryEvent goals now enter the Goal Lifecycle, but
        # they are NOT tagged as ai_director (no AI priority boost).
        entry = ivy.goal_states.get("reach_location:forest", {})
        self.assertEqual(entry.get("source"), "story_event")
        self.assertNotEqual(entry.get("source"), "ai_director")
        # With no explicit active plan, the StoryEvent goal owns the tick.
        self.assertEqual(ivy.active_goal, "reach_location:forest")
        self.assertEqual(ivy.current_action.get("parameters"), {"x": 40.0, "y": 40.0})

    def test_untagged_goals_keep_their_own_priority(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["reach_location:forest"]

        planner = NPCPlanner()
        action = planner.plan_next_action(engine.world, "npc_4")

        self.assertEqual(planner.parse_goal("reach_location:forest").priority, 0.5)
        self.assertEqual((action.source_intent or {}).get("priority"), 0.5)

    def test_ai_goal_source_survives_save_and_load(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["observe village"]
        add_story_event(engine, [add_goal("npc_4", "reach_location:forest")])
        engine.tick()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "goal_source.json"
            save_world(engine.world, path)
            loaded = load_world(path)

        self.assertEqual(
            loaded.get_npc("npc_4").goal_states["reach_location:forest"]["source"],
            "ai_director",
        )


class ExistingGoalCompatibilityTests(unittest.TestCase):
    def test_existing_goal_behaviours_still_plan(self):
        planner = NPCPlanner()
        for goal in (
            "follow player",
            "lead_player_to:home",
            "investigate:ev_any",
            "reach_location:forest",
            "talk_to:npc_2",
            "find_item:item_ledger",
            "find_npc:npc_2",
            "help_npc:npc_2",
            "escape",
            "work",
            "wait",
        ):
            self.assertTrue(planner.can_plan_goal(goal), f"not planned: {goal}")

    def test_reach_location_still_produces_a_move(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [10.0, 10.0]
        ivy.goals = ["reach_location:forest"]

        action = NPCPlanner().plan_next_action(engine.world, "npc_4")
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 40.0, "y": 40.0})

    def test_follow_goal_is_unaffected_by_the_boost(self):
        engine = scheduler_engine()
        ivy = engine.world.get_npc("npc_4")
        ivy.position_override = [0.0, 0.0]
        engine.world.player.x, engine.world.player.y = 10.0, 0.0
        ivy.goals = ["follow player"]

        action = NPCPlanner().plan_next_action(engine.world, "npc_4")
        self.assertEqual(action.action_type, "move")
        self.assertEqual((action.source_intent or {}).get("goal_type"), "follow_npc")


if __name__ == "__main__":
    unittest.main()

