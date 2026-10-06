"""Tests for the corruption-conflict open world and its HTTP API."""

import json
import threading
import unittest
import tempfile
from pathlib import Path
import urllib.request
from http.server import ThreadingHTTPServer

from open_world import OpenWorldEngine
from persistence import load_world, save_world
from visualization.app import OPEN_WORLD_ENGINE, VisualizerHandler


class OpenWorldTests(unittest.TestCase):
    def test_open_world_starts_with_hidden_conflict(self):
        engine = OpenWorldEngine()
        snapshot = engine.snapshot()
        timeline_ids = [entry["id"] for entry in snapshot["timeline"]]
        names = [npc["name"] for npc in snapshot["npcs"]]

        self.assertIn("ren_discovers_ledger", timeline_ids)
        self.assertIn("mayor_flees", timeline_ids)
        self.assertIn("Mayor Aldric", names)
        self.assertIn("Ren", names)
        self.assertIn("Ivy", names)
        self.assertEqual(snapshot["open_world"]["day"], 1)
        self.assertEqual(snapshot["environment"]["weather"]["condition"], "Sunny")

    def test_unintervened_timeline_harms_ren_and_frees_mayor(self):
        engine = OpenWorldEngine()
        for _ in range(21):
            engine.world.advance_time(8 * 3600)

        ren = engine.world.characters_state["npc_2"]
        mayor = engine.world.characters_state["npc_1"]
        statuses = {entry["id"]: entry["status"] for entry in engine.world.story_timeline}
        self.assertEqual(statuses["ren_lost"], "triggered")
        self.assertEqual(statuses["mayor_flees"], "triggered")
        self.assertFalse(ren["alive"])
        self.assertTrue(mayor["fled"])

    def test_player_protection_cancels_ren_lost(self):
        engine = OpenWorldEngine()
        engine.world.characters_state["npc_2"]["protected"] = True
        for _ in range(21):
            engine.world.advance_time(8 * 3600)

        ren = engine.world.characters_state["npc_2"]
        statuses = {entry["id"]: entry["status"] for entry in engine.world.story_timeline}
        self.assertTrue(ren["alive"])
        self.assertEqual(statuses["ren_lost"], "cancelled")

    def test_wasd_movement_uses_safe_action_pipeline(self):
        engine = OpenWorldEngine()
        result = engine.move("d")
        self.assertTrue(result["success"])
        self.assertEqual(result["step"]["snapshot"]["player"]["position"], [55.0, 50.0])

    def test_thought_records_player_memory(self):
        engine = OpenWorldEngine()
        result = engine.analyze_thought("I want to investigate the mayor's records.")
        self.assertTrue(result["success"])
        memory = engine.snapshot()["player"]["memory"]
        self.assertTrue(any(entry["type"] == "thought" for entry in memory))
        self.assertTrue(engine.snapshot()["open_world"]["input_paused"])

    def test_weather_is_stateful_not_random(self):
        engine = OpenWorldEngine()
        engine.tick_seconds = 6 * 3600
        observed = []
        for _ in range(5):
            engine.tick()
            observed.append(engine.world.environment_state["weather"]["condition"])
        self.assertIn("Rain", observed)



    def test_player_discovers_ledger_changes_mayor(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 22.0, 22.0
        result = engine.take_ledger()
        self.assertTrue(result["success"])
        mayor = engine.world.get_npc("npc_1")
        self.assertEqual(mayor.beliefs["ledger_missing"], 1.0)
        self.assertIn("recover ledger", mayor.goals)

    def test_dialogue_enters_npc_memory(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 71.0, 72.0
        engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
        result = engine.talk("npc_2", "I believe the mayor altered the ledger.")
        self.assertTrue(result["success"])
        ren = engine.world.get_npc("npc_2")
        self.assertTrue(any(entry.get("content") == "I believe the mayor altered the ledger." for entry in ren.memory))

    def test_player_action_creates_evidence(self):
        engine = OpenWorldEngine()
        result = engine.execute_action({
            "type": "create_evidence",
            "evidence_type": "footprint",
            "location": [40.0, 40.0],
            "description": "boot prints outside the office",
        })
        self.assertTrue(result["success"])
        self.assertTrue(engine.world.evidence)

    def test_ai_director_modifies_timeline(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
        result = engine.protect_ren()
        self.assertTrue(result["success"])
        statuses = {entry["id"]: entry["status"] for entry in engine.world.story_timeline}
        self.assertEqual(statuses["ren_lost"], "cancelled")
        self.assertEqual(statuses["player_saved_Ren"], "triggered")
        self.assertIn("village_investigation", statuses)



    def test_mayor_house_investigation_generates_contextual_actions(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        result = engine.analyze_thought("调查这里")
        labels = [item["label"] for item in result["candidates"]]
        self.assertIn("Search the desk", labels)
        self.assertIn("Inspect the bookshelf", labels)
        self.assertIn("Look for hidden documents", labels)

    def test_nearby_objects_are_visible_in_snapshot(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        snapshot = engine.snapshot()
        self.assertTrue(snapshot["observation"]["objects"])
        object_ids = [obj["id"] for obj in snapshot["objects"]]
        self.assertIn("mayor_desk", object_ids)

    def test_search_desk_changes_mayor_suspicion(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        result = engine.search_object("mayor_desk")
        self.assertTrue(result["success"])
        mayor = engine.world.get_npc("npc_1")
        self.assertIn("player_searching_office", mayor.beliefs)

    def test_daily_report_contains_ai_explanation(self):
        engine = OpenWorldEngine()
        engine.world.characters_state["npc_2"]["protected"] = True
        # Step 12.5: exercise the legacy compatibility entry directly.
        engine._maybe_daily_director()
        report = engine.last_director_report
        self.assertIsNotNone(report)
        for key in ("observation", "evidence", "npc_belief", "reasoning", "generated_event", "forecast"):
            self.assertIn(key, report)
        self.assertTrue(report["forecast"])



    def test_items_are_localized_to_chinese(self):
        engine = OpenWorldEngine()
        items = {item["id"]: item for item in engine.snapshot()["items"]}
        self.assertEqual(items["item_ledger"]["label"], "账本")
        self.assertEqual(items["item_funds"]["label"], "钱袋")

    def test_player_story_log_is_chronological(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
        engine.talk("npc_2", "你发现了什么？")
        engine.protect_ren()
        log = engine.snapshot()["event_log"]
        self.assertTrue(log)
        sources = {entry["source"] for entry in log}
        self.assertIn("player", sources)



    def test_daily_director_always_generates_event(self):
        engine = OpenWorldEngine()
        # Step 12.5: daily orchestration runs in the 22:00-24:00 Night
        # Director phase, so the legacy entry point is called directly here.
        engine._maybe_daily_director()
        report = engine.last_director_report
        self.assertIsNotNone(report)
        self.assertIsNotNone(report["event"])

    def test_auditor_starts_at_map_edge_and_moves_in(self):
        engine = OpenWorldEngine()
        ivy = engine.world.get_npc("npc_4")
        self.assertEqual(engine.world.npc_position("npc_4"), (5.0, 95.0))
        ivy.add_goal("investigate mayor")
        engine._update_npc_positions_by_goals()
        x, y = engine.world.npc_position("npc_4")
        self.assertGreater(x, 5.0)
        self.assertLess(y, 95.0)

    def test_break_lock_produces_feedback(self):
        engine = OpenWorldEngine()
        mayor = engine.world.get_npc("npc_1")
        mayor.position_override = [90.0, 90.0]
        result = engine.break_object("mayor_desk_lock")
        self.assertTrue(result["success"])
        self.assertIn("砸开", result["message"])
        self.assertTrue(engine.world.evidence)

    def test_daily_report_contains_narrative(self):
        engine = OpenWorldEngine()
        engine.world.characters_state["npc_2"]["protected"] = True
        # Step 12.5: exercise the legacy compatibility entry directly.
        engine._maybe_daily_director()
        report = engine.last_director_report
        self.assertIn("narrative", report)
        self.assertTrue(report["narrative"])



    def test_thought_does_not_jump_to_hidden_ledger(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        result = engine.analyze_thought("我想去检查书架上的账本")
        labels = [item["label"] for item in result["candidates"]]
        self.assertIn("Inspect the bookshelf", labels)
        self.assertNotIn("拿走隐藏账本", labels)
        self.assertNotIn("Take the hidden ledger", labels)

    def test_absent_mayor_does_not_lower_trust(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        mayor = engine.world.get_npc("npc_1")
        mayor.position_override = [90.0, 90.0]
        before = mayor.relationships.get("player", 0.5)
        engine.search_object("mayor_desk")
        self.assertEqual(mayor.relationships.get("player", 0.5), before)

    def test_story_end_spawns_new_npc_goals(self):
        engine = OpenWorldEngine()
        engine.world.characters_state["npc_2"]["alive"] = False
        engine.world.story_timeline = [
            {"id": "ren_lost", "trigger_time": 0, "status": "triggered"},
            {"id": "mayor_flees", "trigger_time": 0, "status": "triggered"},
        ]
        event = engine.director.generate_event(engine.world, context="daily_director")
        self.assertIsNotNone(event)
        goal_types = {effect.get("type") for effect in event["effects"]}
        self.assertIn("add_goal", goal_types)



    def test_break_lock_then_open_drawer_then_take_ledger(self):
        engine = OpenWorldEngine()
        mayor = engine.world.get_npc("npc_1")
        mayor.position_override = [90.0, 90.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine.world.item_ledger = engine.world.get_item("item_ledger")

        self.assertTrue(engine.break_object("mayor_desk_lock")["success"])
        analyzed = engine.analyze_thought("打开抽屉")
        self.assertTrue(any(item["label"] == "打开抽屉" for item in analyzed["candidates"]))

        opened = engine.open_drawer("mayor_hidden_drawer")
        self.assertTrue(opened["success"])
        self.assertTrue(any(entry.get("type") == "hidden_ledger" for entry in engine.world.evidence))

        taken = engine.take_ledger()
        self.assertTrue(taken["success"])
        self.assertEqual(engine.world.get_item("item_ledger").holder, "player")



    def test_search_desk_reports_what_was_found(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        mayor = engine.world.get_npc("npc_1")
        mayor.position_override = [90.0, 90.0]
        result = engine.search_object("mayor_desk")
        self.assertTrue(result["success"])
        self.assertIn("发现", result["message"])

    def test_ivy_is_external_auditor(self):
        engine = OpenWorldEngine()
        ivy = engine.world.get_npc("npc_4")
        self.assertEqual(ivy.role, "external_auditor")

    def test_dead_ren_disappears_from_snapshot(self):
        engine = OpenWorldEngine()
        engine.world.characters_state["npc_2"]["alive"] = False
        snapshot = engine.snapshot()
        self.assertFalse(any(npc["id"] == "npc_2" for npc in snapshot["npcs"]))
        self.assertFalse(any(npc["id"] == "npc_2" for npc in snapshot["observation"]["npcs"]))



    def test_follow_footprints_reveals_clue(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 70.0, 70.0
        result = engine.follow_object("footprints")
        self.assertTrue(result["success"])
        self.assertTrue(any("脚印通向办公室" in clue["text"] for clue in engine.snapshot()["open_world"]["clues"]))
        self.assertTrue(any(entry.get("type") == "ren_hiding" for entry in engine.world.evidence))

    def test_break_lock_triggers_immediate_director_feed(self):
        engine = OpenWorldEngine()
        mayor = engine.world.get_npc("npc_1")
        mayor.position_override = [90.0, 90.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine.break_object("mayor_desk_lock")
        feed = engine.snapshot()["open_world"]["director_feed"]
        self.assertTrue(feed)

    def test_current_objective_changes_after_finding_drawer(self):
        engine = OpenWorldEngine()
        objective = engine.snapshot()["open_world"]["objective"]
        self.assertTrue(objective)
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine.object_state["drawer_open"] = True
        engine.object_state["drawer_searched"] = False
        objective2 = engine.snapshot()["open_world"]["objective"]
        self.assertIn("抽屉已经打开", objective2)



    def test_give_ledger_to_npc(self):
        engine = OpenWorldEngine()
        engine.world.player.x, engine.world.player.y = 22.0, 22.0
        engine.take_ledger()
        engine.world.get_npc("npc_2").position_override = [22.0, 22.0]
        result = engine.analyze_thought("把账本交给Ren")
        give = next((item for item in result["candidates"] if item["action"].get("type") == "give_item"), None)
        self.assertIsNotNone(give)
        executed = engine.execute_candidate(give["id"])
        self.assertTrue(executed["success"])
        self.assertEqual(engine.world.get_item("item_ledger").holder, "npc_2")

    def test_primary_story_end_starts_new_phase(self):
        engine = OpenWorldEngine()
        engine.world.characters_state["npc_2"]["alive"] = False
        engine.world.characters_state["npc_1"]["fled"] = True
        for entry in engine.world.story_timeline:
            if entry["id"] in {"ren_discovers_ledger", "ren_seeks_evidence", "mayor_notices", "ren_pursued", "ren_lost", "external_audit", "mayor_flees"}:
                entry["status"] = "triggered"
        engine._maybe_continue_story()
        self.assertEqual(engine.story_phase, 1)
        ids = {entry["id"] for entry in engine.world.story_timeline}
        self.assertTrue({"mira_seeks_truth", "village_manhunt", "new_arrival"} <= ids)



    def test_ren_follows_player_after_request(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        result = engine.npc_follow_player("npc_2")
        self.assertTrue(result["success"])
        before = engine.world.npc_position("npc_2")
        engine.world.player.x, engine.world.player.y = 30.0, 30.0
        engine._update_npc_positions_by_goals()
        after = engine.world.npc_position("npc_2")
        self.assertNotEqual(before, after)

    def test_ren_lost_forces_new_phase(self):
        engine = OpenWorldEngine()
        engine.world.characters_state["npc_2"]["alive"] = False
        for entry in engine.world.story_timeline:
            if entry["id"] == "ren_lost":
                entry["status"] = "triggered"
        engine._maybe_continue_story()
        self.assertEqual(engine.story_phase, 1)
        ids = {entry["id"] for entry in engine.world.story_timeline}
        self.assertIn("mira_seeks_truth", ids)
        self.assertIn("new_arrival", ids)



    def test_arbitrary_thought_generates_free_action(self):
        engine = OpenWorldEngine()
        result = engine.analyze_thought("我想放风筝")
        self.assertTrue(result["success"])
        self.assertTrue(any(item["action"].get("type") == "free_action" for item in result["candidates"]))

    def test_free_action_has_feedback_and_memory(self):
        engine = OpenWorldEngine()
        result = engine.execute_free_action({"type": "free_action", "verb": "fly_kite", "description": "我想放风筝"})
        self.assertTrue(result["success"])
        self.assertTrue(result["message"])
        self.assertTrue(any(entry.get("type") == "free_action" for entry in engine.world.player.memory))



    def test_request_follow_creates_follow_state(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        result = engine.talk("npc_2", "你可以跟着我吗？")
        self.assertTrue(result["success"])
        self.assertEqual(result["intent"], "request_follow")
        self.assertTrue(engine.world.characters_state["npc_2"]["following_player"])
        self.assertEqual(engine.world.characters_state["npc_2"]["follow_target"], "player")

    def test_stop_follow(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine.talk("npc_2", "你可以跟着我吗？")
        result = engine.talk("npc_2", "你不用跟着我了。")
        self.assertTrue(result["success"])
        self.assertEqual(result["intent"], "stop_follow")
        self.assertFalse(engine.world.characters_state["npc_2"]["following_player"])

    def test_follow_persists_after_save_load(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine.talk("npc_2", "你可以跟着我吗？")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "follow_state.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        self.assertTrue(loaded.characters_state["npc_2"]["following_player"])

    def test_dialogue_memory_records_follow_request(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine.talk("npc_2", "你可以跟着我吗？")
        self.assertTrue(any("follow" in str(entry).lower() for entry in ren.memory))
        self.assertTrue(any("跟着" in str(entry) or "follow" in str(entry).lower() for entry in engine.world.player.memory))



    def test_following_npc_moves_toward_player(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 50.0]
        engine.world.player.x, engine.world.player.y = 50.0, 50.0
        engine._set_follow_state("npc_2", True)
        before = engine.world.distance_between(engine.world.npc_position("npc_2"), engine.world.player)
        for _ in range(3):
            engine._update_npc_positions_by_goals()
        after = engine.world.distance_between(engine.world.npc_position("npc_2"), engine.world.player)
        self.assertLess(after, before)

    def test_following_persists_after_player_moves(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 50.0]
        engine.world.player.x, engine.world.player.y = 50.0, 50.0
        engine._set_follow_state("npc_2", True)
        engine.world.player.x = 60.0
        before = engine.world.npc_position("npc_2")
        engine._update_npc_positions_by_goals()
        after = engine.world.npc_position("npc_2")
        self.assertNotEqual(before, after)
        self.assertTrue(engine.world.characters_state["npc_2"]["following_player"])

    def test_follow_does_not_teleport(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [0.0, 50.0]
        engine.world.player.x, engine.world.player.y = 50.0, 50.0
        engine._set_follow_state("npc_2", True)
        before = engine.world.npc_position("npc_2")
        engine._update_npc_positions_by_goals()
        after = engine.world.npc_position("npc_2")
        moved = engine.world.distance_between(before, after)
        self.assertLessEqual(moved, 2.0)



    def test_dialogue_follow_uses_event_dsl(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine.talk("npc_2", "你可以跟着我吗？")
        latest = engine.world.event_history[-1]
        self.assertEqual(latest["effects"][0]["type"], "set_follow_target")
        self.assertEqual(latest["effects"][0]["follow_target"], "player")

    def test_stop_follow_uses_event_dsl(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 20.0]
        engine.world.player.x, engine.world.player.y = 20.0, 20.0
        engine.talk("npc_2", "你可以跟着我吗？")
        engine.talk("npc_2", "你不用跟着我了。")
        latest = engine.world.event_history[-1]
        self.assertEqual(latest["effects"][0]["type"], "set_follow_target")
        self.assertIsNone(latest["effects"][0]["follow_target"])


class OpenWorldHTTPTests(unittest.TestCase):
    def test_open_world_http_endpoints(self):
        OPEN_WORLD_ENGINE.pause()
        OPEN_WORLD_ENGINE.reset()
        server = ThreadingHTTPServer(("127.0.0.1", 0), VisualizerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def post(path, payload=None):
            data = None if payload is None else json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                f"{base}{path}",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            base = f"http://127.0.0.1:{server.server_port}"
            reset_state = post("/api/open-world/reset")
            self.assertEqual(reset_state["snapshot"]["open_world"]["day"], 1)

            moved = post("/api/open-world/move", {"direction": "w"})
            self.assertTrue(moved["success"])
            self.assertEqual(moved["step"]["snapshot"]["player"]["position"][1], 55.0)

            analyzed = post(
                "/api/open-world/analyze",
                {"input": "我想调查村长的账目问题"},
            )
            self.assertTrue(analyzed["success"])
            self.assertTrue(analyzed["candidates"])
        finally:
            OPEN_WORLD_ENGINE.pause()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
