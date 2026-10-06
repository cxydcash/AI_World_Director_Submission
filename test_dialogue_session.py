"""Step 7.7 tests: Dialogue Session isolation + deferred StoryEvents."""

import tempfile
import unittest
from pathlib import Path

from open_world import OpenWorldEngine
from persistence import load_world, save_world


def runtime_engine():
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    # These tests exercise manual session lifecycle and deferred events; the
    # Step 9 duration-based auto-end is covered in test_npc_reliability.py.
    engine.dialogue_session_max_seconds = None
    # These tests measure world time in hours while a session is open, so
    # disable the Step 12.2 dialogue time-slowdown (covered elsewhere).
    engine.dialogue_time_scale = 1.0
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_1").position_override = [69.0, 70.0]
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    engine.world.get_npc("npc_3").position_override = [68.0, 70.0]
    return engine


def advance(engine, hours):
    engine.tick_seconds = 3600.0
    for _ in range(int(hours)):
        # Step 8 gave NPCs an autonomous planner; these tests are about the
        # manual dialogue-session lifecycle, so keep the partner next to the
        # player instead of letting the planner walk it out of conversation.
        engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        engine.tick()


def statuses(engine):
    return {entry["id"]: entry["status"] for entry in engine.world.story_timeline}


class DialogueSessionTests(unittest.TestCase):
    def test_talk_starts_a_session_for_that_npc(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你好，Ren。")
        session = engine._dialogue_session_view()
        self.assertTrue(session["active"])
        self.assertEqual(session["npc_id"], "npc_2")
        self.assertEqual(session["npc_name"], "Ren")

    def test_three_turns_stay_with_the_same_npc(self):
        engine = runtime_engine()
        for message in ["第一句", "第二句", "第三句"]:
            engine.talk("npc_2", message)
            self.assertEqual(engine._dialogue_session_view()["npc_id"], "npc_2")
            self.assertTrue(engine._dialogue_session_view()["active"])

    def test_ren_story_event_is_due_but_deferred(self):
        engine = runtime_engine()
        engine.talk("npc_2", "我在查账本。")
        advance(engine, 110)   # past ren_lost trigger_at = 104h
        self.assertEqual(statuses(engine)["ren_lost"], "deferred")
        event = engine.world.story_timeline.get_event("ren_lost")
        self.assertIn("dialogue session", event["metadata"]["defer_reason"])

    def test_session_protects_the_talking_npc(self):
        engine = runtime_engine()
        engine.talk("npc_2", "我在查账本。")
        advance(engine, 110)
        self.assertTrue(engine.world.characters_state["npc_2"]["alive"])
        self.assertTrue(engine._dialogue_session_view()["active"])
        self.assertEqual(engine._dialogue_session_view()["npc_id"], "npc_2")

    def test_other_npc_story_events_still_run(self):
        engine = runtime_engine()
        engine.talk("npc_2", "我在查账本。")
        advance(engine, 160)   # past external_audit (128h) and mayor_flees (152h)
        state = statuses(engine)
        self.assertEqual(state["external_audit"], "triggered")
        self.assertEqual(state["mayor_flees"], "triggered")
        self.assertEqual(state["ren_lost"], "deferred")

    def test_end_dialogue_session_clears_the_session(self):
        engine = runtime_engine()
        engine.talk("npc_2", "我在查账本。")
        result = engine.end_dialogue_session()
        self.assertTrue(result["success"])
        self.assertEqual(result["ended_npc_id"], "npc_2")
        session = engine._dialogue_session_view()
        self.assertFalse(session["active"])
        self.assertIsNone(session["npc_id"])

    def test_deferred_event_triggers_after_the_session_ends(self):
        engine = runtime_engine()
        engine.talk("npc_2", "我在查账本。")
        advance(engine, 110)
        self.assertEqual(statuses(engine)["ren_lost"], "deferred")
        engine.end_dialogue_session()
        self.assertEqual(statuses(engine)["ren_lost"], "triggered")
        self.assertFalse(engine.world.characters_state["npc_2"]["alive"])

    def test_switching_npc_releases_the_previous_session(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你好，Ren。")
        engine.talk("npc_1", "你好，村长。")
        session = engine._dialogue_session_view()
        self.assertTrue(session["active"])
        self.assertEqual(session["npc_id"], "npc_1")

    def test_dialogue_history_is_grouped_by_npc(self):
        engine = runtime_engine()
        engine.talk("npc_2", "Ren 的问题")
        engine.talk("npc_1", "村长的问题")
        engine.talk("npc_3", "Mira 的问题")
        grouped = engine._dialogue_history_by_npc()
        self.assertTrue(grouped["npc_2"])
        self.assertTrue(grouped["npc_1"])
        self.assertTrue(grouped["npc_3"])
        self.assertEqual({entry["npc"] for entry in grouped["npc_2"]}, {"Ren"})
        self.assertEqual({entry["npc"] for entry in grouped["npc_1"]}, {"Mayor Aldric"})
        self.assertEqual({entry["npc"] for entry in grouped["npc_3"]}, {"Mira"})

    def test_ren_history_does_not_appear_in_mayor_history(self):
        engine = runtime_engine()
        engine.talk("npc_2", "只有 Ren 听过这句话。")
        engine.talk("npc_1", "村长自己的话。")
        grouped = engine._dialogue_history_by_npc()
        mayor_players = {entry["player"] for entry in grouped["npc_1"]}
        ren_players = {entry["player"] for entry in grouped["npc_2"]}
        self.assertIn("只有 Ren 听过这句话。", ren_players)
        self.assertNotIn("只有 Ren 听过这句话。", mayor_players)

    def test_mayor_history_does_not_appear_in_mira_history(self):
        engine = runtime_engine()
        engine.talk("npc_1", "只有村长听过这句话。")
        engine.talk("npc_3", "Mira 自己的话。")
        grouped = engine._dialogue_history_by_npc()
        mira_players = {entry["player"] for entry in grouped["npc_3"]}
        mayor_players = {entry["player"] for entry in grouped["npc_1"]}
        self.assertIn("只有村长听过这句话。", mayor_players)
        self.assertNotIn("只有村长听过这句话。", mira_players)

    def test_session_survives_save_and_load(self):
        engine = runtime_engine()
        engine.talk("npc_2", "我在查账本。")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dialogue_session.json"
            save_world(engine.world, path)
            loaded = load_world(path)
        self.assertTrue(loaded.dialogue_session["active"])
        self.assertEqual(loaded.dialogue_session["npc_id"], "npc_2")

        resumed = runtime_engine()
        resumed.adopt_world(loaded)
        advance(resumed, 110)
        state = {entry["id"]: entry["status"] for entry in resumed.world.story_timeline}
        self.assertEqual(state["ren_lost"], "deferred")
        self.assertTrue(resumed.world.characters_state["npc_2"]["alive"])

    def test_snapshot_exposes_session_and_grouped_history(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你好。")
        open_world = engine.snapshot()["open_world"]
        self.assertIn("dialogue_session", open_world)
        self.assertIn("dialogue_history_by_npc", open_world)
        self.assertTrue(open_world["dialogue_session"]["active"])
        self.assertEqual(open_world["dialogue_session"]["npc_id"], "npc_2")
        self.assertIn("npc_2", open_world["dialogue_history_by_npc"])


if __name__ == "__main__":
    unittest.main()


