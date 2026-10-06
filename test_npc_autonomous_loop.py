"""Step 8 Stage 4 tests: NPC autonomous behaviour loop and goal lifecycle."""

import unittest

from npc_planner import NPCPlanner
from open_world import OpenWorldEngine


def runtime_engine():
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    return engine


class NPCAutonomousLoopTests(unittest.TestCase):
    def test_lead_player_to_completes_on_arrival(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 21.0]   # within 1.5 of home (20, 20)
        ren.goals = ["lead_player_to:home"]
        engine._update_npcs_with_planner()
        self.assertEqual(ren.goal_states["lead_player_to:home"]["status"], "completed")
        action = NPCPlanner().plan_next_action(engine.world, "npc_2")
        self.assertFalse(action is not None and (action.source_intent or {}).get("raw") == "lead_player_to:home")

    def test_completed_goal_is_not_repeated(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [20.0, 20.0]
        ren.goals = ["lead_player_to:home"]
        engine._update_npcs_with_planner()
        self.assertEqual(ren.goal_states["lead_player_to:home"]["status"], "completed")
        for _ in range(5):
            engine._update_npcs_with_planner()
            action = NPCPlanner().plan_next_action(engine.world, "npc_2")
            self.assertFalse(
                action is not None and (action.source_intent or {}).get("raw") == "lead_player_to:home"
            )
        self.assertEqual(ren.goal_states["lead_player_to:home"]["status"], "completed")

    def test_follow_player_keeps_following_a_moving_player(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.goals = ["follow player"]
        ren.position_override = [40.0, 40.0]
        engine.world.player.x, engine.world.player.y = 60.0, 40.0
        planner = NPCPlanner()
        action = planner.plan_next_action(engine.world, "npc_2")
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 60.0, "y": 40.0})

    def test_follow_player_does_not_move_when_already_close(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.goals = ["follow player"]
        ren.position_override = [50.0, 50.0]
        engine.world.player.x, engine.world.player.y = 50.0, 50.0
        self.assertIsNone(NPCPlanner().plan_next_action(engine.world, "npc_2"))

    def test_stop_follow_ends_the_goal(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        engine.world.player.x, engine.world.player.y = 69.0, 70.0
        engine._set_follow_state("npc_2", True)
        self.assertIn("follow player", ren.goals)
        engine._set_follow_state("npc_2", False)
        self.assertNotIn("follow player", ren.goals)
        self.assertFalse(engine.world.characters_state["npc_2"].get("following_player"))

    def test_nonexistent_item_does_not_search_forever(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        ren.goals = ["find_item:nonexistent_item"]
        engine._update_npcs_with_planner()
        state = ren.goal_states["find_item:nonexistent_item"]
        self.assertEqual(state["status"], "failed")
        self.assertIn("does not exist", state["reason"])
        self.assertNotEqual(ren.action_progress.get("action"), "search")
        action = NPCPlanner().plan_next_action(engine.world, "npc_2")
        self.assertFalse(
            action is not None and (action.source_intent or {}).get("raw") == "find_item:nonexistent_item"
        )

    def test_failed_goal_lets_planner_pick_another_goal(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        ren.goals = ["find_item:nonexistent_item", "reach_location:home"]
        engine._update_npcs_with_planner()
        self.assertEqual(ren.goal_states["find_item:nonexistent_item"]["status"], "failed")
        self.assertEqual(ren.action_progress.get("action"), "move")
        self.assertLess(
            engine.world.distance_between(engine.world.npc_position("npc_2"), (20.0, 20.0)),
            engine.world.distance_between((40.0, 40.0), (20.0, 20.0)),
        )

    def test_multiple_goals_are_planned_by_priority(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [40.0, 40.0]
        ren.goals = ["find ledger", "lead_player_to:home"]
        action = NPCPlanner().plan_next_action(engine.world, "npc_2")
        self.assertEqual(action.action_type, "move")
        self.assertEqual(action.parameters, {"x": 20.0, "y": 20.0})
        self.assertEqual((action.source_intent or {}).get("goal_type"), "lead_player_to")

    def test_npc_acts_autonomously_without_player_input(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [20.0, 20.0]
        ren.goals = ["reach_location:forest"]
        before = engine.world.npc_position("npc_2")
        engine.tick_seconds = 3600.0
        engine._update_npcs_with_planner()
        self.assertNotEqual(engine.world.npc_position("npc_2"), before)
        self.assertEqual(ren.action_progress.get("action"), "move")

    def test_no_repeated_action_over_many_ticks(self):
        engine = runtime_engine()
        ren = engine.world.get_npc("npc_2")
        ren.position_override = [21.0, 21.0]
        ren.goals = ["lead_player_to:home", "reach_location:town"]
        engine.tick_seconds = 3600.0
        for _ in range(8):
            engine.tick()
        self.assertEqual(ren.goal_states["lead_player_to:home"]["status"], "completed")
        action = NPCPlanner().plan_next_action(engine.world, "npc_2")
        self.assertFalse(action is not None and (action.source_intent or {}).get("raw") == "lead_player_to:home")
        self.assertLess(
            engine.world.distance_between(engine.world.npc_position("npc_2"), (70.0, 70.0)),
            engine.world.distance_between((21.0, 21.0), (70.0, 70.0)),
        )


if __name__ == "__main__":
    unittest.main()
