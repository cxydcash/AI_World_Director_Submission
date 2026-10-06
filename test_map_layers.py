"""Step 12.0 地图分层（展示层）测试。

验证：玩家是否"接触 / 发现 / 关注"决定地图显示什么；
只改展示层，不修改 WorldState、NPC 行为与模拟频率。
"""

import unittest

from event import execute_event
from open_world import OpenWorldEngine
from world import EXPLORE_LOCATIONS
from visualization.player_view import PlayerObserver, build_player_view


def engine():
    e = OpenWorldEngine()
    e.director.api_key = ""
    e.director.provider = None
    e.tick_seconds = 3600.0
    return e


def add_evidence(e, evidence_id, x, y, follow_up=None, evidence_type="note"):
    effect = {
        "type": "add_evidence", "evidence_id": evidence_id, "evidence_type": evidence_type,
        "x": x, "y": y, "description": "单元测试线索。",
    }
    if follow_up:
        effect["follow_up_goal"] = follow_up
    execute_event(e.world, {"event_id": f"ev_{evidence_id}", "time": e.world.time, "effects": [effect]})


class MapLayerTests(unittest.TestCase):
    def test_map_only_shows_focused_npcs(self):
        e = engine()
        # 玩家和 Ren 靠近（算"接触过"），Mayor 很远（完全没接触）
        e.world.player.x, e.world.player.y = 50.0, 50.0
        ren = e.world.get_npc("npc_2")
        ren.position_override = [50.0, 50.0]
        mayor = e.world.get_npc("npc_1")
        mayor.position_override = [5.0, 5.0]

        view = build_player_view(e, PlayerObserver(), EXPLORE_LOCATIONS)
        ids = {n["id"] for n in view["map"]["npcs"]}
        self.assertIn("npc_2", ids)          # 接触过 -> 默认关注
        self.assertNotIn("npc_1", ids)       # 没接触、也不是主要人物 -> 不上大地图

    def test_explicit_focus_limits_and_restores(self):
        e = engine()
        view = build_player_view(e, PlayerObserver(), EXPLORE_LOCATIONS, focus_ids=["npc_2"])
        self.assertEqual([n["id"] for n in view["map"]["npcs"]], ["npc_2"])
        self.assertFalse(view["map"]["focus_is_auto"])

        auto = build_player_view(e, PlayerObserver(), EXPLORE_LOCATIONS, focus_ids=None)
        self.assertTrue(auto["map"]["focus_is_auto"])

    def test_met_flag_follows_real_interaction(self):
        e = engine()
        ren = e.world.get_npc("npc_2")
        ren.position_override = [50.0, 50.0]
        e.world.player.x, e.world.player.y = 50.0, 52.0     # 近距离 -> 算接触
        view = build_player_view(e, PlayerObserver(), EXPLORE_LOCATIONS)
        self.assertTrue(next(p for p in view["people"] if p["id"] == "npc_2")["met"])
        self.assertTrue(any(p["id"] == "npc_2" for p in view["focus_candidates"]))

    def test_undiscovered_clue_never_appears_on_map(self):
        e = engine()
        add_evidence(e, "hidden_clue", 40.0, 40.0, follow_up="investigate:hidden_clue")
        view = build_player_view(e, PlayerObserver(), EXPLORE_LOCATIONS)
        keys = {c["key"] for c in view["map"]["clues"]}
        self.assertNotIn("id:hidden_clue", keys)            # 未发现 -> 绝不上大地图
        self.assertIn("id:hidden_clue", {c["key"] for c in view["clues"]})   # 记录页仍可查

    def test_discovered_clue_of_focused_npc_becomes_map_hint(self):
        e = engine()
        ren = e.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        e.world.player.x, e.world.player.y = 40.0, 40.0
        ren.goals = ["investigate:key_clue"]
        add_evidence(e, "key_clue", 40.0, 40.0)
        observer = PlayerObserver()
        for _ in range(3):
            e._update_npcs_with_planner()
            observer.observe(e.snapshot())

        view = build_player_view(e, observer, EXPLORE_LOCATIONS)
        self.assertTrue(next(c for c in view["clues"] if c["key"] == "id:key_clue")["discovered"])
        self.assertIn("id:key_clue", {c["key"] for c in view["map"]["clues"]})

    def test_local_info_only_shows_focused_and_discovered(self):
        e = engine()
        e.world.player.x, e.world.player.y = 40.0, 40.0      # 森林
        ren = e.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        add_evidence(e, "here_seen", 40.0, 40.0)             # 同地点的线索
        add_evidence(e, "far_away", 90.0, 90.0)              # 别的地点
        # 让 Ren 发现本地的线索
        ren.goals = ["investigate:here_seen"]
        observer = PlayerObserver()
        for _ in range(3):
            e._update_npcs_by_planner() if hasattr(e, "_update_npcs_by_planner") else e._update_npcs_with_planner()
            observer.observe(e.snapshot())
        view = build_player_view(e, observer, EXPLORE_LOCATIONS, focus_ids=["npc_2"])

        local = view["local_info"]
        self.assertEqual(local["place"], "森林")
        self.assertTrue(any(n["id"] == "npc_2" for n in local["npcs"]))
        names = [c["name"] for c in local["clues"]]
        self.assertTrue(names)
        # 未发现的线索不会出现在当地信息里
        undiscovered = [c for c in view["clues"] if not c["discovered"]]
        for c in undiscovered:
            self.assertNotIn(c["key"], [x["key"] for x in local["clues"]])

    def test_display_layer_does_not_touch_world_state(self):
        e = engine()
        add_evidence(e, "clue_a", 40.0, 40.0, follow_up="investigate:clue_a")
        before = (len(e.world.npcs), len(e.world.evidence), e.world.player.x, e.world.time)
        build_player_view(e, PlayerObserver(), EXPLORE_LOCATIONS, focus_ids=["npc_2"])
        after = (len(e.world.npcs), len(e.world.evidence), e.world.player.x, e.world.time)
        self.assertEqual(before, after)

    def test_map_labels_are_bounded_by_focus(self):
        """大地图上的标签数 = 关注人物 + 关键线索，而不是 WorldState 里的全部对象。"""
        e = engine()
        add_evidence(e, "c1", 40.0, 40.0, follow_up="investigate:c1")
        add_evidence(e, "c2", 41.0, 41.0, follow_up="investigate:c2")
        view = build_player_view(e, PlayerObserver(), EXPLORE_LOCATIONS, focus_ids=[])
        self.assertEqual(view["map"]["npcs"], [])
        self.assertEqual(view["map"]["clues"], [])      # 没有关注任何人 -> 也没有关键线索


if __name__ == "__main__":
    unittest.main()
