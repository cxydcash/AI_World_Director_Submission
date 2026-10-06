"""Tests for the story timeline engine."""

import unittest

from action import fake_trace, investigate_evidence, rescue
from entities import NPC, Player
from scenario import default_timeline
from story_engine import check_story_events
from world import WorldState


def story_world():
    return WorldState(
        player=Player(40.0, 40.0),
        npcs=[
            NPC(id="npc_1", name="Mira", x=20.0, y=20.0),
            NPC(id="npc_2", name="Ren", x=40.0, y=40.0, position_override=[40.0, 40.0]),
        ],
        story_timeline=default_timeline(),
        characters_state={
            "npc_1": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "village", "rescued": False},
            "npc_2": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "forest", "rescued": False},
        },
    )


class StoryEngineTests(unittest.TestCase):
    def test_no_intervention_leads_to_death(self):
        world = story_world()

        world.wait(3 * 86400)

        statuses = {entry["id"]: entry["status"] for entry in world.story_timeline}
        self.assertEqual(statuses["ren_dies"], "triggered")
        self.assertFalse(world.characters_state["npc_2"]["alive"])
        self.assertTrue(
            any(entry["id"] == "ren_dies" for entry in world.story_event_history)
        )

    def test_early_rescue_cancels_death(self):
        world = story_world()

        rescue(world, "npc_2")
        world.wait(3 * 86400)

        statuses = {entry["id"]: entry["status"] for entry in world.story_timeline}
        self.assertEqual(statuses["ren_dies"], "cancelled")
        self.assertTrue(world.characters_state["npc_2"]["alive"])

    def test_false_evidence_does_not_change_true_timeline(self):
        world = story_world()
        world.move(20.0, 20.0)

        fake_trace(world)
        investigate_evidence(world, "npc_1")
        world.wait(3 * 86400)

        self.assertEqual(world.npcs[0].beliefs["ren_went_wrong_direction"], 0.8)
        statuses = {entry["id"]: entry["status"] for entry in world.story_timeline}
        self.assertEqual(statuses["ren_dies"], "triggered")

    def test_director_can_modify_story_event(self):
        class ModifyDeath:
            def evaluate_story_event(self, world, event):
                if event["id"] == "ren_dies":
                    return {
                        "decision": "modify",
                        "reason": "player delayed but left medicine",
                        "modified_event": {
                            "id": "ren_dies",
                            "trigger_time": event["trigger_time"],
                            "description": "Ren is critical but stable",
                            "status": "modified",
                        },
                    }
                return {"decision": "keep", "reason": "no change", "modified_event": None}

        world = story_world()
        world.time = 3 * 86400

        check_story_events(world, ModifyDeath())

        ren_dead = next(entry for entry in world.story_timeline if entry["id"] == "ren_dies")
        self.assertEqual(ren_dead["status"], "modified")
        self.assertTrue(world.characters_state["npc_2"]["alive"])


if __name__ == "__main__":
    unittest.main()