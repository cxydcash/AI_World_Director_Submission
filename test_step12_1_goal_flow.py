# -*- coding: utf-8 -*-
"""Step 12.1 acceptance tests: unified NPC Goal generation data flow.

Player Dialogue -> DirectorInput -> AI Director -> GoalChange
    -> GoalManager -> Event DSL -> WorldState -> NPC Planner
"""

import unittest
from unittest.mock import patch

from director_input import (
    GOAL_CHANGE_ADD,
    GOAL_CHANGE_NONE,
    GOAL_CHANGE_REMOVE,
    SOURCE_NPC_OBSERVATION,
    SOURCE_PLAYER_DIALOGUE,
    DirectorInput,
    GoalChange,
)
from goal_manager import GoalManager
from npc_action_decision import FakeNPCGoalProvider
from open_world import OpenWorldDirector, OpenWorldEngine


LEAD_TO_MAYOR_HOUSE = {
    "action_required": True,
    "goal_type": "lead_player_to",
    "target": "mayor_house",
    "reason": "玩家希望我带他去村长家",
    "confidence": 0.9,
}

SMALLTALK = {
    "action_required": False,
    "reason": "只是闲聊",
}


def offline_engine(decision=None, provider=None):
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.tick_seconds = 3600.0
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    if provider is not None:
        engine.npc_goal_provider = provider
    elif decision is not None:
        engine.npc_goal_provider = FakeNPCGoalProvider(decision)
    return engine


class DirectorInputContractTests(unittest.TestCase):
    def test_director_input_has_required_fields(self):
        di = DirectorInput(
            source=SOURCE_PLAYER_DIALOGUE,
            actor_id="player",
            target_id="npc_2",
            text="你能带我去村长家看看吗？",
            payload={"note": "x"},
            context={"time": 0.0},
            time=0.0,
        )
        payload = di.to_dict()
        for field in ("source", "actor_id", "target_id", "text", "payload", "context", "time"):
            self.assertIn(field, payload)
        self.assertEqual(payload["source"], "player_dialogue")

    def test_director_accepts_future_sources(self):
        engine = offline_engine(provider=FakeNPCGoalProvider(LEAD_TO_MAYOR_HOUSE))
        future = DirectorInput(
            source=SOURCE_NPC_OBSERVATION,
            actor_id="npc_2",
            target_id="npc_2",
            text="",
            time=engine.world.time,
        )
        # Not implemented yet, but the interface must accept it and decide
        # "no change" instead of crashing.
        self.assertEqual(engine.director.decide_goals(engine.world, future), [])

        dialogue = DirectorInput(
            source=SOURCE_PLAYER_DIALOGUE,
            actor_id="player",
            target_id="npc_2",
            text="你能带我去村长家看看吗？",
            time=engine.world.time,
        )
        changes = engine.director.decide_goals(engine.world, dialogue)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].change_type, GOAL_CHANGE_ADD)
        self.assertEqual(changes[0].goal_type, "lead_player_to")


class GoalManagerTests(unittest.TestCase):
    def test_none_add_remove(self):
        engine = offline_engine()
        world = engine.world
        ren = world.get_npc("npc_2")
        manager = GoalManager()

        none_result = manager.apply(world, GoalChange(change_type=GOAL_CHANGE_NONE))
        self.assertFalse(none_result["applied"])

        add = GoalChange(
            change_type=GOAL_CHANGE_ADD, npc_id="npc_2",
            goal_type="reach_location", target="forest",
        )
        add_result = manager.apply(world, add)
        self.assertTrue(add_result["applied"])
        self.assertIn("reach_location:forest", ren.goals)

        remove = GoalChange(
            change_type=GOAL_CHANGE_REMOVE, npc_id="npc_2",
            goal_type="reach_location", target="forest",
        )
        remove_result = manager.apply(world, remove)
        self.assertTrue(remove_result["applied"])
        self.assertNotIn("reach_location:forest", ren.goals)

    def test_goal_manager_does_not_decide(self):
        manager = GoalManager()
        self.assertFalse(hasattr(manager, "decide_goals"))
        self.assertFalse(hasattr(manager, "plan_next_action"))


class DialogueToGoalFlowTests(unittest.TestCase):
    def test_smalltalk_produces_no_goal(self):
        engine = offline_engine(provider=FakeNPCGoalProvider(SMALLTALK))
        before = list(engine.world.get_npc("npc_2").goals)
        result = engine.talk("npc_2", "你最近怎么样？")
        decision = result["npc_goal_decision"]
        self.assertFalse(decision["action_required"])
        self.assertIsNone(decision["goal"])
        self.assertEqual(list(engine.world.get_npc("npc_2").goals), before)
        self.assertNotIn("wait", engine.world.get_npc("npc_2").goals)

    def test_explicit_request_produces_lead_goal(self):
        engine = offline_engine(provider=FakeNPCGoalProvider(LEAD_TO_MAYOR_HOUSE))
        result = engine.talk("npc_2", "你能带我去村长家看看吗？")
        decision = result["npc_goal_decision"]
        self.assertTrue(decision["action_required"])
        self.assertEqual(decision["goal"]["goal_type"], "lead_player_to")
        self.assertIn("lead_player_to:mayor_house", engine.world.get_npc("npc_2").goals)

    def test_dialogue_does_not_call_apply_npc_goal_directly(self):
        engine = offline_engine(provider=FakeNPCGoalProvider(LEAD_TO_MAYOR_HOUSE))
        applied = []
        director_inputs = []
        real_apply = GoalManager.apply
        real_decide = OpenWorldDirector.decide_goals

        def spy_apply(self, world, change):
            applied.append(change)
            return real_apply(self, world, change)

        def spy_decide(self, world, director_input):
            director_inputs.append(director_input)
            return real_decide(self, world, director_input)

        with patch("npc_action_decision.apply_npc_goal") as legacy_apply, \
                patch.object(GoalManager, "apply", spy_apply), \
                patch.object(OpenWorldDirector, "decide_goals", spy_decide):
            result = engine.talk("npc_2", "你能带我去村长家看看吗？")

        legacy_apply.assert_not_called()
        self.assertTrue(director_inputs)
        self.assertEqual(director_inputs[0].source, SOURCE_PLAYER_DIALOGUE)
        self.assertTrue(applied)
        self.assertEqual(
            result["npc_goal_decision"]["goal"]["goal_type"], "lead_player_to"
        )

    def test_goal_is_written_through_goal_manager_event_dsl(self):
        engine = offline_engine(provider=FakeNPCGoalProvider(LEAD_TO_MAYOR_HOUSE))
        engine.talk("npc_2", "你能带我去村长家看看吗？")
        decision = engine.last_npc_goal_decision
        self.assertTrue(decision["goal_manager"]["applied"])
        effects = engine.world.event_history[-1]["effects"]
        self.assertEqual(effects[0]["type"], "add_goal")
        self.assertEqual(effects[0]["goal"], "lead_player_to:mayor_house")

    def test_goal_reaches_existing_planner(self):
        engine = offline_engine(provider=FakeNPCGoalProvider(LEAD_TO_MAYOR_HOUSE))
        engine.talk("npc_2", "你能带我去村长家看看吗？")
        ren = engine.world.get_npc("npc_2")
        self.assertIn("lead_player_to:mayor_house", ren.goals)
        action = engine.npc_planner.plan_next_action(engine.world, "npc_2")
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 20.0, "y": 20.0})

    def test_follow_question_does_not_create_goal(self):
        provider = FakeNPCGoalProvider({
            "action_required": True, "goal_type": "follow_player",
            "target": "player", "reason": "player asked", "confidence": 0.9,
        })
        engine = offline_engine(provider=provider)
        result = engine.talk("npc_2", "你跟着我吗？")
        decision = result["npc_goal_decision"]
        self.assertFalse(decision["action_required"])
        self.assertIsNone(decision["goal"])
        self.assertNotIn("follow player", engine.world.get_npc("npc_2").goals)


class StoryEventCompatibilityTests(unittest.TestCase):
    def test_story_event_add_goal_path_still_works(self):
        engine = offline_engine()
        world = engine.world
        world.story_timeline.add_event({
            "event_id": "step121_story_goal",
            "title": "Step 12.1 compatibility",
            "description": "StoryEvent add_goal must keep working.",
            "trigger_at": float(world.time),
            "effects": [
                {"type": "add_goal", "npc_id": "npc_3", "goal": "reach_location:forest"},
            ],
        })
        world.check_story_events()
        self.assertIn("reach_location:forest", world.get_npc("npc_3").goals)


if __name__ == "__main__":
    unittest.main()
