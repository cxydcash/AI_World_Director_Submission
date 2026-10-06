# -*- coding: utf-8 -*-
"""Step 12.5 fix tests: one daily AI Director orchestration entry.

The 22:00-24:00 Night Director is the only daily orchestration. The legacy
``_maybe_daily_director`` is kept for compatibility but must never run from the
OpenWorld runtime, and must not fire again at 00:00.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from director_input import GOAL_CHANGE_ADD, GoalChange, split_goal_string
from open_world import OpenWorldEngine
from persistence import load_world, save_world


LEAD = "lead_player_to:mayor_house"
NIGHT_START = 22 * 3600


class CountingPlanner:
    def __init__(self, report=None, error=None):
        self.report = report or {"reasoning_summary": "night plan"}
        self.error = error
        self.calls = 0

    def plan_next_day(self, context):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.report)


def engine_with(planner=None):
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = planner
    engine.tick_seconds = 3600.0
    engine._last_director_day = 0
    return engine


def enter_night(engine, at=NIGHT_START, director_done=None):
    engine.world.time = float(at)
    engine.world.phase = "night_director"
    engine.world.night_director_day = director_done
    return engine


def add_goal(engine, npc_id, goal_string):
    goal_type, target = split_goal_string(goal_string)
    return engine.goal_manager.apply(engine.world, GoalChange(
        change_type=GOAL_CHANGE_ADD, npc_id=npc_id, goal_type=goal_type,
        target=target, goal_string=goal_string, source="ai_director",
        reason="test setup"))


class SingleDailyDirectorTests(unittest.TestCase):
    def test_tick_never_calls_the_legacy_daily_director(self):
        engine = engine_with(CountingPlanner())
        with patch.object(OpenWorldEngine, "_maybe_daily_director") as spy:
            for _ in range(24):
                engine.tick()
        spy.assert_not_called()

    def test_one_night_orchestration_per_day(self):
        planner = CountingPlanner()
        engine = engine_with(planner)
        for _ in range(24):
            engine.tick()
        self.assertEqual(planner.calls, 1)
        self.assertEqual(engine.world.daily_orchestration_day, 1)

    def test_success_at_22_does_not_repeat_at_midnight(self):
        planner = CountingPlanner()
        engine = engine_with(planner)
        engine.world.time = 21 * 3600
        engine.world.phase = "day"
        engine.tick()  # 22:00 -> orchestration
        self.assertEqual(planner.calls, 1)
        engine.tick()  # 23:00
        engine.tick()  # 24:00 -> day
        engine.tick()  # 01:00 next day
        self.assertEqual(planner.calls, 1)

    def test_fallback_at_22_does_not_repeat_at_midnight(self):
        planner = CountingPlanner(error=RuntimeError("boom"))
        engine = engine_with(planner)
        enter_night(engine, director_done=None)
        engine.tick()  # provider raises -> fallback report, still done
        self.assertEqual(planner.calls, 1)
        self.assertEqual(engine.world.daily_orchestration_day, 1)
        self.assertIn("night plan provider error", engine.world.night_director_report["reasoning_summary"])
        engine.tick()
        engine.tick()
        engine.tick()
        self.assertEqual(planner.calls, 1)

    def test_legacy_director_is_gated_after_night_orchestration(self):
        engine = engine_with(CountingPlanner())
        enter_night(engine, director_done=None)
        engine.tick()
        before = engine.last_director_report
        # Even calling the compatibility method directly must not orchestrate
        # again for a day already planned by the night director.
        engine._maybe_daily_director()
        self.assertEqual(engine.last_director_report, before)


class NightOrchestrationPersistenceTests(unittest.TestCase):
    def test_save_load_does_not_repeat_orchestration(self):
        planner = CountingPlanner()
        engine = engine_with(planner)
        enter_night(engine, director_done=None)
        engine.tick()
        self.assertEqual(planner.calls, 1)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily.json"
            save_world(engine.world, path)
            loaded = load_world(path)

        self.assertEqual(loaded.daily_orchestration_day, 1)
        replay = OpenWorldEngine()
        replay.director.api_key = ""
        replay.director.provider = planner
        replay.adopt_world(loaded)
        replay.tick()  # still the same night -> must not orchestrate again
        self.assertEqual(planner.calls, 1)


class NextDayBehaviourTests(unittest.TestCase):
    def test_next_day_goals_resume(self):
        engine = engine_with(CountingPlanner())
        ren = engine.world.get_npc("npc_2")
        add_goal(engine, "npc_2", LEAD)
        enter_night(engine, at=23 * 3600, director_done=0)
        engine.tick()  # 24:00 -> day
        self.assertEqual(engine.world.phase, "day")
        engine.tick()  # day planner resumes
        self.assertEqual(ren.active_goal, LEAD)
        self.assertIn(LEAD, ren.goals)
        self.assertEqual(ren.goal_states[LEAD]["status"], "active")

    def test_next_day_due_story_event_runs(self):
        engine = engine_with(CountingPlanner())
        enter_night(engine, director_done=0)
        ivy = engine.world.get_npc("npc_4")
        engine.world.story_timeline.add_event({
            "event_id": "due_next_day",
            "trigger_at": float(NIGHT_START),
            "source": "scenario",
            "effects": [{"type": "set_belief", "npc_id": "npc_4",
                         "key": "due_marker", "value": 0.5}],
        })
        engine.tick()  # 23:00, still night: no story
        self.assertNotIn("due_marker", ivy.beliefs)
        engine.tick()  # 24:00 -> day
        engine.tick()  # 01:00 next day: due event runs
        self.assertEqual(ivy.beliefs.get("due_marker"), 0.5)


if __name__ == "__main__":
    unittest.main()
