"""Tests for Step 8 baseline, experiment recording, and final demo."""

import json
import tempfile
import unittest
from pathlib import Path

from baseline import BaselineDirector
from director import MockDirector
from event import execute_event
from experiment import (
    default_scenarios,
    run_comparison,
    run_experiment,
    run_scenario,
    save_experiment_results,
    load_experiment_results,
)
from final_demo import build_demo_scenario, run_final_demo
from world import WorldState


class BaselineTests(unittest.TestCase):
    def test_baseline_generates_valid_executable_event(self):
        scenario = default_scenarios()[0]
        from experiment import build_scenario_world

        world = build_scenario_world(scenario)
        director = BaselineDirector()

        event = director.generate_event(world)
        execute_event(world, event)

        self.assertEqual(director.last_log["source"], "Baseline")
        self.assertEqual(len(world.event_history), 1)

    def test_baseline_is_deterministic_for_same_time(self):
        from experiment import build_scenario_world

        scenario = default_scenarios()[0]
        world = build_scenario_world(scenario)
        director = BaselineDirector()

        first = director.generate_event(world)
        second = director.generate_event(world)

        self.assertEqual(first, second)


class ExperimentTests(unittest.TestCase):
    def test_run_experiment_records_three_scenarios(self):
        results = run_experiment(BaselineDirector())

        self.assertEqual(len(results), 3)
        for result in results:
            self.assertIn("player_actions", result)
            self.assertIn("observations", result)
            self.assertIn("generated_event", result)
            self.assertIn("world_before", result)
            self.assertIn("world_after", result)

    def test_experiment_results_save_and_load(self):
        results = run_experiment(BaselineDirector())

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "results.json"
            save_experiment_results(results, path)
            loaded = load_experiment_results(path)

        self.assertEqual(loaded, results)

    def test_comparison_runs_same_scenarios_for_both_directors(self):
        comparison = run_comparison(MockDirector())

        self.assertEqual(
            comparison["scenarios"],
            [scenario.name for scenario in default_scenarios()],
        )
        self.assertEqual(len(comparison["baseline"]), 3)
        self.assertEqual(len(comparison["ai"]), 3)

    def test_final_demo_runs_offline_with_mock_director(self):
        summary = run_final_demo(MockDirector())

        self.assertIn("generated_event", summary)
        self.assertIn("world_before", summary)
        self.assertIn("world_after", summary)


if __name__ == "__main__":
    unittest.main()