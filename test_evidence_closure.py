"""Step 10 tests: Evidence -> Memory -> Belief -> Goal closure.

StoryEvent --add_evidence--> WorldState.evidence
NPC --search/investigate--> Action Layer --> memory + belief (+ goal)
"""

import unittest

from action_layer import Action, ActionExecutor, ActionValidator
from event import EventValidationError, execute_event
from evidence import discover
from npc_planner import NPCPlanner
from open_world import OpenWorldEngine, create_open_world


def add_evidence_event(**overrides):
    effect = {
        "type": "add_evidence",
        "evidence_id": "ev_ledger_wrapping",
        "evidence_type": "ledger_wrapping",
        "x": 72.0,
        "y": 70.0,
        "description": "A torn wrapper from the hidden ledger.",
        "linked_belief": "saw_ledger_wrapping",
        "belief_delta": 0.2,
        "follow_up_goal": "investigate:mayor",
    }
    effect.update(overrides)
    return {"event_id": "evt_wrap", "time": 0.0, "effects": [effect]}


def fresh_world():
    world = create_open_world()
    world.object_state = {}
    return world


class StoryEventEvidenceTests(unittest.TestCase):
    def test_story_event_writes_evidence_to_world_state(self):
        world = fresh_world()
        execute_event(world, add_evidence_event())

        entry = next(e for e in world.evidence if e.get("id") == "ev_ledger_wrapping")
        self.assertEqual(entry["type"], "ledger_wrapping")
        self.assertEqual(entry["location"], [72.0, 70.0])
        self.assertEqual(entry["description"], "A torn wrapper from the hidden ledger.")
        self.assertEqual(entry["source_event"], "evt_wrap")
        self.assertEqual(entry["created_at"], 0.0)

    def test_evidence_ids_must_be_unique(self):
        world = fresh_world()
        execute_event(world, add_evidence_event())
        with self.assertRaises(EventValidationError):
            execute_event(world, {**add_evidence_event(), "event_id": "evt_wrap_2"})

    def test_evidence_effect_requires_a_description(self):
        world = fresh_world()
        with self.assertRaises(EventValidationError):
            execute_event(world, add_evidence_event(description=""))


class EvidenceIsolationTests(unittest.TestCase):
    def test_planner_observation_never_exposes_far_evidence(self):
        world = fresh_world()
        ren = world.get_npc("npc_2")
        ren.position_override = [10.0, 10.0]
        execute_event(world, add_evidence_event())

        observation = NPCPlanner()._observe(world, ren)
        self.assertEqual(observation["evidence"], [])

    def test_far_npc_cannot_discover_evidence(self):
        world = fresh_world()
        ren = world.get_npc("npc_2")
        ren.position_override = [10.0, 10.0]
        execute_event(world, add_evidence_event())

        action = Action("investigate", actor_id="npc_2", target_id="__location__")
        result = ActionExecutor().execute(world, action)

        self.assertTrue(result.success)
        self.assertFalse(result.data.get("evidence_id"))
        self.assertFalse(any(m.get("evidence_id") == "ev_ledger_wrapping" for m in ren.memory))
        self.assertNotIn("saw_ledger_wrapping", ren.beliefs)


class EvidenceDiscoveryTests(unittest.TestCase):
    def world_with_ren(self, vision=5.0, evidence_x=72.0):
        world = fresh_world()
        ren = world.get_npc("npc_2")
        ren.vision_range = vision
        ren.position_override = [70.0, 70.0]
        ren.goals = ["investigate:unknown_rumor"]
        execute_event(world, add_evidence_event(x=evidence_x))
        return world, ren

    def test_search_discovers_evidence_just_outside_vision(self):
        # Distance 7 is outside vision 5 but inside the search reach (5 + 3).
        world, ren = self.world_with_ren(vision=5.0, evidence_x=77.0)

        action = NPCPlanner().plan_next_action(world, "npc_2")
        self.assertEqual(action.action_type, "search")

        validation = ActionValidator().validate(world, action)
        result = ActionExecutor().execute(world, action, validation=validation)

        self.assertTrue(result.success)
        self.assertTrue(result.data.get("evidence_discovered"))
        self.assertTrue(any(m.get("evidence_id") == "ev_ledger_wrapping" for m in ren.memory))

    def test_investigate_discovers_visible_evidence(self):
        world, ren = self.world_with_ren(vision=8.0)

        action = NPCPlanner().plan_next_action(world, "npc_2")
        self.assertEqual(action.action_type, "investigate")
        self.assertEqual(action.target_id, "ev_ledger_wrapping")

        validation = ActionValidator().validate(world, action)
        result = ActionExecutor().execute(world, action, validation=validation)

        self.assertTrue(result.success)
        self.assertFalse(result.data.get("already_known"))

    def test_discovery_produces_memory_belief_and_goal(self):
        world, ren = self.world_with_ren(vision=8.0)
        action = NPCPlanner().plan_next_action(world, "npc_2")
        ActionExecutor().execute(world, action, validation=ActionValidator().validate(world, action))

        memories = [m for m in ren.memory if m.get("evidence_id") == "ev_ledger_wrapping"]
        self.assertEqual(len(memories), 1)
        self.assertIn("ledger_wrapping", memories[0]["content"])
        self.assertAlmostEqual(ren.beliefs["saw_ledger_wrapping"], 0.2)
        self.assertIn("investigate:mayor", ren.goals)

    def test_discovery_is_idempotent_per_npc(self):
        world, ren = self.world_with_ren(vision=8.0)
        entry = next(e for e in world.evidence if e.get("id") == "ev_ledger_wrapping")

        first = discover(world, ren, entry)
        second = discover(world, ren, entry)

        self.assertFalse(first["already_known"])
        self.assertTrue(second["already_known"])
        self.assertEqual(
            len([m for m in ren.memory if m.get("evidence_id") == "ev_ledger_wrapping"]), 1
        )
        self.assertEqual(ren.beliefs["saw_ledger_wrapping"], 0.2)


class FullChainTests(unittest.TestCase):
    def test_engine_tick_runs_the_closure_and_continues(self):
        engine = OpenWorldEngine()
        engine.director.api_key = ""
        engine.director.provider = None
        engine.tick_seconds = 3600.0
        ren = engine.world.get_npc("npc_2")
        ren.vision_range = 8.0
        ren.position_override = [70.0, 70.0]
        ren.goals = ["investigate:unknown_rumor"]
        execute_event(
            engine.world,
            add_evidence_event(follow_up_goal="reach_location:forest"),
        )

        engine.tick()

        self.assertTrue(any(m.get("evidence_id") == "ev_ledger_wrapping" for m in ren.memory))
        self.assertEqual(ren.beliefs["saw_ledger_wrapping"], 0.2)
        self.assertEqual(ren.goal_states["investigate:unknown_rumor"]["status"], "completed")
        self.assertIn("reach_location:forest", ren.goals)

        # The follow-up goal must actually be picked up on the next tick.
        engine.tick()
        self.assertEqual(ren.action_progress.get("goal_type"), "reach_location")


if __name__ == "__main__":
    unittest.main()

