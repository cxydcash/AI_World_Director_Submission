# -*- coding: utf-8 -*-
"""Step 12.2 acceptance tests: close every direct NPC Goal write bypass.

Every new NPC Goal must flow:
    World Information / Event -> DirectorInput -> AI Director -> GoalChange
        -> GoalManager -> Event DSL -> WorldState -> NPC Planner
"""

import inspect
import unittest
from unittest.mock import patch

from action_layer import Action, ActionExecutor, ActionValidator
from director_input import (
    SOURCE_NPC_DISCOVERY,
    SOURCE_PLAYER_ACTION,
    SOURCE_PLAYER_DIALOGUE,
    SOURCE_WORLD_EVENT,
    DirectorInput,
)
from entities import NPC
from event import execute_event
from evidence import discover
from npc_action_decision import FakeNPCGoalProvider
from npc_planner import NPCPlanner
from open_world import OpenWorldDirector, OpenWorldEngine, create_open_world


LEAD = {
    "action_required": True,
    "goal_type": "lead_player_to",
    "target": "mayor_house",
    "reason": "player asked",
    "confidence": 0.9,
}


def offline_engine(provider=None):
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    engine.tick_seconds = 3600.0
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    if provider is not None:
        engine.npc_goal_provider = provider
    return engine


def evidence_event(follow_up_goal="investigate:mayor", evidence_x=70.0):
    effect = {
        "type": "add_evidence",
        "evidence_id": "ev_122_trace",
        "evidence_type": "trace",
        "x": evidence_x,
        "y": 70.0,
        "description": "A trace near Ren.",
        "linked_belief": "saw_trace",
        "belief_delta": 0.2,
    }
    if follow_up_goal is not None:
        effect["follow_up_goal"] = follow_up_goal
    return {"event_id": "evt_122", "time": 0.0, "effects": [effect]}


class DiscoveryClosureTests(unittest.TestCase):
    def test_discover_alone_does_not_write_goal(self):
        world = create_open_world()
        ren = world.get_npc("npc_2")
        entry = {
            "id": "ev_alone", "type": "trace", "location": [70.0, 70.0],
            "description": "trace", "follow_up_goal": "investigate:mayor",
        }
        before = list(ren.goals)
        summary = discover(world, ren, entry)
        # The suggestion is returned as information...
        self.assertEqual(summary["follow_up_goal"], "investigate:mayor")
        # ...but discover() must not write a goal by itself.
        self.assertNotIn("investigate:mayor", ren.goals)
        self.assertEqual(list(ren.goals), before)
        self.assertFalse(summary["goal_added"])

    def test_discovery_produces_goal_through_director(self):
        world = create_open_world()
        ren = world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        ren.vision_range = 8.0
        ren.goals = ["investigate:unknown_rumor"]
        execute_event(world, evidence_event(follow_up_goal="investigate:mayor"))

        action = NPCPlanner().plan_next_action(world, "npc_2")
        self.assertEqual(action.action_type, "investigate")
        validation = ActionValidator().validate(world, action)
        result = ActionExecutor().execute(world, action, validation=validation)

        self.assertTrue(result.data.get("evidence_discovered") or result.data.get("evidence_id"))
        self.assertIn("investigate:mayor", ren.goals)
        self.assertTrue(result.data.get("goal_added"))
        self.assertEqual(result.data.get("goal_decided_by"), "ai_director")

    def test_discovery_does_not_accept_goal_when_no_suggestion(self):
        world = create_open_world()
        ren = world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        ren.vision_range = 8.0
        ren.goals = ["investigate:unknown_rumor"]
        execute_event(world, evidence_event(follow_up_goal=None))

        action = NPCPlanner().plan_next_action(world, "npc_2")
        ActionExecutor().execute(
            world, action, validation=ActionValidator().validate(world, action)
        )
        self.assertNotIn("investigate:mayor", ren.goals)


class TakeLedgerClosureTests(unittest.TestCase):
    def test_take_ledger_goal_goes_through_director(self):
        engine = offline_engine()
        engine.world.player.x, engine.world.player.y = 22.0, 22.0
        sources = []
        real = OpenWorldDirector.decide_goals

        def spy(self, world, director_input):
            sources.append(director_input.source)
            return real(self, world, director_input)

        with patch.object(OpenWorldDirector, "decide_goals", spy):
            result = engine.take_ledger()

        self.assertTrue(result["success"])
        self.assertIn(SOURCE_PLAYER_ACTION, sources)
        self.assertEqual(engine.world.get_npc("npc_1").beliefs["ledger_missing"], 1.0)
        self.assertIn("recover ledger", engine.world.get_npc("npc_1").goals)
        # The write still lands through the Event DSL.
        self.assertEqual(engine.world.event_history[-1]["effects"][0]["type"], "add_goal")


class CommitmentClosureTests(unittest.TestCase):
    def test_commitment_wait_routes_through_director(self):
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        sources = []
        real = OpenWorldDirector.decide_goals

        def spy(self, world, director_input):
            sources.append(director_input.source)
            return real(self, world, director_input)

        with patch.object(OpenWorldDirector, "decide_goals", spy):
            engine.commitments._add_wait_goal(engine.world, ren)

        self.assertIn(SOURCE_WORLD_EVENT, sources)
        self.assertIn("wait", ren.goals)

    def test_commitment_does_not_write_goal_without_director(self):
        from commitments import CommitmentRegistry
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        standalone = CommitmentRegistry()  # no goal router
        standalone._add_wait_goal(engine.world, ren)
        self.assertNotIn("wait", ren.goals)

    def test_commitment_release_routes_through_director(self):
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        ren.add_goal("wait")
        sources = []
        real = OpenWorldDirector.decide_goals

        def spy(self, world, director_input):
            sources.append(director_input.source)
            return real(self, world, director_input)

        with patch.object(OpenWorldDirector, "decide_goals", spy):
            engine.commitments._drop_goal(engine.world, ren, {"goal_string": "wait"})

        self.assertIn(SOURCE_WORLD_EVENT, sources)
        self.assertNotIn("wait", ren.goals)


class FollowClosureTests(unittest.TestCase):
    def test_follow_goal_goes_through_director(self):
        engine = offline_engine()
        sources = []
        real = OpenWorldDirector.decide_goals

        def spy(self, world, director_input):
            sources.append(director_input.source)
            return real(self, world, director_input)

        with patch.object(OpenWorldDirector, "decide_goals", spy):
            engine._set_follow_state("npc_2", True)

        self.assertIn(SOURCE_PLAYER_ACTION, sources)
        self.assertIn("follow player", engine.world.get_npc("npc_2").goals)
        self.assertTrue(engine.world.characters_state["npc_2"]["following_player"])

        with patch.object(OpenWorldDirector, "decide_goals", spy):
            engine._set_follow_state("npc_2", False)
        self.assertNotIn("follow player", engine.world.get_npc("npc_2").goals)

    def test_npc_follow_player_action_still_works(self):
        engine = offline_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [69.0, 70.0]
        result = engine.npc_follow_player("npc_2")
        self.assertTrue(result["success"])
        self.assertIn("follow player", ren.goals)
        self.assertTrue(engine.world.characters_state["npc_2"]["following_player"])


class StoryTimelineCompatibilityTests(unittest.TestCase):
    def test_story_event_add_goal_still_works(self):
        engine = offline_engine()
        world = engine.world
        world.story_timeline.add_event({
            "event_id": "step122_story_goal",
            "title": "Step 12.2 compatibility",
            "description": "StoryEvent add_goal must keep working.",
            "trigger_at": float(world.time),
            "effects": [
                {"type": "add_goal", "npc_id": "npc_3", "goal": "reach_location:forest"},
            ],
        })
        world.check_story_events()
        self.assertIn("reach_location:forest", world.get_npc("npc_3").goals)

    def test_timeline_change_goal_goes_through_manager(self):
        from scenario import apply_timeline_change
        engine = offline_engine()
        world = engine.world
        apply_timeline_change(world, {
            "type": "timeline_change",
            "changes": [],
            "npc_changes": [
                {"npc_id": "npc_3", "goal": "reach_location:forest"},
            ],
        })
        self.assertIn("reach_location:forest", world.get_npc("npc_3").goals)


class NoDirectGoalWriteArchitectureTests(unittest.TestCase):
    """Scan every entry point: no call may bypass the Event DSL executor."""

    def _run_all_entry_points(self, engine):
        # 1) player dialogue -> director -> goal manager
        engine.npc_goal_provider = FakeNPCGoalProvider(LEAD)
        engine.talk("npc_2", "你能带我去村长家看看吗？")

        # 2) player action: take_ledger
        engine.world.player.x, engine.world.player.y = 22.0, 22.0
        engine.take_ledger()

        # 3) follow add/remove
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
        engine._set_follow_state("npc_2", True)
        engine._set_follow_state("npc_2", False)

        # 4) commitment wait via world_event
        ren = engine.world.get_npc("npc_2")
        engine.decide_and_apply_goals(DirectorInput(
            source=SOURCE_WORLD_EVENT, actor_id="npc_2", target_id="npc_2",
            payload={"kind": "commitment_wait"}, time=engine.world.time,
        ))

        # 5) npc discovery -> director -> goal manager
        execute_event(engine.world, evidence_event(follow_up_goal="investigate:mayor"))
        ren.vision_range = 8.0
        ren.position_override = [70.0, 70.0]
        ren.goals = ["investigate:unknown_rumor"]
        action = NPCPlanner().plan_next_action(engine.world, "npc_2")
        ActionExecutor().execute(
            engine.world, action, validation=ActionValidator().validate(engine.world, action),
            runtime=engine,
        )

    def test_no_entry_point_writes_npc_goal_directly(self):
        engine = offline_engine()
        records = []
        real_add_goal = NPC.add_goal

        def spy_add_goal(self, goal):
            stack = [frame.function for frame in inspect.stack()]
            records.append((self.id, str(goal), stack))
            return real_add_goal(self, goal)

        with patch.object(NPC, "add_goal", spy_add_goal):
            self._run_all_entry_points(engine)

        self.assertTrue(records, "expected at least one goal write")
        allowed_frames = {"_execute_add_goal", "_execute_set_follow_target"}
        for npc_id, goal, stack in records:
            self.assertTrue(
                allowed_frames & set(stack),
                f"direct goal write detected: npc={npc_id} goal={goal}\nstack={stack}",
            )

    def test_world_add_goal_only_called_from_event_dsl(self):
        import world as world_module
        engine = offline_engine()
        records = []
        real_world_add_goal = world_module.WorldState.add_goal

        def spy_world_add_goal(self, npc_id, goal):
            stack = [frame.function for frame in inspect.stack()]
            records.append((npc_id, str(goal), stack))
            return real_world_add_goal(self, npc_id, goal)

        with patch.object(world_module.WorldState, "add_goal", spy_world_add_goal):
            self._run_all_entry_points(engine)

        self.assertTrue(records, "expected at least one world goal write")
        for npc_id, goal, stack in records:
            self.assertIn(
                "_execute_add_goal", stack,
                f"world.add_goal bypassed the Event DSL: npc={npc_id} goal={goal}",
            )


if __name__ == "__main__":
    unittest.main()
