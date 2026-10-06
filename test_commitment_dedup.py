# -*- coding: utf-8 -*-
"""P0-4 regression: the same still-valid promise must not be registered twice."""

import tempfile
import unittest
from pathlib import Path

from npc_action_decision import FakeNPCGoalProvider
from open_world import OpenWorldEngine
from persistence import load_world, save_world


def offline_engine(provider):
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.npc_goal_provider = provider
    engine.tick_seconds = 3600.0
    engine._last_director_day = 0
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    return engine


LEAD_MAYOR = {
    "action_required": True, "goal_type": "lead_player_to", "target": "mayor_house",
    "reason": "player asked", "confidence": 0.9,
}


class CommitmentDedupTests(unittest.TestCase):
    def test_case1_repeated_request_registers_once(self):
        engine = offline_engine(FakeNPCGoalProvider(dict(LEAD_MAYOR)))
        engine.talk("npc_2", "Ren，你能带我去村长家看看吗？")
        self.assertEqual(len(engine.commitments.items), 1)

        engine.talk("npc_2", "Ren，你能带我去村长家看看吗？")
        engine.talk("npc_2", "带我去村长家吧")
        self.assertEqual(len(engine.commitments.items), 1)
        self.assertEqual(
            engine.world.get_npc("npc_2").goals.count("lead_player_to:mayor_house"), 1
        )
        self.assertEqual(len(engine.commitments.active(12)), 1)

    def test_case2_different_goals_are_kept(self):
        calls = {"n": 0}

        def scripted(context):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"action_required": True, "goal_type": "lead_player_to",
                        "target": "mayor_house", "confidence": 0.9}
            return {"action_required": True, "goal_type": "lead_player_to",
                    "target": "forest", "confidence": 0.9}

        engine = offline_engine(FakeNPCGoalProvider(scripted))
        engine.talk("npc_2", "带我去村长家")
        engine.talk("npc_2", "带我去森林")
        self.assertEqual(len(engine.commitments.items), 2)
        goal_strings = {item["goal_string"] for item in engine.commitments.items}
        self.assertEqual(
            goal_strings, {"lead_player_to:mayor_house", "lead_player_to:forest"}
        )

    def test_case3_ended_promise_allows_a_new_one(self):
        engine = offline_engine(FakeNPCGoalProvider(dict(LEAD_MAYOR)))
        engine.talk("npc_2", "带我去村长家")
        self.assertEqual(len(engine.commitments.items), 1)

        # The old promise has ended (e.g. kept / abandoned / cancelled).
        engine.commitments.items[-1]["status"] = "kept"

        engine.talk("npc_2", "再带我去村长家")
        self.assertEqual(len(engine.commitments.items), 2)
        self.assertEqual(engine.commitments.items[-1]["status"], "agreed")
        # Only one of the two is still valid.
        active = [i for i in engine.commitments.items if i["status"] not in
                  {"kept", "abandoned", "forgotten", "deceived", "cancelled", "no_action"}]
        self.assertEqual(len(active), 1)

    def test_case4_reprocessing_after_save_load_keeps_one(self):
        engine = offline_engine(FakeNPCGoalProvider(dict(LEAD_MAYOR)))
        engine.talk("npc_2", "带我去村长家")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_world(engine.world, path)
            loaded = load_world(path)

        # Commitments live on the engine, not in WorldState, so a reloaded
        # engine starts with an empty registry (no stale duplicate possible).
        self.assertFalse(hasattr(loaded, "commitments"))

        replay = OpenWorldEngine()
        replay.director.api_key = ""
        replay.director.provider = None
        replay.npc_goal_provider = FakeNPCGoalProvider(dict(LEAD_MAYOR))
        replay.world.player.x, replay.world.player.y = 69.0, 70.0
        replay.world.get_npc("npc_2").position_override = [70.0, 70.0]
        replay.adopt_world(loaded)
        self.assertEqual(len(replay.commitments.items), 0)

        replay.talk("npc_2", "带我去村长家")
        replay.talk("npc_2", "带我去村长家")
        self.assertEqual(len(replay.commitments.items), 1)


if __name__ == "__main__":
    unittest.main()
