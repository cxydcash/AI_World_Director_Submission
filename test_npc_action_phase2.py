"""Step 8 phase 2/3 tests: NPC action decision wiring, target pinning, ren_lost escort."""

import os
import unittest
from unittest.mock import patch

from action_layer import ActionExecutor, ActionValidator
from intent import Intent
from npc_action_decision import FakeNPCGoalProvider, LLMNPCGoalProvider
from npc_planner import NPCPlanner
from open_world import OpenWorldEngine


def runtime_engine():
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    return engine


class NPCActionPhase2Tests(unittest.TestCase):
    def test_mayor_house_alias_maps_to_home(self):
        engine = runtime_engine()
        planner = NPCPlanner()
        self.assertEqual(planner._location_coordinates("mayor_house"), (20.0, 20.0))

    def test_lead_player_to_goal_makes_the_npc_move(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        ren.goals = ["find ledger", "lead_player_to:mayor_house"]
        planner = NPCPlanner()
        action = planner.plan_next_action(engine.world, "npc_2")
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 20.0, "y": 20.0})
        validator = ActionValidator(engine.object_registry)
        executor = ActionExecutor(validator)
        validation = validator.validate(engine.world, action)
        self.assertTrue(validation.valid)
        executor.execute(engine.world, action, validation=validation)
        self.assertLess(
            engine.world.distance_between(engine.world.npc_position("npc_2"), (20.0, 20.0)),
            engine.world.distance_between((40.0, 40.0), (20.0, 20.0)),
        )

    def test_decision_lead_goal_reaches_the_planner(self):
        engine = runtime_engine()
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        engine.npc_goal_provider = FakeNPCGoalProvider({
            "action_required": True, "goal_type": "lead_player_to", "target": "mayor_house",
            "reason": "player asked me to take them there", "confidence": 0.9,
        })
        result = engine.talk("npc_2", "你能带我去村长家看看吗？")
        decision = result["npc_goal_decision"]
        self.assertTrue(decision["action_required"])
        self.assertEqual(decision["goal"]["target"], "mayor_house")
        self.assertIn("lead_player_to:mayor_house", ren.goals)
        before = engine.world.npc_position("npc_2")
        # Step 12.2：对话进行中，NPC 会留在原地等玩家把话说完
        engine.tick()
        self.assertEqual(engine.world.npc_position("npc_2"), before)
        # 结束对话后，时间恢复正常流速，NPC 才开始执行"带路"目标
        engine.end_dialogue_session()
        for _ in range(2):
            engine.tick()
        self.assertNotEqual(engine.world.npc_position("npc_2"), before)

    def test_talk_pins_follow_target_to_the_dialogue_npc(self):
        engine = runtime_engine()
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
        engine.world.get_npc("npc_1").position_override = [69.0, 70.0]

        def forced(text, **kwargs):
            return Intent(
                source="dialogue", actor_id="player", target_id="npc_1",
                intent_type="follow", raw_text="跟我走", confidence=0.9,
            )

        engine.intent_resolver.resolve = forced
        result = engine.talk("npc_2", "跟我走")
        self.assertEqual(result["resolved_intent"]["target_id"], "npc_2")
        self.assertTrue(engine.world.characters_state["npc_2"].get("following_player"))
        self.assertIn("follow player", engine.world.get_npc("npc_2").goals)
        self.assertFalse(engine.world.characters_state["npc_1"].get("following_player"))
        self.assertNotIn("follow player", engine.world.get_npc("npc_1").goals)

    def test_story_move_npc_cannot_teleport_active_escort(self):
        from event import execute_event

        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        ren.goals = ["find ledger", "lead_player_to:mayor_house"]
        engine.end_dialogue_session()
        for _ in range(6):
            engine.tick()
        before = engine.world.npc_position("npc_2")
        self.assertGreater(
            engine.world.distance_between(before, (70.0, 70.0)), 2.0
        )

        execute_event(engine.world, {
            "event_id": "teleport_during_escort",
            "time": engine.world.time,
            "effects": [
                {"type": "move_npc", "npc_id": "npc_2", "x": 70.0, "y": 70.0},
            ],
        })

        after = engine.world.npc_position("npc_2")
        self.assertEqual(after, before)
        diverged = [
            memory for memory in ren.memory
            if "diverged" in str(memory.get("content", ""))
        ]
        self.assertTrue(diverged)

    def test_story_move_npc_still_teleports_when_no_escort(self):
        from event import execute_event

        engine = runtime_engine()
        npc = engine.world.get_npc("npc_3")
        npc.goals = ["help Ren"]
        npc.position_override = [50.0, 30.0]

        execute_event(engine.world, {
            "event_id": "teleport_without_escort",
            "time": engine.world.time,
            "effects": [
                {"type": "move_npc", "npc_id": "npc_3", "x": 10.0, "y": 90.0},
            ],
        })

        self.assertEqual(engine.world.npc_position("npc_3"), (10.0, 90.0))

    def test_preset_ren_seeks_evidence_does_not_reset_escort_route(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        ren.goals = ["find ledger", "lead_player_to:mayor_house"]
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        engine.end_dialogue_session()

        positions = []
        for _ in range(33):
            engine.tick()
            positions.append(engine.world.npc_position("npc_2"))

        # A teleport would create one >2.0 jump. Escort movement is 2.0/tick.
        max_step = max(
            engine.world.distance_between(positions[i - 1], positions[i])
            for i in range(1, len(positions))
        )
        self.assertLessEqual(max_step, 2.001)
        resolved = {
            entry.get("event_id"): entry.get("final_status")
            for entry in engine.world.story_event_history
        }
        # The preset teleport was re-planned into an adapted beat rather than
        # executed as a hard move.
        self.assertEqual(resolved.get("ren_seeks_evidence__adapted"), "modified")
        original = engine.world.story_timeline.get_event("ren_seeks_evidence")
        self.assertEqual(original.get("status"), "modified")
        self.assertGreater(
            engine.world.distance_between(positions[-1], (70.0, 70.0)), 20.0
        )

    def test_escorting_ren_prevents_ren_lost(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        engine.world.player.x, engine.world.player.y = 40.0, 40.0
        ren.goals = ["lead_player_to:mayor_house"]
        engine.tick_seconds = 8 * 3600
        for _ in range(14):
            engine.world.player.x, engine.world.player.y = engine.world.npc_position("npc_2")
            engine.tick()
        statuses = {entry["id"]: entry["status"] for entry in engine.world.story_timeline}
        self.assertEqual(statuses["ren_lost"], "cancelled")
        self.assertTrue(engine.world.characters_state["npc_2"]["alive"])

    def test_director_replans_event_that_contradicts_escort(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        ren.goals = ["find ledger", "lead_player_to:mayor_house"]
        engine.end_dialogue_session()
        # Advance through the preset ren_seeks_evidence event (fires at 32h).
        for _ in range(32):
            engine.tick()

        statuses = {
            (
                entry.get("event_id"),
                entry.get("final_status"),
            )
            for entry in engine.world.story_event_history
        }
        self.assertIn(("ren_seeks_evidence__adapted", "modified"), statuses)
        adapted = [
            entry for entry in engine.world.story_timeline
            if entry.get("event_id") == "ren_seeks_evidence__adapted"
        ]
        self.assertTrue(adapted)
        moves = [
            effect for effect in adapted[0].get("effects", [])
            if isinstance(effect, dict) and effect.get("type") == "move_npc"
        ]
        self.assertEqual(moves, [])
        self.assertIn("lead_player_to:mayor_house", ren.goals)

    def test_ren_lost_still_triggers_without_escort(self):
        engine = runtime_engine()
        engine.world.get_npc("npc_2").position_override = [40.0, 40.0]
        engine.tick_seconds = 8 * 3600
        for _ in range(14):
            engine.tick()
        statuses = {entry["id"]: entry["status"] for entry in engine.world.story_timeline}
        self.assertEqual(statuses["ren_lost"], "triggered")
        self.assertFalse(engine.world.characters_state["npc_2"]["alive"])

    def test_web_engine_defaults_to_llm_goal_provider(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "test-key"}, clear=False):
            engine = OpenWorldEngine()
        self.assertIsInstance(engine.npc_goal_provider, LLMNPCGoalProvider)
        with patch.dict(os.environ, {"LLM_API_KEY": ""}, clear=False):
            offline = OpenWorldEngine()
        self.assertIsNone(offline.npc_goal_provider)


if __name__ == "__main__":
    unittest.main()
