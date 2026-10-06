"""Tests for the environment constraint layer."""

import unittest

from action import execute_parsed_action, investigate_evidence
from entities import NPC, Player
from environment import evaluate_player_action, environment_changes
from world import WorldState


def environment_world():
    return WorldState(
        player=Player(60.0, 60.0),
        npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0)],
    )


class EnvironmentTests(unittest.TestCase):
    def test_soil_preserves_footprints(self):
        world = environment_world()
        action = {
            "type": "create_evidence",
            "evidence_type": "footprint",
            "location": "forest",
            "description": "footprints heading north",
        }

        result = evaluate_player_action(world, action)

        self.assertTrue(result["success"])
        self.assertIn("clear footprints", result["effect"])

    def test_stone_rejects_footprints(self):
        world = environment_world()
        action = {
            "type": "create_evidence",
            "evidence_type": "footprint",
            "location": "stone_road",
            "description": "footprints heading north",
        }

        result = evaluate_player_action(world, action)

        self.assertFalse(result["success"])
        self.assertIn("stone", result["reason"])

    def test_water_allows_stone_footprints_with_side_effects(self):
        world = environment_world()
        action = {
            "type": "create_evidence",
            "evidence_type": "footprint",
            "location": "stone_road",
            "description": "footprints heading north",
            "water_applied": True,
        }

        result = evaluate_player_action(world, action)

        self.assertTrue(result["success"])
        self.assertTrue(result["side_effects"])
        self.assertTrue(
            any(entry["type"] == "water_trace" for entry in world.evidence)
        )
        self.assertTrue(environment_changes(world.environment_state, world.time))

    def test_environment_constraint_in_execute_parsed_action(self):
        world = environment_world()
        action = {
            "type": "create_evidence",
            "evidence_type": "footprint",
            "location": "stone_road",
            "description": "footprints heading north",
        }

        success, message = execute_parsed_action(world, action, "制造脚印")

        self.assertFalse(success)
        self.assertIn("stone", message)

    def test_water_trace_produces_different_npc_belief(self):
        world = environment_world()
        action = {
            "type": "create_evidence",
            "evidence_type": "footprint",
            "location": "stone_road",
            "description": "footprints heading north",
            "water_applied": True,
        }
        evaluate_player_action(world, action)
        world.npcs[0].position_override = [60.0, 60.0]

        success, _ = investigate_evidence(world, "npc_1")

        self.assertTrue(success)
        self.assertEqual(world.npcs[0].beliefs["someone_manipulated_evidence"], 0.8)


if __name__ == "__main__":
    unittest.main()