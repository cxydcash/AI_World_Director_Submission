"""Tests for autonomous Simulation Mode and its HTTP endpoints."""

import json
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from simulation_engine import SIMULATION_START_TIME, SimulationEngine
from visualization.app import SIMULATION_ENGINE, VisualizerHandler


class SimulationEngineTests(unittest.TestCase):
    def test_reset_restores_autonomous_demo_start(self):
        engine = SimulationEngine()
        snapshot = engine.snapshot()
        ren = next(npc for npc in snapshot["npcs"] if npc["id"] == "npc_2")
        mira = next(npc for npc in snapshot["npcs"] if npc["id"] == "npc_1")

        self.assertEqual(snapshot["time"], SIMULATION_START_TIME)
        self.assertEqual(snapshot["simulation"]["tick"], 0)
        self.assertFalse(snapshot["simulation"]["running"])
        self.assertEqual(ren["goals"], ["collect herbs"])
        self.assertEqual(ren["position"], [40.0, 40.0])
        self.assertEqual(mira["goals"], ["wait_home"])

    def test_fixed_demo_sequence_is_state_driven(self):
        engine = SimulationEngine()

        tick1 = engine.tick()
        self.assertIn("Ren enters the forest", tick1["event"]["description"])
        ren = next(npc for npc in tick1["snapshot"]["npcs"] if npc["id"] == "npc_2")
        self.assertEqual(ren["current_action"], "collect herbs")

        tick2 = engine.tick()
        self.assertTrue(tick2["snapshot"]["environment"]["weather"]["rain"])

        tick3 = engine.tick()
        ren_state = engine.snapshot()["npcs"]
        ren = next(npc for npc in ren_state if npc["id"] == "npc_2")
        self.assertFalse(ren["status"]["in_contact"])

        tick4 = engine.tick()
        self.assertIn("ren_missing", tick4["event"]["description"])
        timeline = {entry["id"]: entry["status"] for entry in tick4["snapshot"]["timeline"]}
        self.assertEqual(timeline["ren_missing"], "triggered")

        tick5 = engine.tick()
        self.assertIn("AI Director generated", tick5["event"]["description"])
        mira = next(npc for npc in tick5["snapshot"]["npcs"] if npc["id"] == "npc_1")
        self.assertIn("search Ren", mira["goals"])
        self.assertTrue(tick5["snapshot"]["event_history"])

    def test_world_keeps_evolving_after_director_event(self):
        engine = SimulationEngine()
        for _ in range(5):
            engine.tick()

        tick6 = engine.tick()
        mira = next(npc for npc in tick6["snapshot"]["npcs"] if npc["id"] == "npc_1")
        self.assertIn("searching", mira["current_action"])
        self.assertEqual(len(tick6["snapshot"]["story_event_history"]), 1)

    def test_start_and_pause_control_background_ticks(self):
        engine = SimulationEngine()
        engine.tick_seconds = 0.01
        engine.start(interval=0.01)
        deadline = time.time() + 1.0
        while engine.snapshot()["simulation"]["tick"] < 1 and time.time() < deadline:
            time.sleep(0.01)

        engine.pause()
        paused = engine.snapshot()
        self.assertFalse(paused["simulation"]["running"])
        self.assertGreaterEqual(paused["simulation"]["tick"], 1)

        tick_at_pause = paused["simulation"]["tick"]
        time.sleep(0.04)
        self.assertEqual(engine.snapshot()["simulation"]["tick"], tick_at_pause)


class SimulationHTTPTests(unittest.TestCase):
    def test_simulation_http_endpoints(self):
        SIMULATION_ENGINE.pause()
        SIMULATION_ENGINE.reset()
        server = ThreadingHTTPServer(("127.0.0.1", 0), VisualizerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            request = urllib.request.Request(
                f"{base}/api/simulation/reset", method="POST"
            )
            with urllib.request.urlopen(request) as response:
                reset_state = json.loads(response.read().decode("utf-8"))
            self.assertEqual(reset_state["snapshot"]["simulation"]["tick"], 0)

            request = urllib.request.Request(
                f"{base}/api/simulation/tick", method="POST"
            )
            with urllib.request.urlopen(request) as response:
                tick_state = json.loads(response.read().decode("utf-8"))
            self.assertEqual(tick_state["snapshot"]["simulation"]["tick"], 1)

            with urllib.request.urlopen(f"{base}/api/simulation/state") as response:
                state = json.loads(response.read().decode("utf-8"))
            self.assertIn("simulation", state["snapshot"])

            request = urllib.request.Request(
                f"{base}/api/simulation/start", method="POST"
            )
            with urllib.request.urlopen(request) as response:
                started = json.loads(response.read().decode("utf-8"))
            self.assertTrue(started["snapshot"]["simulation"]["running"])

            deadline = time.time() + 1.0
            while time.time() < deadline:
                with urllib.request.urlopen(f"{base}/api/simulation/state") as response:
                    state = json.loads(response.read().decode("utf-8"))
                if state["snapshot"]["simulation"]["tick"] >= 2:
                    break
                time.sleep(0.01)
            self.assertGreaterEqual(state["snapshot"]["simulation"]["tick"], 2)

            request = urllib.request.Request(
                f"{base}/api/simulation/pause", method="POST"
            )
            with urllib.request.urlopen(request) as response:
                paused = json.loads(response.read().decode("utf-8"))
            self.assertFalse(paused["snapshot"]["simulation"]["running"])
        finally:
            SIMULATION_ENGINE.pause()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
