# -*- coding: utf-8 -*-
"""Step 12.5 tests: daily Night Director Phase (22:00-24:00)."""

import tempfile
import unittest
from pathlib import Path

import night_director
from director_input import GOAL_CHANGE_ADD, GoalChange, split_goal_string
from open_world import OpenWorldEngine
from persistence import load_world, save_world


LEAD = "lead_player_to:mayor_house"
NIGHT_START = 22 * 3600
DAY_SECONDS = 24 * 3600


class FakeNightPlanner:
    """Fake night provider: records contexts, returns a fixed report."""

    def __init__(self, report=None):
        self.report = report or {"reasoning_summary": "fake night plan"}
        self.contexts = []
        self.calls = 0

    def plan_next_day(self, context):
        self.calls += 1
        self.contexts.append(context)
        return dict(self.report)


def night_engine(provider=None):
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = provider
    engine.tick_seconds = 3600.0
    engine._last_director_day = 0
    return engine


def enter_night(engine, at=NIGHT_START, director_done=None):
    engine.world.time = float(at)
    engine.world.phase = "night_director"
    engine.world.night_director_day = director_done
    return engine


def add_goal(engine, npc_id, goal_string, urgency="normal"):
    goal_type, target = split_goal_string(goal_string)
    return engine.goal_manager.apply(engine.world, GoalChange(
        change_type=GOAL_CHANGE_ADD, npc_id=npc_id, goal_type=goal_type,
        target=target, goal_string=goal_string, urgency=urgency,
        source="ai_director", reason="test setup"))


class PhaseMathsTests(unittest.TestCase):
    def test_22_and_24_boundaries(self):
        self.assertEqual(night_director.phase_for_time(21 * 3600), "day")
        self.assertEqual(night_director.phase_for_time(NIGHT_START), "night_director")
        self.assertEqual(night_director.phase_for_time(23 * 3600 + 59 * 60), "night_director")
        self.assertEqual(night_director.phase_for_time(DAY_SECONDS), "day")
        self.assertEqual(night_director.seconds_until_next_boundary(21 * 3600), 3600)
        self.assertEqual(night_director.seconds_until_next_boundary(23 * 3600), 3600)


class NightPhaseEnterTests(unittest.TestCase):
    def test_entering_22_sets_explicit_phase(self):
        engine = night_engine()
        engine.world.time = 21 * 3600
        engine.world.phase = "day"
        engine.tick()
        self.assertEqual(engine.world.time, NIGHT_START)
        self.assertEqual(engine.world.phase, "night_director")

    def test_phase_is_persisted(self):
        engine = night_engine()
        enter_night(engine)
        data = engine.world.to_dict()
        self.assertEqual(data["phase"], "night_director")
        from world import WorldState
        restored = WorldState.from_dict(data)
        self.assertEqual(restored.phase, "night_director")


class NightPlayerBlockTests(unittest.TestCase):
    def test_all_player_actions_return_structured_refusal(self):
        engine = night_engine()
        enter_night(engine, director_done=0)
        results = [
            engine.move("up"),
            engine.analyze_thought("看看桌子"),
            engine.talk("npc_2", "你好"),
            engine.wait_world(60),
            engine.execute_action({"type": "inspect", "object_id": "mayor_desk"}),
        ]
        for result in results:
            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "night_director_phase")
            self.assertEqual(result["phase"], "night_director")
            self.assertIn("22:00", result["message"])


class NightNpcPauseTests(unittest.TestCase):
    def test_npc_planner_paused_and_goals_preserved(self):
        engine = night_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD)
        ren.position_override = [40.0, 40.0]
        enter_night(engine, director_done=0)
        before_pos = engine.world.npc_position("npc_2")
        before_goals = list(ren.goals)
        before_active = ren.active_goal

        # One pure night tick (22:00 -> 23:00) must not run the Planner.
        engine.tick()
        self.assertEqual(engine.world.phase, "night_director")
        self.assertEqual(engine.world.npc_position("npc_2"), before_pos)
        self.assertEqual(list(ren.goals), before_goals)
        self.assertEqual(ren.active_goal, before_active)
        self.assertEqual(ren.goal_states[LEAD]["status"], "active")

    def test_due_story_event_deferred_until_next_day(self):
        engine = night_engine()
        enter_night(engine, director_done=0)
        ivy = engine.world.get_npc("npc_4")
        engine.world.story_timeline.add_event({
            "event_id": "night_due_event",
            "trigger_at": float(NIGHT_START),
            "source": "scenario",
            "effects": [{"type": "set_belief", "npc_id": "npc_4", "key": "night_marker", "value": 0.5}],
        })

        engine.tick()  # 23:00, story events must not run
        self.assertNotIn("night_marker", ivy.beliefs)
        engine.tick()  # 24:00 -> day
        engine.tick()  # 01:00 next day -> deferred event runs
        self.assertEqual(ivy.beliefs.get("night_marker"), 0.5)


class NightDirectorRunTests(unittest.TestCase):
    def test_runs_once_per_night(self):
        planner = FakeNightPlanner({"reasoning_summary": "once"})
        engine = night_engine(planner)
        enter_night(engine, director_done=None)
        engine.tick()  # 22:00 -> run director, then 23:00
        engine.tick()  # 23:00 -> 24:00
        engine.tick()  # next day
        self.assertEqual(planner.calls, 1)
        self.assertEqual(engine.world.night_director_day, 0)

    def test_report_has_structured_keys(self):
        planner = FakeNightPlanner({"reasoning_summary": "keys"})
        engine = night_engine(planner)
        enter_night(engine, director_done=None)
        engine.tick()
        report = engine.world.night_director_report
        for key in ("goal_changes", "story_events", "story_updates",
                    "new_mysteries", "reasoning_summary", "applied", "day"):
            self.assertIn(key, report)

    def test_context_is_structured_and_bounded(self):
        planner = FakeNightPlanner({"reasoning_summary": "ctx"})
        engine = night_engine(planner)
        enter_night(engine, director_done=None)
        engine.tick()
        context = planner.contexts[0]
        for key in ("time", "player", "npcs", "story", "world"):
            self.assertIn(key, context)
        self.assertIn("active_goal", context["npcs"][0])
        self.assertIn("timeline", context["story"])
        self.assertIn("weather", context["world"])

    def test_default_plan_without_provider(self):
        engine = night_engine(provider=None)
        enter_night(engine, director_done=None)
        engine.tick()
        report = engine.world.night_director_report
        self.assertEqual(report["goal_changes"], [])
        self.assertIn("reasoning_summary", report)


class NightReportApplicationTests(unittest.TestCase):
    def test_goal_changes_go_through_goal_manager(self):
        planner = FakeNightPlanner({
            "goal_changes": [{
                "npc_id": "npc_2", "action": "add", "goal": "investigate:mayor",
                "urgency": "urgent", "reason": "night plan",
            }],
            "reasoning_summary": "goal plan",
        })
        engine = night_engine(planner)
        enter_night(engine, director_done=None)
        engine.tick()
        ren = engine.world.get_npc("npc_2")
        self.assertIn("investigate:mayor", ren.goals)
        self.assertEqual(ren.goal_states["investigate:mayor"]["urgency"], "urgent")
        self.assertEqual(ren.goal_states["investigate:mayor"]["source"], "night_director")

    def test_story_events_added_to_timeline(self):
        planner = FakeNightPlanner({
            "story_events": [{
                "event_id": "night_story_event",
                "title": "Night story",
                "description": "planned at night",
                "trigger_at": float(NIGHT_START + 4 * 3600),
                "effects": [{"type": "set_belief", "npc_id": "npc_4",
                             "key": "night_planned", "value": 0.4}],
            }],
            "reasoning_summary": "story plan",
        })
        engine = night_engine(planner)
        enter_night(engine, director_done=None)
        engine.tick()
        self.assertIsNotNone(engine.world.story_timeline.get_event("night_story_event"))

    def test_new_mysteries_stored_as_story_seeds(self):
        planner = FakeNightPlanner({
            "new_mysteries": [{"type": "rumor", "summary": "夜里出现了奇怪的光。"}],
            "reasoning_summary": "mystery",
        })
        engine = night_engine(planner)
        enter_night(engine, director_done=None)
        engine.tick()
        self.assertTrue(any(
            "奇怪的光" in str(seed.get("content", ""))
            for seed in engine.world.story_seeds
        ))


class NightResumeAndPersistenceTests(unittest.TestCase):
    def test_day_resumes_after_midnight(self):
        engine = night_engine()
        enter_night(engine, at=23 * 3600, director_done=0)
        engine.tick()  # 24:00 -> day
        self.assertEqual(engine.world.phase, "day")
        self.assertTrue(engine.move("up")["success"])

    def test_save_load_preserves_phase_and_report(self):
        planner = FakeNightPlanner({"reasoning_summary": "saved"})
        engine = night_engine(planner)
        enter_night(engine, director_done=None)
        engine.tick()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "night.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        self.assertEqual(loaded.phase, "night_director")
        self.assertEqual(loaded.night_director_day, 0)
        self.assertEqual(loaded.night_director_report["reasoning_summary"], "saved")

    def test_night_goals_resume_next_day(self):
        engine = night_engine()
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD)
        enter_night(engine, at=23 * 3600, director_done=0)
        engine.tick()  # -> day
        engine.tick()  # day tick resumes planner
        self.assertEqual(ren.active_goal, LEAD)
        self.assertIn(LEAD, ren.goals)


if __name__ == "__main__":
    unittest.main()
