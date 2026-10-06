"""Tests for evidence/environment/NPC observation formatters."""

import unittest

import main
from demo_log import format_world_event
from entities import NPC, Player
from world import WorldState


class ObservationCommandTests(unittest.TestCase):
    def _world(self):
        return WorldState(
            player=Player(40.0, 40.0),
            npcs=[
                NPC(
                    id="npc_1",
                    name="Mira",
                    x=20.0,
                    y=20.0,
                    beliefs={"Ren fled north": 0.7},
                    relationships={"player": 0.3},
                    goals=["find Ren"],
                    memory=[{"content": "player created footprint"}],
                )
            ],
            evidence=[
                {
                    "type": "footprint",
                    "location": [40.0, 40.0],
                    "description": "footprints heading north",
                    "target_belief": "Ren fled north",
                    "created_at": 10.0,
                }
            ],
        )

    def test_evidence_observation(self):
        text = main.format_evidence(self._world())
        self.assertIn("footprint", text)
        self.assertIn("footprints heading north", text)
        self.assertIn("target belief: Ren fled north", text)

    def test_environment_observation(self):
        world = self._world()
        world.environment_state["temporary_changes"].append(
            {
                "location": "stone_road",
                "change": {"moisture": 0.5, "duration": 3600},
                "created_at": 0.0,
            }
        )
        text = main.format_environment(world)
        self.assertIn("forest: soil", text)
        self.assertIn("Temporary changes:", text)
        self.assertIn("stone_road", text)

    def test_demo_log_format(self):
        text = format_world_event(
            player_action="created false footprint",
            environment="forest soil supports footprint",
            evidence="footprint created",
            npc_reactions="Ren belief changed",
            director="Ren investigates footprint",
            timeline="ren_missing: pending",
        )
        self.assertIn("========== WORLD EVENT ==========", text)
        self.assertIn("Player Action:", text)
        self.assertIn("Environment:", text)
        self.assertIn("Evidence:", text)
        self.assertIn("NPC Reaction:", text)
        self.assertIn("AI Director:", text)
        self.assertIn("Timeline:", text)
    def test_npc_observation(self):
        text = main.format_npc(self._world(), "npc_1")
        self.assertIn("Mira", text)
        self.assertIn("Ren fled north: 0.7", text)
        self.assertIn("find Ren", text)
        self.assertIn("player: 0.3", text)
        self.assertIn("player created footprint", text)


if __name__ == "__main__":
    unittest.main()