"""Deterministic rule baseline: a finite pre-written event catalog."""

import json

from director import Director, build_world_context, summarize_context
from event import EventValidator


BASELINE_EVENT_LABELS = (
    "find_item",
    "npc_talk",
    "npc_move",
    "item_spawn",
    "relationship_change",
)


class BaselineDirector(Director):
    """Choose one pre-defined event from a small catalog, without any LLM."""

    def __init__(self):
        self.last_log = {}

    def generate_event(self, world, context=None):
        payload = build_world_context(world, context)
        npc_id = world.npcs[0].id if world.npcs else "player"
        index = int(world.time) % len(BASELINE_EVENT_LABELS)
        label = BASELINE_EVENT_LABELS[index]

        if label == "find_item":
            effects = [
                {"type": "add_goal", "npc_id": npc_id, "goal": "find_item"}
            ]
        elif label == "npc_talk":
            effects = [
                {
                    "type": "dialogue",
                    "speaker_id": npc_id,
                    "content": "Baseline NPC reacts to the player.",
                }
            ]
        elif label == "npc_move":
            effects = [
                {"type": "move_npc", "npc_id": npc_id, "x": 30.0, "y": 40.0}
            ]
        elif label == "item_spawn":
            item_id = _next_baseline_item_id(world)
            effects = [
                {
                    "type": "spawn_item",
                    "item_id": item_id,
                    "item_type": "baseline_item",
                    "x": 45.0,
                    "y": 55.0,
                }
            ]
        else:
            effects = [
                {
                    "type": "change_relationship",
                    "npc_id": npc_id,
                    "target_id": "player",
                    "delta": -0.1,
                }
            ]

        event = {
            "event_id": f"baseline_{int(world.time)}_{label}",
            "time": world.time,
            "effects": effects,
        }
        EventValidator().validate(world, event)
        self.last_log = {
            "source": "Baseline",
            "model": None,
            "input_summary": summarize_context(payload),
            "raw_json": json.dumps(event, ensure_ascii=False),
            "validator_result": "valid",
            "executed": False,
            "failure_reason": None,
        }
        return event


def _next_baseline_item_id(world):
    index = 0
    while world.get_item(f"baseline_item_{index}") is not None:
        index += 1
    return f"baseline_item_{index}"