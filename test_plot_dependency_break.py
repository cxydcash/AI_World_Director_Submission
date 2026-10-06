# -*- coding: utf-8 -*-
"""Web-Fix-2 regression: a cancelled prerequisite must not force the chain.

The world is allowed to drift: if A was cancelled, B stays unsatisfied, no
replacement A is fabricated, and the Night Director is told about the break so
it can decide (keep / cancel / replace / new story / nothing).
"""

import unittest

import night_director
import plot_deviation
from entities import NPC, Player
from story_timeline import StoryTimeline
from world import WorldState


def mini_world():
    world = WorldState(
        player=Player(0.0, 0.0),
        npcs=[NPC(id="npc_2", name="Ren", x=0.0, y=0.0)],
    )
    world.story_timeline = StoryTimeline([])
    return world


def add_event(world, event_id, conditions=None, effects=None):
    return world.story_timeline.add_event({
        "event_id": event_id,
        "trigger_at": 0.0,
        "source": "scenario",
        "conditions": conditions or [],
        "effects": effects or [
            {"type": "set_belief", "npc_id": "npc_2", "key": event_id, "value": 0.5}
        ],
    })


def dependent_on_a(event_id):
    return {"type": "event_completed", "event_id": event_id}


class DependencyBreakTests(unittest.TestCase):
    def test_case1_cancelled_prerequisite_does_not_trigger_dependent(self):
        world = mini_world()
        add_event(world, "A")
        b = add_event(world, "B", conditions=[dependent_on_a("A")])
        world.story_timeline.cancel_event("A")

        world.time = 10.0
        world.check_story_events()

        self.assertEqual(b["status"], "pending")                       # not executed
        self.assertNotIn("B", world.get_npc("npc_2").beliefs)          # effects never ran
        self.assertFalse(any(
            entry.get("event_id") == "B" for entry in world.story_event_history
        ))

    def test_case2_broken_prerequisite_reaches_the_night_director(self):
        world = mini_world()
        add_event(world, "A")
        add_event(world, "B", conditions=[dependent_on_a("A")])
        world.story_timeline.cancel_event("A")

        deviations = plot_deviation.detect_deviations(world)
        self.assertTrue(any(
            d.event_id == "B"
            and d.kind == plot_deviation.KIND_DEPENDENCY_MISSING
            and d.metadata.get("depends_on_event") == "A"
            for d in deviations
        ))

        world.time = 23 * 3600
        context = night_director.build_daily_director_context(world)
        self.assertTrue(any(
            d["event_id"] == "B" and d["metadata"].get("depends_on_event") == "A"
            for d in context["story"]["deviations"]
        ))

    def test_case3_no_automatic_replacement_is_fabricated(self):
        world = mini_world()
        add_event(world, "A")
        add_event(world, "B", conditions=[dependent_on_a("A")])
        world.story_timeline.cancel_event("A")

        world.time = 10.0
        world.check_story_events()

        ids = [entry.get("event_id") for entry in world.story_timeline]
        self.assertEqual(ids, ["A", "B"])                    # nothing added
        self.assertFalse(any("__adapted" in str(i) for i in ids))

    def test_case4_director_can_start_an_unrelated_story(self):
        world = mini_world()
        add_event(world, "A")
        add_event(world, "B", conditions=[dependent_on_a("A")])
        world.story_timeline.cancel_event("A")
        world.time = 22 * 3600

        class Provider:
            def plan_next_day(self, context):
                return {
                    "goal_changes": [], "story_updates": [], "new_mysteries": [],
                    "story_events": [{
                        "event_id": "new_story", "title": "A new thread",
                        "description": "An unrelated new storyline begins.",
                        "trigger_at": context["time"]["now"],
                        "conditions": [], "cancel_conditions": [],
                        "effects": [{"type": "set_belief", "npc_id": "npc_2",
                                     "key": "new_story", "value": 0.7}],
                    }],
                    "reasoning_summary": "let the old chain die; start something else",
                }

        class Director:
            def plan_next_day(self, world, context):
                return Provider().plan_next_day(context)

        report = night_director.run_night_director(world, Director())
        self.assertTrue(report["applied"]["story_events"][0]["added"])
        self.assertIsNotNone(world.story_timeline.get_event("new_story"))

        world.time += 1
        world.check_story_events()
        self.assertEqual(world.get_npc("npc_2").beliefs.get("new_story"), 0.7)
        # The old, unsatisfiable dependent is still untouched.
        self.assertEqual(world.story_timeline.get_event("B")["status"], "pending")

    def test_case5_normal_dependency_chain_still_runs(self):
        world = mini_world()
        add_event(world, "A")
        add_event(world, "B", conditions=[dependent_on_a("A")])

        world.time = 1.0
        world.check_story_events()

        self.assertEqual(world.story_timeline.get_event("A")["status"], "triggered")
        self.assertEqual(world.story_timeline.get_event("B")["status"], "triggered")
        self.assertEqual(world.get_npc("npc_2").beliefs.get("B"), 0.5)


if __name__ == "__main__":
    unittest.main()
