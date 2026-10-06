"""Tests for Interactive Autonomous World Simulation."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from interactive_simulation import InteractiveSimulationEngine
from visualization.app import INTERACTIVE_ENGINE, VisualizerHandler


class InteractiveSimulationTests(unittest.TestCase):
    def test_player_move_uses_safe_action_pipeline(self):
        engine = InteractiveSimulationEngine()
        before = engine.snapshot()["player"]["position"]

        result = engine.move("w")

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], {"type": "move", "x": 50.0, "y": 52.0})
        self.assertEqual(
            result["step"]["snapshot"]["player"]["position"],
            [50.0, 52.0],
        )
        self.assertNotEqual(before, result["step"]["snapshot"]["player"]["position"])

    def test_thought_input_pauses_world_time(self):
        engine = InteractiveSimulationEngine()
        engine.start(interval=0.01)
        deadline = time.time() + 1.0
        while engine.snapshot()["simulation"]["tick"] < 1 and time.time() < deadline:
            time.sleep(0.01)

        result = engine.analyze_thought("我想去森林寻找Ren")
        self.assertTrue(result["success"])
        paused_time = engine.snapshot()["time"]
        paused_tick = engine.snapshot()["simulation"]["tick"]
        time.sleep(0.05)

        current = engine.snapshot()
        self.assertEqual(current["time"], paused_time)
        self.assertEqual(current["simulation"]["tick"], paused_tick)
        self.assertTrue(current["interactive"]["input_paused"])

    def test_confirmed_candidate_executes_and_changes_world(self):
        engine = InteractiveSimulationEngine()
        analysis = engine.analyze_thought("我想去森林寻找Ren")
        candidate = next(
            item for item in analysis["candidates"] if item["action"]["type"] == "move"
        )

        result = engine.execute_candidate(candidate["id"])

        self.assertTrue(result["success"])
        self.assertEqual(result["step"]["snapshot"]["player"]["position"], [40.0, 40.0])
        self.assertFalse(result["step"]["snapshot"]["interactive"]["input_paused"])

    def test_dialogue_uses_npc_memory_and_belief(self):
        engine = InteractiveSimulationEngine()
        move_result = engine.execute_action({"type": "move", "x": 41.0, "y": 40.0})
        self.assertTrue(move_result["success"])

        ren = engine.world.get_npc("npc_2")
        ren.add_memory(
            {
                "time": engine.world.time,
                "source": "test",
                "content": "I heard something strange in the forest.",
            }
        )
        ren.set_belief("player_has_item_1", 1.0)

        result = engine.talk("npc_2", "Why are you here? I want to understand what happened.")

        self.assertTrue(result["success"])
        self.assertIn("heard something strange", result["reply"])
        self.assertIn("player has item 1", result["reply"])
        self.assertTrue(result["step"]["snapshot"]["interactive"]["dialogue_log"])

    def test_invalid_player_action_is_rejected_without_mutation(self):
        engine = InteractiveSimulationEngine()
        before = engine.snapshot()["player"]["position"]

        result = engine.execute_action({"type": "move", "x": 999, "y": 0})

        self.assertFalse(result["success"])
        self.assertEqual(engine.snapshot()["player"]["position"], before)

    def test_world_resumes_after_confirmed_action(self):
        engine = InteractiveSimulationEngine()
        engine.start(interval=0.01)
        deadline = time.time() + 1.0
        while engine.snapshot()["simulation"]["tick"] < 1 and time.time() < deadline:
            time.sleep(0.01)

        analysis = engine.analyze_thought("我想去森林")
        paused_tick = engine.snapshot()["simulation"]["tick"]
        candidate = next(item for item in analysis["candidates"] if item["action"]["type"] == "move")
        engine.execute_candidate(candidate["id"])

        deadline = time.time() + 1.0
        while engine.snapshot()["simulation"]["tick"] <= paused_tick and time.time() < deadline:
            time.sleep(0.01)
        engine.pause()
        self.assertGreater(engine.snapshot()["simulation"]["tick"], paused_tick)


class InteractiveHTTPTests(unittest.TestCase):
    def test_interactive_http_endpoints(self):
        INTERACTIVE_ENGINE.pause()
        INTERACTIVE_ENGINE.reset()
        server = ThreadingHTTPServer(("127.0.0.1", 0), VisualizerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def post(path, payload=None, expected=200):
            data = None if payload is None else json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                f"{base}{path}",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status, expected)
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, expected)
                return json.loads(exc.read().decode("utf-8"))

        try:
            base = f"http://127.0.0.1:{server.server_port}"
            reset_state = post("/api/interactive/reset")
            self.assertEqual(reset_state["snapshot"]["time"], 10800.0)

            moved = post("/api/interactive/move", {"direction": "d"})
            self.assertTrue(moved["success"])
            self.assertEqual(moved["action"]["x"], 52.0)

            analyzed = post(
                "/api/interactive/analyze",
                {"input": "我想去森林寻找Ren"},
            )
            self.assertTrue(analyzed["success"])
            candidate = analyzed["candidates"][0]
            executed = post(
                "/api/interactive/execute",
                {"candidate_id": candidate["id"]},
            )
            self.assertTrue(executed["success"])

            rejected = post(
                "/api/interactive/action",
                {"action": {"type": "move", "x": 999, "y": 0}},
                expected=400,
            )
            self.assertFalse(rejected["success"])
        finally:
            INTERACTIVE_ENGINE.pause()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
