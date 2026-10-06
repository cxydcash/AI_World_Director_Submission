"""Tests for player world actions."""

import unittest

from action import attack, escape, fake_trace, investigate_evidence, knock_out, rescue
from entities import NPC, Player
from quest import Quest
from scenario import default_timeline
from world import WorldState


class AlwaysSucceed:
    def random(self):
        return 0.0


def action_world():
    return WorldState(
        time=12 * 3600,
        player=Player(40.0, 40.0),
        npcs=[NPC(id="npc_2", name="Ren", x=40.0, y=40.0, vision_range=8.0, relationships={"player": 0.5}, position_override=[40.0, 40.0])],
        quests=[Quest("quest_001", "Find Ren", "npc_1", "Find Ren.", "find Ren", 50, "active")],
        story_timeline=default_timeline(),
        characters_state={"npc_2": {"alive": True, "injured": True, "unconscious": False, "health": 80, "location": "forest", "rescued": False}},
    )


class ActionTests(unittest.TestCase):
    def test_rescue_resolves_quest_and_timeline(self):
        world = action_world()

        success, _ = rescue(world, "npc_2")

        self.assertTrue(success)
        self.assertTrue(world.characters_state["npc_2"]["rescued"])
        self.assertEqual(world.get_quest("quest_001").status, "completed")
        statuses = {entry["id"]: entry["status"] for entry in world.story_timeline}
        self.assertEqual(statuses["ren_dies"], "cancelled")

    def test_attack_and_knock_out_change_character_state(self):
        world = action_world()

        attack(world, "npc_2")
        self.assertEqual(world.characters_state["npc_2"]["health"], 60)
        self.assertEqual(world.get_npc("npc_2").relationships["player"], 0.3)

        knock_out(world, "npc_2")
        self.assertTrue(world.characters_state["npc_2"]["unconscious"])

    def test_escape_with_training_succeeds(self):
        world = action_world()
        world.player.escape_probability = 0.8

        success, _ = escape(world, "npc_2", rng=AlwaysSucceed())

        self.assertTrue(success)
        self.assertTrue(world.characters_state["npc_2"]["rescued"])

    def test_fake_trace_can_create_wrong_npc_belief(self):
        world = action_world()

        fake_trace(world)
        success, _ = investigate_evidence(world, "npc_2")

        self.assertTrue(success)
        self.assertEqual(world.npcs[0].beliefs["ren_went_wrong_direction"], 0.8)
        self.assertTrue(world.evidence[0]["investigated"])


if __name__ == "__main__":
    unittest.main()