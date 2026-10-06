"""Step 12.2 测试：对话期间的时间流速、外部变化与 NPC 停留。

只验证引擎的对话节奏与"原地不动"行为，不改 Planner/ActionLayer 逻辑。
"""

import unittest

from open_world import DIALOGUE_TIME_SCALE, OpenWorldEngine


def engine():
    e = OpenWorldEngine()
    e.director.api_key = ""
    e.director.provider = None
    e.tick_seconds = 3600.0
    return e


class DialoguePacingTests(unittest.TestCase):
    def test_world_time_slows_down_during_dialogue(self):
        e = engine()
        e.world.player.x, e.world.player.y = 50.0, 32.0
        mira = e.world.get_npc("npc_3")
        mira.position_override = [50.0, 30.0]

        before = e.world.time
        e.tick()
        normal_step = e.world.time - before

        e.talk("npc_3", "你好，Mira。")          # 建立对话会话
        self.assertTrue(e._dialogue_session_view()["active"])
        start = e.world.time
        e.tick()
        talk_step = e.world.time - start

        self.assertLess(talk_step, normal_step)
        self.assertAlmostEqual(talk_step, e.tick_seconds * DIALOGUE_TIME_SCALE, delta=1.0)

    def test_time_returns_to_normal_after_session_ends(self):
        e = engine()
        e.world.player.x, e.world.player.y = 50.0, 32.0
        e.world.get_npc("npc_3").position_override = [50.0, 30.0]
        e.talk("npc_3", "你好。")
        start = e.world.time
        e.tick()
        slowed = e.world.time - start

        e.end_dialogue_session()
        start = e.world.time
        e.tick()
        resumed = e.world.time - start

        self.assertGreater(resumed, slowed)
        self.assertAlmostEqual(resumed, e.tick_seconds, delta=1.0)

    def test_due_story_event_is_less_likely_to_fire_during_dialogue(self):
        """对话中世界时间走得慢 -> 到点的外部事件不会那么快发生。"""
        e = engine()
        e.world.player.x, e.world.player.y = 50.0, 32.0
        e.world.get_npc("npc_3").position_override = [50.0, 30.0]
        e.world.story_timeline.add_event({
            "event_id": "pacing_event", "trigger_at": e.world.time + 3600.0,
            "effects": [{"type": "set_belief", "npc_id": "npc_2", "key": "paced", "value": 0.5}],
        })
        e.talk("npc_3", "我们慢慢聊。")
        e.tick()
        self.assertEqual(e.world.story_timeline.get_event("pacing_event")["status"], "pending")

        e.end_dialogue_session()
        e.tick()
        self.assertEqual(e.world.story_timeline.get_event("pacing_event")["status"], "triggered")


class DialogueNpcHoldTests(unittest.TestCase):
    def test_talking_npc_stays_put(self):
        e = engine()
        e.world.player.x, e.world.player.y = 50.0, 32.0
        mira = e.world.get_npc("npc_3")
        mira.position_override = [50.0, 30.0]
        mira.goals = ["reach_location:forest"]      # 本来要去别处
        e.talk("npc_3", "先别走，我想问你点事。")
        before = e.world.npc_position("npc_3")

        for _ in range(4):
            e.tick()

        self.assertEqual(e.world.npc_position("npc_3"), before)

    def test_other_npcs_keep_acting(self):
        e = engine()
        e.world.player.x, e.world.player.y = 50.0, 32.0
        mira = e.world.get_npc("npc_3")
        mira.position_override = [50.0, 30.0]
        ren = e.world.get_npc("npc_2")
        ren.position_override = [80.0, 80.0]
        ren.goals = ["reach_location:forest"]
        e.talk("npc_3", "我们聊聊。")

        before = e.world.npc_position("npc_2")
        for _ in range(3):
            e.tick()
        self.assertNotEqual(e.world.npc_position("npc_2"), before)   # 世界仍在运行

    def test_urgent_npc_can_still_leave_the_conversation(self):
        e = engine()
        e.world.player.x, e.world.player.y = 50.0, 32.0
        mira = e.world.get_npc("npc_3")
        mira.position_override = [50.0, 30.0]
        mira.goals = ["escape"]
        e.talk("npc_3", "我有话要问你。")

        before = e.world.npc_position("npc_3")
        for _ in range(3):
            e.tick()
        self.assertNotEqual(e.world.npc_position("npc_3"), before)   # 紧急 -> 仍会行动

    def test_fled_state_also_counts_as_urgent(self):
        e = engine()
        e.world.player.x, e.world.player.y = 50.0, 32.0
        mira = e.world.get_npc("npc_3")
        mira.position_override = [50.0, 30.0]
        mira.goals = ["reach_location:forest"]
        e.world.characters_state["npc_3"]["hunted"] = True
        e.talk("npc_3", "你还好吗？")

        before = e.world.npc_position("npc_3")
        for _ in range(3):
            e.tick()
        self.assertNotEqual(e.world.npc_position("npc_3"), before)


if __name__ == "__main__":
    unittest.main()
