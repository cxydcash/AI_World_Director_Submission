"""Tests for the visual replay and HTTP API."""

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from demo_replay import EVENTS_FILE, ReplayEngine
from visualization.app import ENGINE, VisualizerHandler


class VisualizationTests(unittest.TestCase):
    def test_replay_contains_key_steps(self):
        engine = ReplayEngine()
        types = [step["event"]["type"] for step in engine.steps]
        self.assertEqual(
            types,
            ["reset", "player_action", "evidence_created", "ai_director", "timeline"],
        )
        evidence_snapshot = engine.steps[2]["snapshot"]
        self.assertTrue(evidence_snapshot["evidence"])
        ren = next(npc for npc in evidence_snapshot["npcs"] if npc["id"] == "npc_2")
        self.assertIn("Ren fled north", ren["beliefs"])
        director_snapshot = engine.steps[3]["snapshot"]
        self.assertTrue(director_snapshot["event_history"])

    def test_invalid_action_does_not_modify_world(self):
        engine = ReplayEngine()
        before = engine.current()["snapshot"]

        result = engine.apply_input("move 200 200")

        after = engine.current()["snapshot"]
        self.assertFalse(result["success"])
        self.assertEqual(after["player"], before["player"])

    def test_move_action_changes_player_position(self):
        engine = ReplayEngine()
        result = engine.apply_input("move 40 40")
        self.assertTrue(result["success"])
        self.assertEqual(result["step"]["snapshot"]["player"]["position"], [40.0, 40.0])

    def test_create_evidence_changes_nearby_npc_belief(self):
        engine = ReplayEngine()
        engine.apply_input("move 40 40")
        result = engine.apply_input("create evidence footprints heading north")
        self.assertTrue(result["success"])
        ren = next(npc for npc in result["step"]["snapshot"]["npcs"] if npc["id"] == "npc_2")
        self.assertTrue(ren["beliefs"])

    def test_director_generates_event_for_interactive_action(self):
        engine = ReplayEngine()
        result = engine.apply_input("move 40 40")
        self.assertTrue(result["success"])
        self.assertTrue(result["director_event"])
        self.assertTrue(result["step"]["snapshot"]["event_history"])
    def test_demo_events_file_is_valid(self):
        self.assertTrue(EVENTS_FILE.exists())
        events = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
        self.assertIsInstance(events, list)
        self.assertGreaterEqual(len(events), 3)

    def test_http_api_state_and_next(self):
        ENGINE.reset()
        server = ThreadingHTTPServer(("127.0.0.1", 0), VisualizerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(f"{base}/api/state") as response:
                state = json.loads(response.read().decode("utf-8"))
            self.assertIn("snapshot", state)
            request = urllib.request.Request(f"{base}/api/next", method="POST")
            with urllib.request.urlopen(request) as response:
                next_state = json.loads(response.read().decode("utf-8"))
            self.assertEqual(next_state["event"]["type"], "player_action")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()