"""Tests for LLM-driven timeline adjustment."""

import json
import unittest
from unittest.mock import patch

from director import LLMDirector
from entities import NPC, Player
from scenario import apply_timeline_change, default_timeline
from world import WorldState


class DirectorTimelineTests(unittest.TestCase):
    def test_director_can_cancel_timeline_and_update_npc(self):
        world = WorldState(
            time=12 * 3600,
            player=Player(40.0, 40.0),
            npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0)],
            story_timeline=default_timeline(),
            history=[
                {
                    "time": 12 * 3600,
                    "actor": "player",
                    "action": "player_rescued_npc",
                    "target": "npc_2",
                    "content": "Rescued Ren.",
                }
            ],
        )
        proposal = {
            "type": "timeline_change",
            "changes": [
                {"event": "villagers_find_ren", "status": "cancelled"},
                {"event": "ren_dies", "status": "cancelled"},
            ],
            "npc_changes": [
                {
                    "npc_id": "npc_1",
                    "belief": "player_saved_Ren",
                    "value": 1.0,
                    "relationship_delta": 0.3,
                    "goal": "thank_player",
                }
            ],
        }
        director = LLMDirector(api_key="test-key")

        with patch.object(
            director, "_request_timeline_change", return_value=json.dumps(proposal)
        ):
            generated = director.check_timeline_change(world)

        apply_timeline_change(world, generated)
        statuses = {entry["id"]: entry["status"] for entry in world.story_timeline}
        npc = world.get_npc("npc_1")

        self.assertEqual(statuses["villagers_find_ren"], "cancelled")
        self.assertEqual(npc.beliefs["player_saved_Ren"], 1.0)
        self.assertEqual(npc.relationships["player"], 0.3)
        self.assertIn("thank_player", npc.goals)


if __name__ == "__main__":
    unittest.main()