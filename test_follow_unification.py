"""Step 8 Stage 4.5 tests: unified follow path (goal decision, not _execute_follow)."""

import unittest

from npc_action_decision import FakeNPCGoalProvider
from open_world import OpenWorldEngine


FOLLOW_PLAYER = {
    "action_required": True,
    "goal_type": "follow_player",
    "target": "player",
    "reason": "player ordered me to follow",
    "confidence": 0.9,
}


def follow_engine(decision=FOLLOW_PLAYER):
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    engine.world.get_npc("npc_1").position_override = [69.0, 70.0]
    engine.npc_goal_provider = FakeNPCGoalProvider(decision)
    return engine


class FollowUnificationTests(unittest.TestCase):
    def _assert_follows(self, message):
        engine = follow_engine()
        ren = engine.world.get_npc("npc_2")
        result = engine.talk("npc_2", message)
        decision = result["npc_goal_decision"]
        self.assertTrue(decision["action_required"])
        self.assertEqual(decision["goal"]["goal_type"], "follow_player")
        self.assertIn("follow player", ren.goals)
        self.assertTrue(engine.world.characters_state["npc_2"].get("following_player"))
        return engine, ren, result

    def test_command_you_follow_me(self):
        self._assert_follows("你跟我走")

    def test_command_gen_zhe_wo(self):
        self._assert_follows("跟着我")

    def test_command_you_follow_me_lai(self):
        self._assert_follows("你跟我来")

    def test_question_does_not_follow(self):
        engine = follow_engine()
        ren = engine.world.get_npc("npc_2")
        result = engine.talk("npc_2", "你跟着我吗？")
        decision = result["npc_goal_decision"]
        self.assertFalse(decision["action_required"])
        self.assertIsNone(decision["goal"])
        self.assertNotIn("follow player", ren.goals)
        self.assertFalse(engine.world.characters_state["npc_2"].get("following_player"))

    def test_join_request_does_not_follow(self):
        engine = follow_engine()
        ren = engine.world.get_npc("npc_2")
        result = engine.talk("npc_2", "我能跟你一起去看看吗？")
        decision = result["npc_goal_decision"]
        self.assertFalse(decision["action_required"])
        self.assertNotEqual((decision.get("goal") or {}).get("goal_type"), "follow_player")
        self.assertNotIn("follow player", ren.goals)

    def test_follow_goal_is_written_through_the_event_dsl(self):
        engine, ren, result = self._assert_follows("你跟我走")
        effects = engine.world.event_history[-1]["effects"]
        self.assertEqual(effects[0]["type"], "set_follow_target")
        self.assertEqual(effects[0]["follow_target"], "player")

    def test_planner_executes_follow_next_tick(self):
        engine, ren, result = self._assert_follows("你跟我走")
        self.assertEqual(ren.goals[0], "follow player")
        before = engine.world.npc_position("npc_2")
        engine.world.player.x, engine.world.player.y = 50.0, 70.0
        engine.tick_seconds = 3600.0
        engine.tick()
        after = engine.world.npc_position("npc_2")
        self.assertNotEqual(after, before)
        self.assertLess(
            engine.world.distance_between(after, (50.0, 70.0)),
            engine.world.distance_between(before, (50.0, 70.0)),
        )

    def test_reply_does_not_use_the_old_fixed_follow_reply(self):
        engine, ren, result = self._assert_follows("你跟我走")
        self.assertNotEqual(result.get("reply"), "可以，我跟着你。")
        self.assertEqual(result.get("intent"), "request_follow")

    def test_stop_follow_ends_the_goal(self):
        engine, ren, result = self._assert_follows("你跟我走")
        engine.world.player.x, engine.world.player.y = engine.world.npc_position("npc_2")
        stop = engine.talk("npc_2", "你不用跟着我了。")
        self.assertEqual(stop.get("intent"), "stop_follow")
        self.assertNotIn("follow player", ren.goals)
        self.assertFalse(engine.world.characters_state["npc_2"].get("following_player"))

    def test_no_provider_keeps_the_legacy_follow_path(self):
        engine = OpenWorldEngine()
        engine.director.api_key = ""
        engine.director.provider = None
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        self.assertIsNone(engine.npc_goal_provider)
        result = engine.talk("npc_2", "你跟我走")
        self.assertTrue(engine.world.characters_state["npc_2"].get("following_player"))
        self.assertIn("follow player", ren.goals)
        self.assertIsNone(result.get("npc_goal_decision"))


if __name__ == "__main__":
    unittest.main()
