"""Tests for NPC character seeds and conflict-aware opportunities."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from director import LLMDirector, build_world_context
from entities import NPC, Player
from event import execute_event
from persistence import load_world, save_world
from quest import Quest
from world import WorldState


def seeded_world():
    return WorldState(
        time=1000.0,
        player=Player(20.0, 20.0),
        npcs=[
            NPC(
                id="npc_1",
                name="Mira",
                x=20.0,
                y=20.0,
                vision_range=5.0,
                beliefs={"ren_may_be_in_danger": 0.8},
                goals=["find Ren"],
                character_seed={
                    "personality": ["careful", "kind"],
                    "long_term_goal": "protect Ren",
                    "secret": "She knows more about Ren's disappearance than she tells the player.",
                    "fear": "The player discovers the truth.",
                    "conflict": "She wants the player to find Ren, but fears the truth being revealed.",
                },
            )
        ],
        quests=[
            Quest(
                quest_id="quest_001",
                title="Find Ren",
                giver_id="npc_1",
                description="Find Ren.",
                target="find Ren",
                reward=50,
                status="active",
            )
        ],
        history=[
            {
                "time": 900.0,
                "actor": "player",
                "action": "player_explored",
                "target": "forest",
                "content": "Found a clue about Ren.",
            }
        ],
    )


class CharacterSeedTests(unittest.TestCase):
    def test_character_seed_survives_save_and_load(self):
        world = seeded_world()

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file = Path(temporary_directory) / "game_state.json"
            save_world(world, state_file)
            loaded = load_world(state_file)

        self.assertEqual(
            loaded.npcs[0].character_seed,
            world.npcs[0].character_seed,
        )

    def test_director_context_includes_character_seed(self):
        world = seeded_world()

        context = build_world_context(world)
        seed = context["npcs"][0]["character_seed"]

        self.assertEqual(seed["long_term_goal"], "protect Ren")
        self.assertIn("fear", seed)
        self.assertIn("conflict", seed)

    def test_conflict_aware_opportunity_can_trigger_valid_event(self):
        world = seeded_world()
        director = LLMDirector(api_key="test-key")
        event = {
            "event_id": "event_conflict",
            "time": 1000.0,
            "effects": [
                {
                    "type": "set_belief",
                    "npc_id": "npc_1",
                    "key": "player_knows_more",
                    "value": 0.9,
                },
                {
                    "type": "dialogue",
                    "speaker_id": "npc_1",
                    "content": "What did you find in the forest?",
                },
            ],
        }
        decision = json.dumps(
            {
                "should_trigger": True,
                "reason": "Player history touches Mira's conflict.",
                "event": event,
            }
        )

        with patch.object(director, "_request_opportunity", return_value=decision):
            result = director.check_opportunity(world)

        self.assertTrue(result["should_trigger"])
        execute_event(world, result["event"])
        self.assertEqual(world.npcs[0].beliefs["player_knows_more"], 0.9)
        self.assertEqual(len(world.event_history), 1)

    def test_no_conflict_seed_skips_opportunity_llm(self):
        source_world = seeded_world()
        world = WorldState(
            time=1000.0,
            player=Player(20.0, 20.0),
            npcs=[NPC(id="npc_1", name="Mira", x=20.0, y=20.0)],
            quests=source_world.quests,
            history=source_world.history,
        )
        director = LLMDirector(api_key="test-key")

        with patch.object(director, "_request_opportunity") as request:
            result = director.check_opportunity(world)

        request.assert_not_called()
        self.assertFalse(result["should_trigger"])


if __name__ == "__main__":
    unittest.main()