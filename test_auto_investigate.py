"""Tests for automatic nearby-NPC evidence discovery."""

import unittest

from action import create_evidence
from entities import NPC, Player
from world import WorldState


def auto_world(suspicious=False):
    personality = ["suspicious"] if suspicious else ["kind"]
    return WorldState(
        player=Player(40.0, 40.0),
        npcs=[
            NPC(
                id="npc_1",
                name="Mira",
                x=40.0,
                y=40.0,
                vision_range=5.0,
                personality=personality,
                position_override=[40.0, 40.0],
            ),
            NPC(
                id="npc_2",
                name="Ren",
                x=90.0,
                y=90.0,
                vision_range=5.0,
                position_override=[90.0, 90.0],
            ),
        ],
    )


class AutoInvestigateTests(unittest.TestCase):
    def test_nearby_npc_discovers_evidence(self):
        world = auto_world()

        create_evidence(
            world,
            "footprint",
            "forest",
            "footprints heading north",
            target_belief="Ren fled north",
        )

        self.assertEqual(world.npcs[0].beliefs["Ren fled north"], 0.7)
        self.assertTrue(
            any("Observed footprint evidence" in memory.get("content", "") for memory in world.npcs[0].memory)
        )
        self.assertNotIn("Ren fled north", world.npcs[1].beliefs)

    def test_suspicious_npc_forms_suspicion(self):
        world = auto_world(suspicious=True)

        create_evidence(
            world,
            "footprint",
            "forest",
            "footprints heading north",
            target_belief="Ren fled north",
        )

        self.assertEqual(world.npcs[0].beliefs["suspect_false_evidence"], 0.5)


if __name__ == "__main__":
    unittest.main()