"""Step 12.1 验收测试：语言行动（说话 → 真的去做 / 口头答应 / 放鸽子）与"我听到的"。"""

import unittest

from commitments import CommitmentRegistry
from entities import NPC, Player
from event import execute_event
from npc_action_decision import FakeNPCGoalProvider
from open_world import OpenWorldEngine
from world import EXPLORE_LOCATIONS, WorldState
from visualization.player_view import PlayerObserver, build_player_view


def offline_engine():
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.tick_seconds = 3600.0
    return engine


class LeadPlayerWaitingTests(unittest.TestCase):
    def test_npc_leads_then_waits_and_joins_when_player_arrives(self):
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]          # 已经到达森林
        ren.goals = ["lead_player_to:forest"]         # 目标已落地
        engine.world.player.x, engine.world.player.y = 38.0, 40.0   # 玩家就在旁边
        engine.commitments.register(
            npc_id="npc_2", npc_name="Ren",
            goal={"goal_type": "lead_player_to", "target": "forest"},
            goal_string="lead_player_to:forest", commitment="immediate",
            tick_index=engine.tick_index,
        )
        for _ in range(3):
            engine.tick()

        record = engine.commitments.items[-1]
        self.assertEqual(record["status"], "kept")
        self.assertTrue(any("等到了玩家" in n["text"] for n in engine.commitments.notices))

    def test_npc_gives_up_waiting_after_five_ticks(self):
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        ren.goals = ["lead_player_to:forest"]                        # 目标已落地
        engine.world.player.x, engine.world.player.y = 0.0, 0.0     # 玩家很远
        engine.commitments.register(
            npc_id="npc_2", npc_name="Ren",
            goal={"goal_type": "lead_player_to", "target": "forest"},
            goal_string="lead_player_to:forest", commitment="immediate",
            tick_index=engine.tick_index,
        )
        for _ in range(8):
            engine.tick()

        record = engine.commitments.items[-1]
        self.assertEqual(record["status"], "abandoned")
        self.assertTrue(any("没等到" in n["text"] for n in engine.commitments.notices))
        # 到点的目标被撤掉，NPC 不再原地干等
        self.assertNotIn("lead_player_to:forest", engine.world.get_npc("npc_2").goals)


class SpeechActCommitmentTests(unittest.TestCase):
    def test_deceive_writes_no_goal_and_is_reported(self):
        engine = offline_engine()
        engine.npc_goal_provider = FakeNPCGoalProvider({
            "action_required": True, "goal_type": "lead_player_to", "target": "forest",
            "reason": "Player asked to go together.", "confidence": 0.9,
            "commitment": "deceive",
        })
        decision = engine._maybe_decide_npc_goal("npc_2", "你能带我去森林看看吗？")

        self.assertTrue(decision["action_required"])
        self.assertFalse(decision.get("commitment_applied", True))
        self.assertEqual(engine.world.get_npc("npc_2").goals, ["find ledger"])   # 没有落地新目标

        for _ in range(8):
            engine.tick()
        record = engine.commitments.items[-1]
        self.assertEqual(record["status"], "deceived")
        self.assertTrue(any("没有动身" in n["text"] for n in engine.commitments.notices))

    def test_later_commitment_is_forgotten_if_never_started(self):
        registry = CommitmentRegistry()
        world = WorldState(player=Player(0.0, 0.0), npcs=[NPC(id="npc_a", name="AAA", x=1.0, y=1.0)])
        registry.register(npc_id="npc_a", npc_name="AAA",
                          goal={"goal_type": "go_to", "target": "forest"},
                          goal_string="", commitment="later", tick_index=0)
        for tick in range(1, 12):
            registry.tick(world, tick)
        record = registry.items[-1]
        self.assertEqual(record["status"], "forgotten")
        self.assertTrue(any("一直没去" in n["text"] for n in registry.notices))

    def test_follow_commitment_ends_when_player_says_stop(self):
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        engine.world.player.x, engine.world.player.y = 41.0, 40.0
        engine.commitments.register(npc_id="npc_2", npc_name="Ren",
                                    goal={"goal_type": "follow_player", "target": "player"},
                                    goal_string="follow player", commitment="immediate",
                                    tick_index=engine.tick_index)
        engine._set_follow_state("npc_2", True)
        engine.commitments.tick(engine.world, engine.tick_index)
        self.assertEqual(engine.commitments.items[-1]["status"], "acting")

        engine._set_follow_state("npc_2", False)
        self.assertEqual(engine.commitments.items[-1]["status"], "cancelled")

    def test_follow_commitment_reports_when_npc_leaves(self):
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        engine.world.player.x, engine.world.player.y = 41.0, 40.0
        engine.commitments.register(npc_id="npc_2", npc_name="Ren",
                                    goal={"goal_type": "follow_player", "target": "player"},
                                    goal_string="follow player", commitment="immediate",
                                    tick_index=engine.tick_index)
        engine._set_follow_state("npc_2", True)
        engine.commitments.tick(engine.world, engine.tick_index)
        # NPC 自己改成别的目标 = 主动不跟了
        ren.remove_goal("follow player")
        ren.add_goal("investigate:ev_something")
        engine._set_follow_state  # noqa: B018  (只是说明这里不再由玩家取消)
        engine.commitments.tick(engine.world, engine.tick_index + 1)

        record = engine.commitments.items[-1]
        self.assertEqual(record["status"], "abandoned")
        self.assertTrue(any("不跟着玩家" in n["text"] for n in engine.commitments.notices))


class HeardDialogueTests(unittest.TestCase):
    def spoken_world(self, player_position, speaker_position):
        return WorldState(
            time=0.0,
            player=Player(*player_position),
            npcs=[
                NPC(id="npc_a", name="AAA", x=speaker_position[0], y=speaker_position[1],
                    position_override=list(speaker_position)),
                NPC(id="npc_b", name="BBB", x=speaker_position[0] + 1, y=speaker_position[1],
                    position_override=[speaker_position[0] + 1, speaker_position[1]]),
            ],
        )

    def test_npc_speaking_to_player_is_heard(self):
        world = self.spoken_world((10.0, 10.0), (11.0, 10.0))
        execute_event(world, {"event_id": "d1", "time": 0.0, "effects": [
            {"type": "dialogue", "speaker_id": "npc_a", "content": "我有些话想告诉你。"}]})
        engine = offline_engine()
        engine._world = world
        observer = PlayerObserver()
        observer.observe(engine.snapshot())
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        heard = view["heard"]
        self.assertTrue(any(h["kind"] == "overheard" or h["kind"] == "to_player" for h in heard))
        self.assertTrue(any("我有些话想告诉你" in h["text"] for h in heard))

    def test_far_dialogue_is_not_heard(self):
        world = self.spoken_world((0.0, 0.0), (90.0, 90.0))
        execute_event(world, {"event_id": "d2", "time": 0.0, "effects": [
            {"type": "dialogue", "speaker_id": "npc_a", "content": "远处的悄悄话。"}]})
        engine = offline_engine()
        engine._world = world
        observer = PlayerObserver()
        observer.observe(engine.snapshot())
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)
        self.assertFalse(any("远处的悄悄话" in h["text"] for h in view["heard"]))

    def test_overheard_npc_talk_uses_real_history(self):
        world = self.spoken_world((10.0, 10.0), (11.0, 10.0))
        world.add_history("npc_talked", "npc_b", "AAA 对 BBB 说了话。", actor="npc_a")
        engine = offline_engine()
        engine._world = world
        observer = PlayerObserver()
        observer.observe(engine.snapshot())
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        self.assertTrue(any(h["kind"] == "overheard" for h in view["heard"]))
        self.assertFalse([h for h in view["heard"] if h["kind"] == "to_player"])

    def test_commitments_and_notices_reach_the_player_view(self):
        engine = offline_engine()
        engine.commitments.register(npc_id="npc_2", npc_name="Ren",
                                    goal={"goal_type": "lead_player_to", "target": "forest"},
                                    goal_string="lead_player_to:forest", commitment="immediate",
                                    tick_index=0)
        engine.commitments._notify("Ren 等了玩家一会儿，先走了。")
        view = build_player_view(engine, PlayerObserver(), EXPLORE_LOCATIONS)
        self.assertTrue(view["commitments"])
        self.assertTrue(any("先走了" in n["text"] for n in view["notices"]))
        self.assertEqual(view["commitments"][-1]["npc"], "Ren")


if __name__ == "__main__":
    unittest.main()
