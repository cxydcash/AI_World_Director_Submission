"""Step 9 tests: NPC autonomous reliability (search loop, death, session auto-end)."""

import unittest

from open_world import OpenWorldEngine


def runtime_engine():
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    return engine


def statuses(engine):
    return {entry["id"]: entry["status"] for entry in engine.world.story_timeline}


class NPCReliabilityTests(unittest.TestCase):
    def test_find_item_fails_after_repeated_fruitless_search(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        ren.goals = ["find_item:item_ledger"]   # the item exists but is far away
        engine.tick_seconds = 3600.0
        # Isolate this search-loop test from the preset corruption timeline:
        # its 8h StoryEvent legitimately adds a fresh, equivalent goal.
        from story_timeline import StoryTimeline
        engine.world.story_timeline = StoryTimeline([])
        for _ in range(6):
            engine.tick()
            if ren.goal_states.get("find_item:item_ledger", {}).get("status") == "failed":
                break
        state = ren.goal_states.get("find_item:item_ledger", {})
        self.assertEqual(state.get("status"), "failed", f"goal_states={ren.goal_states}")
        # After failing, it must not keep searching.
        for _ in range(5):
            engine.tick()
            self.assertNotEqual(ren.action_progress.get("action"), "search")

    def test_dead_npc_does_not_act_and_goals_terminate(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        ren.goals = ["find ledger"]
        engine.world.characters_state["npc_2"]["alive"] = False
        before = engine.world.npc_position("npc_2")
        engine.tick_seconds = 3600.0
        for _ in range(3):
            engine.tick()
        self.assertEqual(engine.world.npc_position("npc_2"), before)
        self.assertFalse(ren.current_action)
        self.assertEqual(ren.goal_states.get("find ledger", {}).get("status"), "failed")

    def test_dialogue_session_auto_ends_when_player_is_far(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你好，Ren。")
        self.assertTrue(engine._dialogue_session_view()["active"])
        engine.world.player.x, engine.world.player.y = 0.0, 0.0
        engine.tick_seconds = 60.0
        engine.tick()
        self.assertFalse(engine._dialogue_session_view()["active"])

    def test_dialogue_session_auto_ends_after_timeout(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你好，Ren。")
        self.assertTrue(engine._dialogue_session_view()["active"])
        engine.tick_seconds = 3600.0
        engine.dialogue_time_scale = 1.0     # 这个用例测的是超时机制本身
        for _ in range(3):
            engine.world.player.x, engine.world.player.y = engine.world.npc_position("npc_2")
            engine.tick()
        self.assertFalse(engine._dialogue_session_view()["active"])

    def test_deferred_event_resumes_after_session_auto_end(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你好，Ren。")
        engine.world.story_timeline.add_event({
            "event_id": "resume_event",
            "trigger_at": 0,
            "effects": [{"type": "set_belief", "npc_id": "npc_2", "key": "resume_ok", "value": 0.5}],
        })
        engine.tick_seconds = 60.0
        engine.tick()
        self.assertEqual(engine.world.story_timeline.get_event("resume_event")["status"], "deferred")
        self.assertNotIn("resume_ok", engine.world.get_npc("npc_2").beliefs)
        # Walk away: the session must auto-end and release the deferred event.
        engine.world.player.x, engine.world.player.y = 0.0, 0.0
        engine.tick()
        self.assertFalse(engine._dialogue_session_view()["active"])
        for _ in range(3):
            engine.tick()
        self.assertEqual(engine.world.story_timeline.get_event("resume_event")["status"], "triggered")
        self.assertEqual(engine.world.get_npc("npc_2").beliefs["resume_ok"], 0.5)

    def test_manual_end_conversation_still_works(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你好，Ren。")
        self.assertTrue(engine._dialogue_session_view()["active"])
        result = engine.end_dialogue_session()
        self.assertTrue(result["success"])
        self.assertFalse(engine._dialogue_session_view()["active"])


if __name__ == "__main__":
    unittest.main()
