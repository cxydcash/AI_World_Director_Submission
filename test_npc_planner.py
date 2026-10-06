"""Tests for the rule-based NPC Planner."""

import unittest

from action_layer import ActionExecutor, ActionValidator, ExecutionResult
from npc_planner import NPCPlanner
from open_world import OpenWorldEngine, create_open_world


class NPCPlannerTests(unittest.TestCase):
    def setUp(self):
        self.world = create_open_world()
        self.world.object_state = {}
        self.planner = NPCPlanner()

    def set_goal(self, npc_id, goal):
        self.world.get_npc(npc_id).goals = [goal]

    def test_reach_location_generates_move(self):
        npc = self.world.get_npc("npc_2")
        npc.position_override = [20.0, 20.0]
        npc.goals = [{"goal_type": "reach_location", "target_id": "forest"}]
        action = self.planner.plan_next_action(self.world, npc.id)
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 40.0, "y": 40.0})

    def test_reach_location_stops_when_arrived(self):
        npc = self.world.get_npc("npc_2")
        npc.position_override = [40.0, 40.0]
        npc.goals = [{"goal_type": "reach_location", "target_id": "forest"}]
        self.assertIsNone(self.planner.plan_next_action(self.world, npc.id))

    def test_find_item_with_known_location_generates_move(self):
        npc = self.world.get_npc("npc_2")
        npc.position_override = [20.0, 20.0]
        npc.goals = [{"goal_type": "find_item", "target_id": "ledger"}]
        # The ledger must not be inside the NPC's vision, otherwise the planner
        # would (correctly) take it instead of travelling to the known location.
        ledger = self.world.get_item("item_ledger")
        ledger.x, ledger.y = 70.0, 70.0
        npc.memory.append({"time": 0, "content": "The ledger is in the town office."})
        action = self.planner.plan_next_action(self.world, npc.id)
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 70.0, "y": 70.0})

    def test_find_item_nearby_generates_take(self):
        npc = self.world.get_npc("npc_2")
        npc.position_override = [21.0, 20.0]
        self.world.get_item("item_ledger").x = 22.0
        self.world.get_item("item_ledger").y = 20.0
        npc.goals = [{"goal_type": "find_item", "target_id": "ledger"}]
        action = self.planner.plan_next_action(self.world, npc.id)
        self.assertEqual(action.action_type, "take")
        self.assertEqual(action.target_id, "item_ledger")

    def test_find_item_completed_when_npc_holds_it(self):
        npc = self.world.get_npc("npc_2")
        npc.goals = [{"goal_type": "find_item", "target_id": "ledger"}]
        self.world.get_item("item_ledger").holder = npc.id
        self.assertIsNone(self.planner.plan_next_action(self.world, npc.id))

    def test_unknown_item_location_does_not_read_world_truth(self):
        npc = self.world.get_npc("npc_2")
        npc.position_override = [20.0, 20.0]
        npc.goals = [{"goal_type": "find_item", "target_id": "ledger"}]
        self.world.get_item("item_ledger").x = 90.0
        self.world.get_item("item_ledger").y = 90.0
        action = self.planner.plan_next_action(self.world, npc.id)
        self.assertEqual(action.action_type, "search")
        self.assertEqual(action.target_id, "__location__")

    def test_engine_updates_memory_after_npc_take(self):
        engine = OpenWorldEngine()
        npc = engine.world.get_npc("npc_2")
        npc.position_override = [21.0, 20.0]
        npc.goals = [{"goal_type": "find_item", "target_id": "ledger"}]
        item = engine.world.get_item("item_ledger")
        item.x, item.y, item.holder = 22.0, 20.0, None
        engine._update_npcs_with_planner()
        self.assertEqual(item.holder, "npc_2")
        self.assertTrue(any("Took" in str(memory) for memory in npc.memory))

    def test_planner_does_not_mutate_world(self):
        npc = self.world.get_npc("npc_2")
        npc.goals = [{"goal_type": "reach_location", "target_id": "forest"}]
        before = (list(npc.goals), list(npc.memory), self.world.time)
        self.planner.plan_next_action(self.world, npc.id)
        after = (list(npc.goals), list(npc.memory), self.world.time)
        self.assertEqual(before, after)

    def test_npc_action_executes_through_validator_and_executor(self):
        npc = self.world.get_npc("npc_2")
        npc.position_override = [40.0, 40.0]
        action = self.planner._move_action(npc, (50.0, 40.0), "reach_location", {"goal_type": "reach_location"})
        validator = ActionValidator()
        executor = ActionExecutor(validator)
        validation = validator.validate(self.world, action)
        self.assertTrue(validation.valid)
        result = executor.execute(self.world, action, validation=validation)
        self.assertTrue(result.success)
        self.assertLessEqual(self.world.distance_between((40.0, 40.0), self.world.npc_position(npc.id)), 2.0)

    def test_follow_goal_generates_move_toward_player(self):
        npc = self.world.get_npc("npc_2")
        npc.position_override = [30.0, 30.0]
        npc.goals = [{"goal_type": "follow_npc", "target_id": "player"}]
        self.world.player.x, self.world.player.y = 50.0, 30.0
        action = self.planner.plan_next_action(self.world, npc.id)
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 50.0, "y": 30.0})

    def test_other_npcs_do_not_follow_when_one_npc_follows(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        other = engine.world.get_npc("npc_3")
        ren.position_override = [21.0, 20.0]
        other.position_override = [50.0, 30.0]
        ren.goals = [{"goal_type": "follow_npc", "target_id": "player"}]
        engine.world.player.x, engine.world.player.y = 30.0, 20.0
        before_other = engine.world.npc_position(other.id)
        engine._update_npcs_with_planner()
        self.assertNotEqual(engine.world.npc_position(ren.id), (21.0, 20.0))
        self.assertEqual(engine.world.npc_position(other.id), before_other)

    def test_schedule_and_goal_can_coexist(self):
        npc = self.world.get_npc("npc_3")
        # At world time 0 the schedule says "home" (20, 20); start away from it
        # so the schedule fallback has somewhere to move to.
        npc.position_override = [80.0, 80.0]
        npc.goals = ["unknown_goal"]
        action = self.planner.plan_next_action(self.world, npc.id)
        self.assertIsNotNone(action)
        self.assertEqual(action.action_type, "move")

    def test_npc_goal_can_be_removed_after_take_result(self):
        engine = OpenWorldEngine()
        npc = engine.world.get_npc("npc_2")
        npc.position_override = [21.0, 20.0]
        npc.goals = [{"goal_type": "find_item", "target_id": "ledger"}]
        item = engine.world.get_item("item_ledger")
        item.x, item.y, item.holder = 22.0, 20.0, None
        engine._update_npcs_with_planner()
        self.assertFalse(npc.goals)


    def test_planner_switches_to_take_once_item_is_in_view(self):
        npc = self.world.get_npc("npc_2")
        npc.position_override = [20.0, 20.0]
        npc.goals = [{"goal_type": "find_item", "target_id": "ledger"}]
        ledger = self.world.get_item("item_ledger")
        ledger.x, ledger.y = 70.0, 70.0
        npc.memory.append({"time": 0, "content": "The ledger is in the town office."})
        first = self.planner.plan_next_action(self.world, npc.id)
        self.assertEqual(first.action_type, "move")
        npc.position_override = [70.0, 70.0]
        second = self.planner.plan_next_action(self.world, npc.id)
        self.assertEqual(second.action_type, "take")

    def test_npc_belief_can_redirect_planner_to_search(self):
        npc = self.world.get_npc("npc_2")
        npc.position_override = [20.0, 20.0]
        npc.goals = [{"goal_type": "find_item", "target_id": "ledger"}]
        ledger = self.world.get_item("item_ledger")
        ledger.x, ledger.y = 70.0, 70.0
        npc.memory.append({"time": 0, "content": "The ledger is in the town office."})
        npc.set_belief("ledger_missing", 0.8)
        action = self.planner.plan_next_action(self.world, npc.id)
        self.assertEqual(action.action_type, "search")
        self.assertEqual(action.reason, "believes item is missing")

    def test_npc_move_advances_with_world_time(self):
        engine = OpenWorldEngine()
        engine.tick_seconds = 3600.0
        before_time = engine.world.time
        before_position = engine.world.npc_position("npc_4")
        engine.tick()
        after_position = engine.world.npc_position("npc_4")
        moved = engine.world.distance_between(before_position, after_position)
        self.assertGreater(engine.world.time, before_time)
        self.assertGreater(moved, 0.0)
        self.assertLessEqual(moved, 2.0)

    def test_multiple_npcs_plan_independently(self):
        engine = OpenWorldEngine()
        ren = engine.world.get_npc("npc_2")
        mira = engine.world.get_npc("npc_3")
        ren.position_override = [21.0, 20.0]
        ren.goals = [{"goal_type": "follow_npc", "target_id": "player"}]
        mira.position_override = [80.0, 80.0]
        mira.goals = [{"goal_type": "reach_location", "target_id": "forest"}]
        engine.world.player.x, engine.world.player.y = 30.0, 20.0
        engine._update_npcs_with_planner()
        self.assertNotEqual(engine.world.npc_position("npc_2"), (21.0, 20.0))
        self.assertNotEqual(engine.world.npc_position("npc_3"), (80.0, 80.0))
        self.assertEqual(ren.goals, [{"goal_type": "follow_npc", "target_id": "player"}])
        self.assertEqual(mira.goals, [{"goal_type": "reach_location", "target_id": "forest"}])

    def test_failed_action_is_fed_back_and_replans(self):
        engine = OpenWorldEngine()
        npc = engine.world.get_npc("npc_2")
        npc.position_override = [20.0, 20.0]
        npc.goals = [{"goal_type": "reach_location", "target_id": "forest"}]
        action = engine.npc_planner.plan_next_action(engine.world, npc.id)
        result = ExecutionResult(False, "path blocked", action)
        engine._apply_npc_action_feedback(npc, action, result)
        self.assertFalse(npc.action_progress["success"])
        self.assertEqual(npc.current_action, {})
        replanned = engine.npc_planner.plan_next_action(engine.world, npc.id)
        self.assertIsNotNone(replanned)
        self.assertEqual(replanned.action_type, "move")


if __name__ == "__main__":
    unittest.main()
