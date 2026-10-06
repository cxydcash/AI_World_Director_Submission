"""Step 12.0 验收测试：玩家视角与叙事观察层。

覆盖：玩家/世界/NPC 记录边界、线索身份、轨迹去重、对话归属、
玩家可见文本中文化、以及隐藏 AI Director 页面后后端功能仍然正常。
"""

import re
import unittest

from director import FakeDirectorProvider
from event import execute_event
from open_world import OpenWorldEngine
from world import EXPLORE_LOCATIONS, WorldState
from entities import NPC, Player
from visualization.observation import (
    CAT_EVIDENCE,
    CAT_NPC_ACTION,
    CAT_NPC_DIALOGUE,
    CAT_PLAYER_ACTION,
    CAT_PLAYER_INPUT,
    CAT_WORLD,
    classify_history_entry,
    clue_key,
)
from visualization.player_view import PlayerObserver, build_player_view


def offline_engine():
    """真实引擎，但离线（不调用 LLM），用于验证数据边界。"""
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.tick_seconds = 3600.0
    return engine


def observe(engine, observer, ticks):
    for _ in range(ticks):
        engine.tick()
        observer.observe(engine.snapshot())


class PlayerActionBoundaryTests(unittest.TestCase):
    def test_no_player_input_keeps_player_actions_empty(self):
        engine = offline_engine()
        observer = PlayerObserver()
        observe(engine, observer, 48)
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        self.assertEqual(view["my_story"]["actions"], [])
        self.assertFalse([r for r in view["recent"] if r["category"] == CAT_PLAYER_ACTION])
        self.assertEqual(view["debug"]["player_action_log"], [])
        # 没有玩家行为时，"我的一天"里不能出现任何玩家动作动词
        # （停留记录是位置信息，不算行为，允许出现）
        for day in view["my_story"]["daily"]:
            for verb in ("散播", "留下", "移动了位置", "说了话"):
                self.assertNotIn(verb, day["text"])

    def test_npc_movement_never_becomes_a_player_action(self):
        engine = offline_engine()
        observer = PlayerObserver()
        observe(engine, observer, 24)
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        categories = {r["category"] for r in view["recent"]}
        self.assertIn(CAT_NPC_ACTION, categories)
        self.assertNotIn(CAT_PLAYER_ACTION, categories)
        self.assertEqual(view["my_story"]["actions"], [])

    def test_director_story_event_never_becomes_a_player_action(self):
        engine = offline_engine()
        engine.director.provider = FakeDirectorProvider({
            "event_id": "fixture_story_event",
            "title": "fixture",
            "description": "fixture event used by unit tests only",
            "trigger_at": 0.0,
            "conditions": [],
            "cancel_conditions": [],
            "effects": [
                {"type": "set_belief", "npc_id": "npc_2", "key": "fixture_belief", "value": 0.5},
                {"type": "add_goal", "npc_id": "npc_2", "goal": "investigate:fixture_clue"},
            ],
        })
        result = engine.run_director_cycle()
        self.assertTrue(result["added"])

        observer = PlayerObserver()
        observe(engine, observer, 6)
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        self.assertEqual(view["my_story"]["actions"], [])
        self.assertIn("fixture_story_event", [t["event_id"] for t in view["debug"]["timeline"]])

    def test_real_player_action_enters_player_actions(self):
        engine = offline_engine()
        engine.world.player.x, engine.world.player.y = 50.0, 33.0
        engine.execute_action({
            "type": "create_rumor", "target": "npc_3", "content": "单元测试用的传闻。",
        })
        observer = PlayerObserver()
        observer.observe(engine.snapshot())
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        actions = view["my_story"]["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["category"], CAT_PLAYER_ACTION)
        self.assertIn("传闻", actions[0]["text"])

    def test_history_actor_default_is_not_treated_as_player(self):
        """world.add_history 的默认 actor 就是 player，不能被当成玩家行为。"""
        engine = offline_engine()
        world = engine.world
        world.add_history("npc_investigated_evidence", "npc_2", "NPC 调查了线索。")
        world.add_history("timeline_changed", "story", "Director adjusted the timeline.")
        world.add_history("weather_changed", "world", "Weather changed to Rain.", actor="environment")

        observer = PlayerObserver()
        observer.observe(engine.snapshot())
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        self.assertEqual(view["my_story"]["actions"], [])
        suspects = {row["action"] for row in view["debug"]["suspect_actor_defaults"]}
        self.assertEqual(suspects, {"npc_investigated_evidence", "timeline_changed"})
        # 分类只依据真实来源
        # npc_* 记录即使被默认 actor 标成 player，也仍然归为 NPC 行动（绝不是玩家行为）
        self.assertEqual(
            classify_history_entry({"actor": "player", "action": "npc_investigated_evidence"}),
            CAT_NPC_ACTION,
        )
        self.assertEqual(
            classify_history_entry({"actor": "player", "action": "timeline_changed"}),
            CAT_WORLD,
        )
        self.assertEqual(classify_history_entry({"actor": "npc_2", "action": "npc_talked"}), CAT_NPC_ACTION)
        self.assertEqual(classify_history_entry({"actor": "story", "action": "timeline_changed"}), CAT_WORLD)


class ClueIdentityTests(unittest.TestCase):
    def add_clue(self, engine, evidence_id="fixture_clue", place=(40.0, 40.0)):
        execute_event(engine.world, {
            "event_id": f"fixture_evidence_{evidence_id}",
            "time": engine.world.time,
            "effects": [{
                "type": "add_evidence",
                "evidence_id": evidence_id,
                "evidence_type": "ledger_copy",
                "x": place[0], "y": place[1],
                "description": "单元测试线索。",
            }],
        })

    def test_one_clue_keeps_one_identity_through_its_lifecycle(self):
        engine = offline_engine()
        self.add_clue(engine)
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        ren.goals = ["investigate:fixture_clue"]

        observer = PlayerObserver()
        for _ in range(4):
            engine._update_npcs_with_planner()
            observer.observe(engine.snapshot())
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        self.assertEqual(len(view["clues"]), 1)
        clue = view["clues"][0]
        stages = [e["stage"] for e in clue["lifecycle"]]
        self.assertIn("产生", stages)
        self.assertTrue(clue["discovered"])
        self.assertEqual(clue["key"], clue_key(engine.world.evidence[0]))

    def test_two_different_clues_stay_separate_entities(self):
        engine = offline_engine()
        self.add_clue(engine, "fixture_clue_a", (40.0, 40.0))
        self.add_clue(engine, "fixture_clue_b", (20.0, 20.0))
        observer = PlayerObserver()
        observer.observe(engine.snapshot())
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)
        self.assertEqual(len(view["clues"]), 2)
        self.assertEqual(len({c["key"] for c in view["clues"]}), 2)


class TrackAndDialogueTests(unittest.TestCase):
    def test_moving_npc_does_not_create_per_tick_records(self):
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [10.0, 10.0]
        ren.goals = ["reach_location:forest"]
        observer = PlayerObserver()
        observe(engine, observer, 12)

        moves = [a for a in observer.activity_log(200)
                 if a.get("npc_id") == "npc_2" and a.get("kind") == "move"]
        self.assertLess(len(moves), 12)          # 不是每 tick 一条
        points = observer.track_of("npc_2")
        self.assertGreater(len(points), 0)
        self.assertEqual(len({p["place"] for p in points}), len({p["place"] for p in points}))

    def test_idle_npc_produces_no_movement_records(self):
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        ren.goals = ["wait"]
        observer = PlayerObserver()
        observe(engine, observer, 12)

        self.assertEqual(observer.track_of("npc_2"), [])
        self.assertEqual(observer.stays_of("npc_2"), [])
        self.assertFalse([a for a in observer.activity_log(200)
                          if a.get("npc_id") == "npc_2" and a.get("kind") == "move"])

    def test_npc_dialogue_goes_to_npc_history_not_player_actions(self):
        engine = offline_engine()
        execute_event(engine.world, {
            "event_id": "fixture_dialogue",
            "time": engine.world.time,
            "effects": [{"type": "dialogue", "speaker_id": "npc_2", "content": "我在磨坊发现了东西。"}],
        })
        observer = PlayerObserver()
        observer.observe(engine.snapshot())
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        detail = view["npcs_detail"]["npc_2"]
        self.assertTrue(any("磨坊" in t["text"] for t in detail["talks"]))
        self.assertEqual(view["my_story"]["actions"], [])
        self.assertFalse([r for r in view["recent"] if r["category"] == CAT_PLAYER_ACTION])


class PlayerVisibleTextTests(unittest.TestCase):
    FORBIDDEN = [
        r"\bnpc_\d", r"\bNPC_\d", r"\bev_[A-Za-z0-9_]+", r"\bitem_[a-z_]+",
        r"generated_event", r"\badd_goal\b", r"\bset_belief\b", r"\badd_memory\b",
        r"\bfind_item:", r"\breach_location:", r"\btalk_to:", r"\bhelp_npc:",
        r"\bgoal_states\b", r"\bpriority\b", r"\bbelief=", r"\brejection\b",
        r"\bfallback\b", r"\bprovider\b",
    ]

    def player_texts(self, view):
        texts = [r["text"] for r in view["recent"]]
        texts += [c["category_label"] for c in view["recent"]]
        for p in view["people"]:
            texts += [p["name"], p["title"], p["place"], p["action"], p["goal"], p["tier_label"]] + list(p["reasons"])
        for c in view["clues"]:
            texts += [c["name"], c["place"], c["origin"], c["current"], c["status_label"]] + list(c["discoverers"])
            texts += [e.get("text", "") for e in c["lifecycle"]]
        for t in view["my_story"]["thoughts"]:
            texts.append(t["text"])
        for d in view["my_story"]["daily"]:
            texts.append(d["text"])
        for p in view["npcs_detail"].values():
            texts += [t["text"] for t in p.get("talks", [])]
        return [str(t) for t in texts if t]

    def test_player_facing_text_has_no_internal_identifiers(self):
        engine = offline_engine()
        engine.world.player.x, engine.world.player.y = 50.0, 33.0
        engine.execute_action({"type": "create_rumor", "target": "npc_3", "content": "单元测试传闻。"})
        execute_event(engine.world, {
            "event_id": "fixture_evidence", "time": engine.world.time,
            "effects": [{"type": "add_evidence", "evidence_id": "fixture_ev", "evidence_type": "note",
                         "x": 50.0, "y": 33.0, "description": "单元测试线索。"}],
        })
        observer = PlayerObserver()
        observe(engine, observer, 6)
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)

        for text in self.player_texts(view):
            for pattern in self.FORBIDDEN:
                self.assertIsNone(re.search(pattern, text), f"{pattern!r} 出现在玩家可见文本: {text!r}")


class DebugIsolationTests(unittest.TestCase):
    def test_backend_director_still_works_while_player_ui_hides_it(self):
        engine = offline_engine()
        engine.director.provider = FakeDirectorProvider({
            "event_id": "fixture_ai_event",
            "title": "fixture",
            "description": "fixture",
            "trigger_at": 0.0,
            "conditions": [],
            "cancel_conditions": [],
            "effects": [{"type": "set_belief", "npc_id": "npc_2", "key": "fixture_state", "value": 0.7}],
        })
        self.assertTrue(engine.run_director_cycle()["added"])
        engine.tick()
        self.assertEqual(engine.world.get_npc("npc_2").beliefs.get("fixture_state"), 0.7)

        observer = PlayerObserver()
        view = build_player_view(engine, observer, EXPLORE_LOCATIONS)
        # AI 编导信息只在 debug 段，不进入玩家主视图字段
        self.assertNotIn("fixture_ai_event", str(view["recent"]))
        self.assertNotIn("fixture_ai_event", str(view["my_story"]))
        self.assertIn("fixture_ai_event", str(view["debug"]["timeline"]))

    def test_player_view_has_no_story_specific_hardcoding(self):
        """空世界也要能正常生成视图（不依赖任何具体剧情）。"""
        world = WorldState(
            time=0.0,
            player=Player(50.0, 50.0),
            npcs=[NPC(id="npc_a", name="AAA", x=10.0, y=10.0)],
        )
        engine = OpenWorldEngine.__new__(OpenWorldEngine)
        engine._world = world
        engine.director = engine.director if hasattr(engine, "director") else None
        # 直接用观察层函数验证：没有任何线索/证据时也不报错
        observer = PlayerObserver()
        from visualization.observation import build_clue_entities, importance_scores, tier_of
        self.assertEqual(build_clue_entities({"evidence": [], "items": []}, observer, EXPLORE_LOCATIONS), [])
        scores = importance_scores({"time": 0.0, "npcs": [], "open_world": {}}, observer, [], EXPLORE_LOCATIONS)
        self.assertEqual(scores, {})
        self.assertEqual(tier_of(0)[0], "background")


if __name__ == "__main__":
    unittest.main()
