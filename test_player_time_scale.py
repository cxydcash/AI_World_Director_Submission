# -*- coding: utf-8 -*-
"""Step 12.7 tests: player time scale + WASD keys.

A few WASD presses must not burn hours. Interaction times stay as before.
"""

import unittest
from pathlib import Path

from action_time import ACTION_TIME_SECONDS, MOVE_SPEED_PER_HOUR, move_time_cost
from entities import Player
from open_world import OpenWorldEngine
from world import WorldState


PROJECT_ROOT = Path(__file__).resolve().parent


class MovementScaleTests(unittest.TestCase):
    def test_speed_is_one_unit_per_minute(self):
        self.assertEqual(MOVE_SPEED_PER_HOUR, 60.0)
        self.assertAlmostEqual(move_time_cost(1.0), 60.0)

    def test_single_step_is_minutes_not_hours(self):
        cost = move_time_cost(5.0)          # PlayerController move_step
        self.assertGreaterEqual(cost, 60.0)
        self.assertLessEqual(cost, 10 * 60.0)

    def test_tens_of_units_is_tens_of_minutes(self):
        cost = move_time_cost(50.0)
        self.assertGreaterEqual(cost, 20 * 60.0)
        self.assertLessEqual(cost, 70 * 60.0)

    def test_far_area_is_about_an_hour(self):
        cost = move_time_cost(60.0)
        self.assertAlmostEqual(cost, 3600.0, delta=300.0)

    def test_world_move_advances_minutes(self):
        world = WorldState(player=Player(50.0, 50.0))
        before = world.time
        world.move(55.0, 50.0)
        cost = world.time - before
        self.assertGreater(cost, 0.0)
        self.assertLess(cost, 3600.0)

    def test_engine_move_advances_minutes(self):
        engine = OpenWorldEngine()
        engine.director.api_key = ""
        engine.director.provider = None
        before = engine.world.time
        result = engine.move("w")
        self.assertTrue(result["success"])
        cost = engine.world.time - before
        self.assertGreater(cost, 0.0)
        self.assertLess(cost, 3600.0)


class InteractionScaleTests(unittest.TestCase):
    def test_interaction_times_unchanged(self):
        expected = {
            "talk": 10 * 60,
            "observe": 10 * 60,
            "investigate": 10 * 60,
            "pick": 1 * 60,
            "drop": 1 * 60,
            "buy": 1 * 60,
            "give_item": 1 * 60,
            "create_rumor": 10 * 60,
            "work": 8 * 3600,
            "sleep": 8 * 3600,
        }
        for action, seconds in expected.items():
            self.assertEqual(ACTION_TIME_SECONDS.get(action), seconds, action)


class WasdGuardTests(unittest.TestCase):
    def test_keydown_ignores_os_repeat(self):
        source = (PROJECT_ROOT / "visualization" / "app.js").read_text(encoding="utf-8")
        self.assertIn("event.repeat", source)
        self.assertIn("按住 WASD", source)


if __name__ == "__main__":
    unittest.main()
