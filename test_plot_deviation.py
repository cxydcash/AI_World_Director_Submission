# -*- coding: utf-8 -*-
"""Step 12.6 tests: Plot Deviation detection + Director awareness/re-plan."""

import unittest

import night_director
import plot_deviation
from director_input import GOAL_CHANGE_ADD, GoalChange, split_goal_string
from entities import NPC, Player
from goal_manager import goal_manager_for
from plot_deviation import (
    BLOCKED_DEVIATION_SECONDS,
    KIND_CONDITION_BLOCKED,
    KIND_DEPENDENCY_MISSING,
    KIND_EVENT_OVERDUE,
    KIND_EXECUTION_FAILED,
    KIND_GOAL_FAILED,
    KIND_GOAL_SUSPENDED,
    KIND_NPC_DEAD,
    OVERDUE_DEVIATION_SECONDS,
    detect_deviations,
)
from story_timeline import StoryTimeline
from world import WorldState


def mini_world():
    world = WorldState(
        player=Player(70.0, 70.0),
        npcs=[
            NPC(id="npc_2", name="Ren", x=70.0, y=70.0),
            NPC(id="npc_4", name="Ivy", x=5.0, y=95.0),
        ],
    )
    world.story_timeline = StoryTimeline([])
    return world


def add_event(world, event_id, effects=None, conditions=None, trigger_at=0.0,
              status="pending", metadata=None, source="scenario"):
    event = {
        "event_id": event_id,
        "trigger_at": float(trigger_at),
        "source": source,
        "status": status,
        "effects": effects or [{"type": "set_belief", "npc_id": "npc_4", "key": "marker", "value": 0.1}],
    }
    if conditions:
        event["conditions"] = conditions
    if metadata:
        event["metadata"] = metadata
    return world.story_timeline.add_event(event)


def add_goal(world, npc_id, goal_string, urgency="normal"):
    goal_type, target = split_goal_string(goal_string)
    return goal_manager_for(world).apply(world, GoalChange(
        change_type=GOAL_CHANGE_ADD, npc_id=npc_id, goal_type=goal_type,
        target=target, goal_string=goal_string, urgency=urgency,
        source="ai_director", reason="test"))


class DetectionTests(unittest.TestCase):
    def test_1_npc_dead(self):
        world = mini_world()
        add_event(world, "ev_dead", effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"}])
        world.characters_state["npc_2"]["alive"] = False
        devs = detect_deviations(world)
        self.assertTrue(any(d.kind == KIND_NPC_DEAD and d.event_id == "ev_dead" for d in devs))

    def test_2_missing_dependency(self):
        world = mini_world()
        add_event(world, "ev_missing", effects=[
            {"type": "spawn_item", "item_id": "item_ghost", "item_type": "x", "x": 1.0, "y": 1.0}
        ])
        devs = detect_deviations(world)
        self.assertTrue(any(
            d.kind == KIND_DEPENDENCY_MISSING and d.item_id == "item_ghost" for d in devs
        ))

    def test_3_suspended_goal(self):
        world = mini_world()
        add_goal(world, "npc_2", "lead_player_to:mayor_house")
        add_goal(world, "npc_2", "reach_location:forest", urgency="urgent")
        add_event(world, "ev_susp", effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"}])
        devs = detect_deviations(world)
        self.assertTrue(any(
            d.kind == KIND_GOAL_SUSPENDED and d.npc_id == "npc_2" for d in devs
        ))
        self.assertNotIn("reach_location:forest", [])  # sanity

    def test_4_failed_goal(self):
        world = mini_world()
        add_goal(world, "npc_2", "reach_location:forest")
        world.get_npc("npc_2").goal_states["reach_location:forest"]["status"] = "failed"
        add_event(world, "ev_failed_goal", effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"}])
        devs = detect_deviations(world)
        self.assertTrue(any(d.kind == KIND_GOAL_FAILED and d.npc_id == "npc_2" for d in devs))

    def test_5_blocked_event_is_not_immediately_a_deviation(self):
        world = mini_world()
        add_event(world, "ev_blocked", trigger_at=0.0, conditions=[
            {"type": "character_state", "npc_id": "npc_2", "key": "alive", "value": False}
        ])
        world.check_story_events()
        event = world.story_timeline.get_event("ev_blocked")
        self.assertEqual(event["status"], "pending")
        self.assertIn("blocked_since", event["metadata"])
        devs = detect_deviations(world, now=world.time)
        self.assertFalse(any(d.kind == KIND_CONDITION_BLOCKED for d in devs))

    def test_6_long_blocked_becomes_deviation(self):
        world = mini_world()
        add_event(world, "ev_long_blocked", trigger_at=0.0, conditions=[
            {"type": "character_state", "npc_id": "npc_2", "key": "alive", "value": False}
        ])
        world.check_story_events()
        devs = detect_deviations(world, now=BLOCKED_DEVIATION_SECONDS + 60.0)
        self.assertTrue(any(d.kind == KIND_CONDITION_BLOCKED and d.event_id == "ev_long_blocked" for d in devs))

    def test_7_execution_failure(self):
        world = mini_world()
        add_event(world, "ev_fail", effects=[{"type": "add_memory", "npc_id": "npc_ghost", "content": "x"}])
        world.check_story_events()
        event = world.story_timeline.get_event("ev_fail")
        self.assertEqual(event["status"], "pending")
        self.assertGreaterEqual(event["metadata"].get("failure_count", 0), 1)
        devs = detect_deviations(world)
        self.assertTrue(any(d.kind == KIND_EXECUTION_FAILED and d.event_id == "ev_fail" for d in devs))

    def test_8_overdue_event(self):
        world = mini_world()
        add_event(world, "ev_overdue", trigger_at=0.0, conditions=[
            {"type": "character_state", "npc_id": "npc_2", "key": "alive", "value": False}
        ])
        devs = detect_deviations(world, now=OVERDUE_DEVIATION_SECONDS + 60.0)
        self.assertTrue(any(d.kind == KIND_EVENT_OVERDUE and d.event_id == "ev_overdue" for d in devs))

    def test_detection_is_read_only(self):
        world = mini_world()
        add_event(world, "ev_ro", effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"}])
        before_goals = {npc.id: list(npc.goals) for npc in world.npcs}
        detect_deviations(world)
        self.assertEqual({npc.id: list(npc.goals) for npc in world.npcs}, before_goals)

    def test_plot_deviation_has_required_fields(self):
        world = mini_world()
        add_event(world, "ev_fields", effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"}])
        world.characters_state["npc_2"]["alive"] = False
        dev = detect_deviations(world)[0]
        payload = dev.to_dict()
        for key in ("event_id", "kind", "evidence", "severity", "detected_at"):
            self.assertIn(key, payload)


class NightContextTests(unittest.TestCase):
    def test_9_deviations_enter_night_context(self):
        world = mini_world()
        world.time = 23 * 3600
        add_event(world, "ev_ctx", effects=[{"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"}])
        world.characters_state["npc_2"]["alive"] = False
        context = night_director.build_daily_director_context(world)
        self.assertIn("deviations", context["story"])
        self.assertTrue(any(d["kind"] == KIND_NPC_DEAD for d in context["story"]["deviations"]))

    def test_10_empty_deviations_is_valid(self):
        world = mini_world()
        self.assertEqual(detect_deviations(world), [])
        context = night_director.build_daily_director_context(world)
        self.assertEqual(context["story"]["deviations"], [])


class DirectorDecisionTests(unittest.TestCase):
    class DelayEvaluator:
        def evaluate_story_event(self, world, event):
            return {"decision": "delay", "delay_until": world.time + 7200, "reason": "wait"}

    class ModifyEvaluator:
        def evaluate_story_event(self, world, event):
            return {"decision": "modify", "reason": "re-plan", "modified_event": {
                "effects": [{"type": "set_belief", "npc_id": "npc_4", "key": "modified", "value": 0.9}],
            }}

    class CancelEvaluator:
        def evaluate_story_event(self, world, event):
            return {"decision": "cancel", "reason": "no longer valid"}

    def test_11_12_delay_updates_trigger_at_and_stays_pending(self):
        world = mini_world()
        add_event(world, "ev_delay", trigger_at=0.0)
        world.story_director = self.DelayEvaluator()
        world.check_story_events()
        event = world.story_timeline.get_event("ev_delay")
        self.assertEqual(event["status"], "pending")
        self.assertEqual(event["trigger_at"], 7200.0)
        self.assertTrue(event["metadata"]["delay_history"])
        self.assertNotIn("marker", world.get_npc("npc_4").beliefs)  # effect did not run

    def test_13_modify(self):
        world = mini_world()
        add_event(world, "ev_modify", trigger_at=0.0)
        world.story_director = self.ModifyEvaluator()
        world.check_story_events()
        self.assertEqual(world.story_timeline.get_event("ev_modify")["status"], "modified")
        self.assertIsNotNone(world.story_timeline.get_event("ev_modify__adapted"))

    def test_14_cancel(self):
        world = mini_world()
        add_event(world, "ev_cancel", trigger_at=0.0)
        world.story_director = self.CancelEvaluator()
        world.check_story_events()
        self.assertEqual(world.story_timeline.get_event("ev_cancel")["status"], "cancelled")

    def test_15_replace_does_not_leave_original_running(self):
        world = mini_world()
        add_event(world, "ev_replace", trigger_at=0.0)
        report = night_director.normalize_report({
            "story_updates": [{"event_id": "ev_replace", "status": "cancelled"}],
            "story_events": [{
                "event_id": "ev_replacement", "title": "replacement", "description": "replacement",
                "trigger_at": 3600.0,
                "effects": [{"type": "set_belief", "npc_id": "npc_4", "key": "replacement", "value": 0.4}],
            }],
        })
        night_director.apply_daily_report(world, report)
        self.assertEqual(world.story_timeline.get_event("ev_replace")["status"], "cancelled")
        self.assertIsNotNone(world.story_timeline.get_event("ev_replacement"))

    def test_16_goal_changes_go_through_goal_manager(self):
        world = mini_world()
        report = night_director.normalize_report({
            "goal_changes": [{"npc_id": "npc_2", "action": "add", "goal": "investigate:mayor",
                              "urgency": "urgent", "reason": "night"}],
        })
        night_director.apply_daily_report(world, report)
        ren = world.get_npc("npc_2")
        self.assertIn("investigate:mayor", ren.goals)
        self.assertEqual(ren.goal_states["investigate:mayor"]["source"], "night_director")

    def test_17_story_updates_go_through_story_timeline(self):
        world = mini_world()
        add_event(world, "ev_update", trigger_at=1000.0)
        report = night_director.normalize_report({
            "story_updates": [{"event_id": "ev_update", "trigger_at": 5000.0}],
        })
        night_director.apply_daily_report(world, report)
        event = world.story_timeline.get_event("ev_update")
        self.assertEqual(event["trigger_at"], 5000.0)


class CorruptionCaseTests(unittest.TestCase):
    def _open_world(self):
        from open_world import create_open_world
        return create_open_world()

    def test_18_ren_death_marks_pending_events(self):
        world = self._open_world()
        world.characters_state["npc_2"]["alive"] = False
        devs = detect_deviations(world)
        self.assertTrue(any(d.kind == KIND_NPC_DEAD and d.npc_id == "npc_2" for d in devs))

    def test_19_ren_escort_still_cancels_ren_lost(self):
        world = self._open_world()
        ren = world.get_npc("npc_2")
        ren.position_override = [70.0, 70.0]
        world.player.x, world.player.y = 70.0, 70.0
        ren.goals = ["follow player"]
        event = world.story_timeline.get_event("ren_lost")
        event["status"] = "pending"
        event["trigger_at"] = 0.0
        event["trigger_time"] = 0.0
        world.check_story_events()
        self.assertEqual(event["status"], "cancelled")

    def test_20_player_takes_ledger_context_and_replace(self):
        world = self._open_world()
        world.player.x, world.player.y = 22.0, 22.0
        world.pick("item_ledger")
        self.assertIn("item_ledger", world.player.inventory)

        # The ledger still exists (held by the player): no false dependency error.
        devs = detect_deviations(world)
        self.assertFalse(any(
            d.kind == KIND_DEPENDENCY_MISSING and d.item_id == "item_ledger" for d in devs
        ))

        # The Night Director context exposes it, so the Director can re-plan.
        context = night_director.build_daily_director_context(world)
        self.assertIn("item_ledger", context["player"]["inventory"])
        report = night_director.normalize_report({
            "story_events": [{
                "event_id": "ledger_reaction", "title": "ledger reaction",
                "description": "the ledger moved", "trigger_at": world.time + 3600,
                "effects": [{"type": "set_belief", "npc_id": "npc_1", "key": "ledger_taken", "value": 1.0}],
            }],
            "reasoning_summary": "player holds the ledger",
        })
        applied = night_director.apply_daily_report(world, report)
        self.assertTrue(applied["story_events"][0]["added"])
        self.assertIsNotNone(world.story_timeline.get_event("ledger_reaction"))


if __name__ == "__main__":
    unittest.main()
