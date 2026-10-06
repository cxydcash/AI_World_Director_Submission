"""Tests for the predefined and dynamic story timeline."""

import inspect
import tempfile
import unittest
from pathlib import Path

from action import rescue
from action_layer import ActionExecutor, ActionValidator
from entities import Item, NPC, Player
from npc_planner import NPCPlanner
from persistence import load_world, save_world
from scenario import corruption_timeline, default_timeline
from story_timeline import StoryTimeline
from world import WorldState


def timeline_world():
    return WorldState(
        player=Player(50.0, 50.0),
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


def dynamic_world():
    world = WorldState(
        time=0.0,
        player=Player(50.0, 50.0, inventory=[]),
        npcs=[
            NPC(id="npc_1", name="Mira", x=20.0, y=20.0, position_override=[20.0, 20.0]),
            NPC(id="npc_2", name="Ren", x=40.0, y=40.0, position_override=[40.0, 40.0]),
            NPC(id="npc_4", name="Ivy", x=5.0, y=5.0, position_override=[5.0, 5.0]),
        ],
        items=[Item(id="item_ledger", type="ledger", x=40.0, y=40.0)],
        story_timeline=default_timeline(),
        characters_state={
            "npc_1": {"alive": True, "location": "home", "rescued": False},
            "npc_2": {"alive": True, "location": "forest", "rescued": False, "injured": False, "health": 100},
            "npc_4": {"alive": True, "location": "village"},
        },
    )
    world.story_timeline.clear()
    return world


def status_of(world, event_id):
    return world.story_timeline.get_event(event_id)["status"]


class StoryTimelineTests(unittest.TestCase):
    def test_time_advance_triggers_due_events(self):
        world = timeline_world()

        world.wait(8 * 3600)
        world.wait(4 * 3600)

        statuses = {entry["id"]: entry["status"] for entry in world.story_timeline}
        self.assertEqual(statuses["ren_missing"], "triggered")
        self.assertEqual(statuses["mira_search"], "triggered")

    def test_unintervened_timeline_harms_ren(self):
        world = timeline_world()

        world.wait(18 * 3600)

        self.assertTrue(world.characters_state["npc_2"]["injured"])
        self.assertTrue(world.characters_state["npc_2"]["alive"])


class DynamicStoryTimelineTests(unittest.TestCase):
    def test_add_event(self):
        world = dynamic_world()
        event = world.story_timeline.add_event({
            "event_id": "guard_investigates",
            "trigger_at": 100,
            "effects": [{"type": "add_goal", "npc_id": "npc_1", "goal": "investigate mayor"}],
        })
        self.assertEqual(event["event_id"], "guard_investigates")
        self.assertEqual(event["trigger_time"], 100)
        self.assertEqual(status_of(world, "guard_investigates"), "pending")

    def test_remove_and_cancel_event(self):
        world = dynamic_world()
        timeline = world.story_timeline
        timeline.add_event({"event_id": "a", "trigger_at": 1, "effects": []})
        timeline.add_event({"event_id": "b", "trigger_at": 1, "effects": []})

        self.assertTrue(timeline.cancel_event("a"))
        self.assertEqual(status_of(world, "a"), "cancelled")
        self.assertTrue(timeline.remove_event("b"))
        self.assertIsNone(timeline.get_event("b"))

    def test_get_event(self):
        world = dynamic_world()
        world.story_timeline.add_event({"event_id": "lookup", "trigger_at": 5, "effects": []})
        self.assertIsNotNone(world.story_timeline.get_event("lookup"))
        self.assertIsNone(world.story_timeline.get_event("missing"))

    def test_pending_event_waits_for_trigger_time(self):
        world = dynamic_world()
        world.story_timeline.add_event({
            "event_id": "later",
            "trigger_at": 100,
            "effects": [{"type": "add_goal", "npc_id": "npc_1", "goal": "later_goal"}],
        })
        world.advance_time(50)
        self.assertEqual(status_of(world, "later"), "pending")
        self.assertIn("later", [event["event_id"] for event in world.story_timeline.list_pending()])

    def test_time_trigger(self):
        world = dynamic_world()
        world.story_timeline.add_event({
            "event_id": "timed",
            "trigger_at": 10,
            "effects": [{"type": "add_goal", "npc_id": "npc_1", "goal": "timed_goal"}],
        })
        world.advance_time(10)
        self.assertEqual(status_of(world, "timed"), "triggered")
        self.assertIn("timed_goal", world.get_npc("npc_1").goals)

    def test_condition_trigger(self):
        world = dynamic_world()
        world.story_timeline.add_event({
            "event_id": "needs_item",
            "trigger_at": 0,
            "conditions": [{"type": "player_has_item", "item_id": "item_ledger"}],
            "effects": [{"type": "set_belief", "npc_id": "npc_2", "key": "player_has_ledger", "value": 1.0}],
        })
        world.advance_time(1)
        self.assertEqual(status_of(world, "needs_item"), "pending")

        world.player.inventory.append("item_ledger")
        world.advance_time(1)
        self.assertEqual(status_of(world, "needs_item"), "triggered")
        self.assertEqual(world.get_npc("npc_2").beliefs["player_has_ledger"], 1.0)

    def test_event_dependency(self):
        world = dynamic_world()
        world.story_timeline.add_event({
            "event_id": "first",
            "trigger_at": 0,
            "effects": [{"type": "set_belief", "npc_id": "npc_1", "key": "first_done", "value": 1.0}],
        })
        world.story_timeline.add_event({
            "event_id": "second",
            "trigger_at": 0,
            "conditions": [{"type": "event_completed", "event_id": "first"}],
            "effects": [{"type": "set_belief", "npc_id": "npc_1", "key": "second_done", "value": 1.0}],
        })
        world.advance_time(1)
        self.assertEqual(status_of(world, "first"), "triggered")
        self.assertEqual(status_of(world, "second"), "triggered")
        self.assertEqual(world.get_npc("npc_1").beliefs["second_done"], 1.0)

    def test_event_completed_condition_accepts_triggered(self):
        world = dynamic_world()
        world.story_timeline.add_event({"event_id": "done", "trigger_at": 0, "effects": []})
        world.story_timeline.add_event({
            "event_id": "depends",
            "trigger_at": 0,
            "conditions": [{"type": "event_completed", "event_id": "done"}],
            "effects": [{"type": "set_belief", "npc_id": "npc_2", "key": "depends_ok", "value": 1.0}],
        })
        world.advance_time(1)
        self.assertEqual(status_of(world, "depends"), "triggered")

    def test_event_cancelled_condition(self):
        world = dynamic_world()
        timeline = world.story_timeline
        timeline.add_event({"event_id": "will_cancel", "trigger_at": 0, "effects": []})
        timeline.add_event({
            "event_id": "reacts",
            "trigger_at": 0,
            "conditions": [{"type": "event_cancelled", "event_id": "will_cancel"}],
            "effects": [{"type": "set_belief", "npc_id": "npc_1", "key": "branch_taken", "value": 1.0}],
        })
        timeline.cancel_event("will_cancel")
        world.advance_time(1)
        self.assertEqual(status_of(world, "will_cancel"), "cancelled")
        self.assertEqual(status_of(world, "reacts"), "triggered")

    def test_effects_execute_through_event_executor(self):
        world = dynamic_world()
        world.story_timeline.add_event({
            "event_id": "effects",
            "trigger_at": 0,
            "effects": [
                {"type": "set_belief", "npc_id": "npc_2", "key": "mayor_is_corrupt", "value": 0.9},
                {"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"},
                {"type": "add_history", "action": "test_effect", "target": "npc_2", "content": "effect ran"},
            ],
        })
        world.advance_time(1)
        self.assertEqual(status_of(world, "effects"), "triggered")
        self.assertEqual(world.get_npc("npc_2").beliefs["mayor_is_corrupt"], 0.9)
        self.assertIn("find ledger", world.get_npc("npc_2").goals)
        self.assertTrue(any(entry.get("action") == "test_effect" for entry in world.history))
        self.assertTrue(any(entry.get("event_id") == "effects" for entry in world.event_history))

    def test_execution_failure_does_not_fake_completion(self):
        world = dynamic_world()
        event = world.story_timeline.add_event({
            "event_id": "bad_effect",
            "trigger_at": 0,
            "effects": [{"type": "add_goal", "npc_id": "npc_missing", "goal": "x"}],
        })
        world.advance_time(1)
        self.assertEqual(status_of(world, "bad_effect"), "pending")
        self.assertNotEqual(event["metadata"].get("execution_status"), "completed")
        self.assertIn("last_error", event["metadata"])
        self.assertFalse(any(entry.get("event_id") == "bad_effect" for entry in world.story_event_history))

    def test_cancelled_event_does_not_execute(self):
        world = dynamic_world()
        timeline = world.story_timeline
        timeline.add_event({
            "event_id": "cancel_me",
            "trigger_at": 0,
            "effects": [{"type": "add_goal", "npc_id": "npc_1", "goal": "should_not_happen"}],
        })
        timeline.cancel_event("cancel_me")
        world.advance_time(5)
        self.assertEqual(status_of(world, "cancel_me"), "cancelled")
        self.assertNotIn("should_not_happen", world.get_npc("npc_1").goals)

    def test_dynamic_event_added_at_runtime(self):
        world = dynamic_world()
        world.advance_time(10)
        world.story_timeline.add_event({
            "event_id": "late_addition",
            "trigger_at": world.time + 1,
            "effects": [{"type": "set_belief", "npc_id": "npc_1", "key": "late_addition", "value": 1.0}],
        })
        world.advance_time(1)
        self.assertEqual(status_of(world, "late_addition"), "triggered")
        self.assertEqual(world.get_npc("npc_1").beliefs["late_addition"], 1.0)

    def test_dynamic_event_survives_save_and_load(self):
        world = dynamic_world()
        world.story_timeline.add_event({
            "event_id": "dynamic_persisted",
            "trigger_at": 999,
            "effects": [{"type": "set_belief", "npc_id": "npc_1", "key": "dynamic", "value": 1.0}],
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dynamic_state.json"
            save_world(world, path)
            loaded = load_world(path)

        self.assertIsNotNone(loaded.story_timeline.get_event("dynamic_persisted"))
        loaded.advance_time(999)
        self.assertEqual(status_of(loaded, "dynamic_persisted"), "triggered")
        self.assertEqual(loaded.get_npc("npc_1").beliefs["dynamic"], 1.0)

    def test_corruption_timeline_still_runs_after_migration(self):
        world = dynamic_world()
        world.story_timeline = StoryTimeline(corruption_timeline())
        for event in world.story_timeline:
            self.assertTrue(event["effects"])
        world.advance_time(8 * 3600)
        self.assertEqual(status_of(world, "ren_discovers_ledger"), "triggered")
        self.assertEqual(world.get_npc("npc_2").beliefs["mayor_is_corrupt"], 0.8)
        self.assertIn("find ledger", world.get_npc("npc_2").goals)

    def test_rescue_cancels_and_blocks_followup_events(self):
        world = dynamic_world()
        world.story_timeline = StoryTimeline(default_timeline())
        world.player.x, world.player.y = 40.0, 40.0
        world.get_npc("npc_2").position_override = [40.0, 40.0]
        success, _ = rescue(world, "npc_2")
        self.assertTrue(success)
        self.assertEqual(status_of(world, "ren_dies"), "cancelled")

        world.advance_time(60 * 3600)
        self.assertEqual(status_of(world, "ren_dies"), "cancelled")
        self.assertTrue(world.characters_state["npc_2"]["alive"])

    def test_timeline_layer_has_no_event_id_branching(self):
        import scenario as scenario_module
        import story_timeline as timeline_module

        for module in (scenario_module, timeline_module):
            source = inspect.getsource(module)
            self.assertNotIn("event_id ==", source)
            self.assertNotIn('event["id"] ==', source)

        world = dynamic_world()
        world.story_timeline.add_event({
            "event_id": "totally_novel_branch",
            "trigger_at": 0,
            "effects": [{"type": "set_belief", "npc_id": "npc_1", "key": "novel", "value": 0.42}],
        })
        world.advance_time(1)
        self.assertEqual(status_of(world, "totally_novel_branch"), "triggered")
        self.assertEqual(world.get_npc("npc_1").beliefs["novel"], 0.42)

    def test_timeline_does_not_directly_modify_worldstate(self):
        world = dynamic_world()
        npc = world.get_npc("npc_2")
        before = (list(npc.goals), dict(npc.beliefs), list(npc.memory))
        world.story_timeline.add_event({
            "event_id": "blocked",
            "trigger_at": 0,
            "conditions": [{"type": "player_has_item", "item_id": "item_absent"}],
            "effects": [{"type": "add_goal", "npc_id": "npc_2", "goal": "should_wait"}],
        })
        world.advance_time(1)
        after = (list(npc.goals), dict(npc.beliefs), list(npc.memory))
        self.assertEqual(before, after)
        self.assertEqual(status_of(world, "blocked"), "pending")
        self.assertFalse(any(entry.get("event_id") == "blocked" for entry in world.event_history))

    def test_npc_goal_effect_is_taken_over_by_planner(self):
        world = dynamic_world()
        world.story_timeline.add_event({
            "event_id": "assign_goal",
            "trigger_at": 0,
            "effects": [{"type": "add_goal", "npc_id": "npc_2", "goal": "reach town"}],
        })
        before = world.npc_position("npc_2")
        world.advance_time(1)

        self.assertIn("reach town", world.get_npc("npc_2").goals)
        self.assertEqual(world.npc_position("npc_2"), before)

        action = NPCPlanner().plan_next_action(world, "npc_2")
        self.assertEqual(action.action_type, "move")
        validator = ActionValidator()
        executor = ActionExecutor(validator)
        validation = validator.validate(world, action)
        self.assertTrue(validation.valid)
        executor.execute(world, action, validation=validation)
        self.assertNotEqual(world.npc_position("npc_2"), before)


if __name__ == "__main__":
    unittest.main()
