"""Constrained Event DSL, validator, and executor for world changes."""

import math

import npc_movement_intent

from entities import Item, NPC
from evidence import make_record as _make_evidence_record
from schedule import default_schedule


TOP_LEVEL_KEYS = frozenset({"event_id", "time", "effects"})

EFFECT_REQUIRED_FIELDS = {
    "add_memory": frozenset({"npc_id", "content"}),
    "set_belief": frozenset({"npc_id", "key", "value"}),
    "change_relationship": frozenset({"npc_id", "target_id", "delta"}),
    "add_goal": frozenset({"npc_id", "goal"}),
    "remove_goal": frozenset({"npc_id", "goal"}),
    "move_npc": frozenset({"npc_id", "x", "y"}),
    "spawn_item": frozenset({"item_id", "item_type", "x", "y"}),
    "add_evidence": frozenset({"evidence_id", "evidence_type", "x", "y", "description"}),
    "dialogue": frozenset({"speaker_id", "content"}),
    "set_follow_target": frozenset({"npc_id", "follow_target"}),
    "create_npc": frozenset({"name", "role", "personality", "goal", "x", "y"}),
    "add_history": frozenset({"action", "target", "content"}),
    "set_character_state": frozenset({"npc_id", "key", "value"}),
    "change_character_state": frozenset({"npc_id", "key", "delta"}),
    "fail_quest": frozenset({"quest_id"}),
}

EFFECT_OPTIONAL_FIELDS = {
    "add_goal": frozenset({"priority", "urgency", "source", "reason"}),
    "remove_goal": frozenset({"source", "reason"}),
    "set_follow_target": frozenset({"urgency", "source", "reason"}),
    "create_npc": frozenset({"vision_range", "background"}),
    "add_history": frozenset({"actor"}),
    "add_evidence": frozenset({
        "source_event",
        "linked_belief",
        "belief_delta",
        "follow_up_goal",
        "related_npc",
        "related_object",
    }),
}

SUPPORTED_EFFECT_TYPES = frozenset(EFFECT_REQUIRED_FIELDS)


class EventValidationError(ValueError):
    """Raised when an Event fails structural or world validation."""


class EventValidator:
    """Validate one Event against the DSL schema and the current world."""

    def validate(self, world, event):
        if not isinstance(event, dict):
            raise EventValidationError("Event must be an object.")

        unknown = set(event) - TOP_LEVEL_KEYS
        if unknown:
            raise EventValidationError(
                f"Unknown event fields: {sorted(unknown)}."
            )

        event_id = _validate_string(event.get("event_id"), "event_id")
        time = _validate_time(event.get("time"))
        effects = event.get("effects")
        if not isinstance(effects, list) or not effects:
            raise EventValidationError("Event effects must be a non-empty list.")

        normalized_effects = []
        for effect in effects:
            normalized_effects.append(self._validate_effect(world, effect))

        return {
            "event_id": event_id,
            "time": time,
            "effects": normalized_effects,
        }

    def _validate_effect(self, world, effect):
        if not isinstance(effect, dict):
            raise EventValidationError("Each effect must be an object.")
        effect_type = effect.get("type")
        if not isinstance(effect_type, str) or effect_type not in SUPPORTED_EFFECT_TYPES:
            raise EventValidationError(f"Unknown effect type: {effect_type}.")

        optional_fields = EFFECT_OPTIONAL_FIELDS.get(effect_type, frozenset())
        allowed_fields = {"type"} | EFFECT_REQUIRED_FIELDS[effect_type] | optional_fields
        unknown_fields = set(effect) - allowed_fields
        if unknown_fields:
            raise EventValidationError(
                f"Unknown fields for {effect_type}: {sorted(unknown_fields)}."
            )
        for field in EFFECT_REQUIRED_FIELDS[effect_type]:
            if field not in effect:
                raise EventValidationError(
                    f"Missing field for {effect_type}: {field}."
                )

        validator = getattr(self, f"_validate_{effect_type}")
        validated = validator(world, effect)
        validated["type"] = effect_type
        return validated

    def _validate_add_memory(self, world, effect):
        npc_id = _require_npc(world, _validate_string(effect["npc_id"], "npc_id"))
        content = _validate_content(effect["content"])
        return {"npc_id": npc_id, "content": content}

    def _validate_set_belief(self, world, effect):
        npc_id = _require_npc(world, _validate_string(effect["npc_id"], "npc_id"))
        key = _validate_string(effect["key"], "key")
        value = _validate_probability(effect["value"])
        return {"npc_id": npc_id, "key": key, "value": value}

    def _validate_change_relationship(self, world, effect):
        npc_id = _require_npc(world, _validate_string(effect["npc_id"], "npc_id"))
        target_id = _validate_relationship_target(
            world, _validate_string(effect["target_id"], "target_id")
        )
        delta = _validate_number(effect["delta"], "delta")
        return {"npc_id": npc_id, "target_id": target_id, "delta": delta}

    def _validate_add_goal(self, world, effect):
        npc_id = _require_npc(world, _validate_string(effect["npc_id"], "npc_id"))
        goal = _validate_string(effect["goal"], "goal")
        normalized = {"npc_id": npc_id, "goal": goal}
        if "priority" in effect:
            normalized["priority"] = _validate_number(effect["priority"], "priority")
        if "urgency" in effect:
            from director_input import normalize_urgency
            normalized["urgency"] = normalize_urgency(effect["urgency"])
        if "source" in effect:
            normalized["source"] = _validate_string(effect["source"], "source")
        if "reason" in effect:
            normalized["reason"] = _validate_string(effect["reason"], "reason")
        return normalized

    def _validate_remove_goal(self, world, effect):
        return self._validate_add_goal(world, effect)

    def _validate_move_npc(self, world, effect):
        npc_id = _require_npc(world, _validate_string(effect["npc_id"], "npc_id"))
        x = _validate_coordinate(effect["x"], "x")
        y = _validate_coordinate(effect["y"], "y")
        return {"npc_id": npc_id, "x": x, "y": y}

    def _validate_spawn_item(self, world, effect):
        item_id = _validate_string(effect["item_id"], "item_id")
        item_type = _validate_string(effect["item_type"], "item_type")
        x = _validate_coordinate(effect["x"], "x")
        y = _validate_coordinate(effect["y"], "y")
        if world.get_item(item_id) is not None:
            raise EventValidationError(f"Item already exists: {item_id}.")
        return {"item_id": item_id, "item_type": item_type, "x": x, "y": y}

    def _validate_add_evidence(self, world, effect):
        evidence_id = _validate_string(effect["evidence_id"], "evidence_id")
        if any(str(entry.get("id")) == evidence_id for entry in world.evidence):
            raise EventValidationError(f"Evidence already exists: {evidence_id}.")
        evidence_type = _validate_string(effect["evidence_type"], "evidence_type")
        x = _validate_coordinate(effect["x"], "x")
        y = _validate_coordinate(effect["y"], "y")
        description = _validate_content(effect["description"])
        normalized = {
            "evidence_id": evidence_id,
            "evidence_type": evidence_type,
            "x": x,
            "y": y,
            "description": description,
        }
        if "source_event" in effect:
            normalized["source_event"] = _validate_string(effect["source_event"], "source_event")
        if "linked_belief" in effect:
            normalized["linked_belief"] = _validate_string(effect["linked_belief"], "linked_belief")
        if "belief_delta" in effect:
            normalized["belief_delta"] = _validate_number(effect["belief_delta"], "belief_delta")
        if "follow_up_goal" in effect:
            normalized["follow_up_goal"] = _validate_string(effect["follow_up_goal"], "follow_up_goal")
        if "related_npc" in effect:
            normalized["related_npc"] = _require_npc(
                world, _validate_string(effect["related_npc"], "related_npc")
            )
        if "related_object" in effect:
            normalized["related_object"] = _validate_string(effect["related_object"], "related_object")
        return normalized

    def _validate_create_npc(self, world, effect):
        name = _validate_string(effect["name"], "name")
        role = _validate_string(effect["role"], "role")
        personality = _validate_string_list(effect["personality"], "personality")
        background = effect.get("background", "")
        if not isinstance(background, str):
            raise EventValidationError("background must be a string.")
        goal = _validate_string(effect["goal"], "goal")
        x = _validate_coordinate(effect["x"], "x")
        y = _validate_coordinate(effect["y"], "y")
        vision_range = _validate_vision_range(effect.get("vision_range", 5.0))
        return {
            "name": name,
            "role": role,
            "personality": personality,
            "background": background,
            "goal": goal,
            "x": x,
            "y": y,
            "vision_range": vision_range,
        }

    def _validate_dialogue(self, world, effect):
        speaker_id = _validate_speaker(
            world, _validate_string(effect["speaker_id"], "speaker_id")
        )
        content = _validate_content(effect["content"])
        return {"speaker_id": speaker_id, "content": content}

    def _validate_set_follow_target(self, world, effect):
        npc_id = _require_npc(world, _validate_string(effect["npc_id"], "npc_id"))
        follow_target = effect["follow_target"]
        if follow_target is not None:
            follow_target = _validate_relationship_target(world, _validate_string(follow_target, "follow_target"))
        return {"npc_id": npc_id, "follow_target": follow_target}


    def _validate_add_history(self, world, effect):
        action = _validate_string(effect["action"], "action")
        target = _validate_string(effect["target"], "target")
        content = _validate_content(effect["content"])
        actor = _validate_string(effect.get("actor", "story"), "actor")
        return {"action": action, "target": target, "content": content, "actor": actor}

    def _validate_set_character_state(self, world, effect):
        npc_id = _require_npc(world, _validate_string(effect["npc_id"], "npc_id"))
        key = _validate_string(effect["key"], "key")
        value = effect["value"]
        if isinstance(value, float) and not math.isfinite(value):
            raise EventValidationError("value must be finite.")
        if not isinstance(value, (str, bool, int, float)):
            raise EventValidationError("value must be a string, boolean or number.")
        return {"npc_id": npc_id, "key": key, "value": value}

    def _validate_change_character_state(self, world, effect):
        npc_id = _require_npc(world, _validate_string(effect["npc_id"], "npc_id"))
        key = _validate_string(effect["key"], "key")
        delta = _validate_number(effect["delta"], "delta")
        return {"npc_id": npc_id, "key": key, "delta": delta}

    def _validate_fail_quest(self, world, effect):
        quest_id = _validate_string(effect["quest_id"], "quest_id")
        return {"quest_id": quest_id}


class EventExecutor:
    """Execute validated events and record them in world event_history."""

    def __init__(self, validator=None):
        self.validator = validator or EventValidator()
        # Divergences found during the most recent execute(); the Director
        # reads this to decide whether to keep/modify/cancel the event.
        self.last_move_defers = []
        # Step 12.4: goal effects are routed through GoalManager so StoryEvent
        # goals share one lifecycle. GoalManager disables this flag for its own
        # Event DSL write so the goal is only written once.
        self._goal_lifecycle = True

    def execute(self, world, event, goal_lifecycle=None, record_history=True):
        normalized = self.validator.validate(world, event)
        self.last_move_defers = []
        previous_lifecycle = self._goal_lifecycle
        if goal_lifecycle is not None:
            self._goal_lifecycle = bool(goal_lifecycle)
        try:
            for effect in normalized["effects"]:
                executor = getattr(self, f"_execute_{effect['type']}")
                executor(world, effect, normalized)
        finally:
            self._goal_lifecycle = previous_lifecycle

        if record_history:
            world.event_history.append(normalized)
        return normalized

    def _execute_add_memory(self, world, effect, event):
        record = {
            "time": event["time"],
            "source": "event",
            "event_id": event["event_id"],
            "content": effect["content"],
        }
        world.add_memory(effect["npc_id"], record)

    def _execute_set_belief(self, world, effect, event):
        world.set_belief(effect["npc_id"], effect["key"], effect["value"])

    def _execute_change_relationship(self, world, effect, event):
        world.change_relationship(
            effect["npc_id"], effect["target_id"], effect["delta"]
        )

    def _goal_source(self, world, event, effect):
        """Where a StoryEvent/Event DSL goal came from (Step 12.4)."""
        source = effect.get("source")
        if isinstance(source, str) and source.strip():
            return source.strip()
        event_id = (event or {}).get("event_id")
        timeline = getattr(world, "story_timeline", None)
        if timeline is not None and event_id:
            try:
                entry = timeline.get_event(event_id)
            except Exception:
                entry = None
            if entry is not None:
                entry_source = entry.get("source")
                if isinstance(entry_source, str) and entry_source.strip() in {"ai_director", "story_event"}:
                    return entry_source.strip()
        return "story_event"

    def _execute_add_goal(self, world, effect, event):
        if self._goal_lifecycle:
            from goal_manager import apply_goal_effect
            apply_goal_effect(
                world,
                effect["npc_id"],
                effect["goal"],
                priority=effect.get("priority"),
                urgency=effect.get("urgency", "normal"),
                source=self._goal_source(world, event, effect),
                reason=effect.get("reason", ""),
            )
            return
        world.add_goal(effect["npc_id"], effect["goal"])

    def _execute_remove_goal(self, world, effect, event):
        if self._goal_lifecycle:
            from goal_manager import apply_goal_effect
            apply_goal_effect(
                world,
                effect["npc_id"],
                effect["goal"],
                change_type="remove",
                source=self._goal_source(world, event, effect),
                reason=effect.get("reason", ""),
            )
            return
        world.remove_goal(effect["npc_id"], effect["goal"])

    def _execute_move_npc(self, world, effect, event):
        npc = world.get_npc(effect["npc_id"])
        destination = (effect["x"], effect["y"])
        # A scripted move is an expectation, not a command over the NPC. The
        # NPC's own active goals (leading/following/reaching) are authoritative.
        conflict = self._scripted_move_conflict(world, npc, destination)
        if conflict is not None:
            # Do not fight the NPC's world state by teleporting it back to the
            # script's old assumption. Record the divergence so the AI Director
            # can re-plan the event around where the NPC actually is.
            npc.add_memory({
                "time": world.time,
                "source": "director",
                "content": (
                    f"Scripted move to {destination} diverged from my goal "
                    f"{conflict.get('goal')}; it was left to the director."
                ),
                "goal_type": conflict.get("goal_type"),
            })
            self.last_move_defers.append({
                "npc_id": npc.id,
                "npc_name": npc.name,
                "scripted_destination": destination,
                "committed_goal": conflict.get("goal"),
                "committed_target": conflict.get("target"),
                "event_id": event.get("event_id"),
            })
            return
        npc.position_override = [destination[0], destination[1]]
        npc.x = destination[0]
        npc.y = destination[1]

    def _scripted_move_conflict(self, world, npc, destination):
        if npc is None:
            return None
        planner = getattr(world, "npc_planner", None)
        resolver = getattr(planner, "resolve_location", None)
        if resolver is None:
            # Fall back to the world's map resolver when no planner is wired.
            def resolver(location):
                aliases = getattr(world, "LOCATION_ALIASES", None) or {}
                locations = getattr(world, "EXPLORE_LOCATIONS", None) or {}
                key = aliases.get(str(location).lower(), location)
                return locations.get(key)
        return npc_movement_intent.movement_conflict(
            world, npc, destination, resolver
        )

    def _execute_spawn_item(self, world, effect, event):
        world.items.append(
            Item(
                id=effect["item_id"],
                type=effect["item_type"],
                x=effect["x"],
                y=effect["y"],
                holder=None,
            )
        )

    def _execute_add_evidence(self, world, effect, event):
        world.evidence.append(_make_evidence_record(effect, event))

    def _execute_create_npc(self, world, effect, event):
        npc_id = world.next_npc_id()
        world.characters_state[npc_id] = {
            "alive": True,
            "injured": False,
            "unconscious": False,
            "health": 100,
            "location": "village",
            "rescued": False,
        }
        world.npcs.append(
            NPC(
                id=npc_id,
                name=effect["name"],
                role=effect["role"],
                personality=effect["personality"],
                background=effect["background"],
                x=effect["x"],
                y=effect["y"],
                vision_range=effect["vision_range"],
                goals=[effect["goal"]],
                character_seed={
                    "personality": list(effect["personality"]),
                    "long_term_goal": effect["goal"],
                    "secret": "",
                    "fear": "",
                    "conflict": "",
                },
                schedule=default_schedule(),
                position_override=[effect["x"], effect["y"]],
            )
        )
        return npc_id

    def _execute_add_history(self, world, effect, event):
        world.add_history(
            effect["action"],
            effect["target"],
            effect["content"],
            actor=effect["actor"],
        )

    def _execute_set_character_state(self, world, effect, event):
        state = world.characters_state.setdefault(effect["npc_id"], {})
        state[effect["key"]] = effect["value"]

    def _execute_change_character_state(self, world, effect, event):
        state = world.characters_state.setdefault(effect["npc_id"], {})
        current = state.get(effect["key"], 0)
        state[effect["key"]] = max(0.0, float(current) + float(effect["delta"]))

    def _execute_fail_quest(self, world, effect, event):
        quest = world.get_quest(effect["quest_id"])
        if quest is not None and quest.status == "active":
            world.fail_quest(effect["quest_id"])

    def _execute_dialogue(self, world, effect, event):
        world.dialogues.append(
            {
                "time": event["time"],
                "event_id": event["event_id"],
                "speaker_id": effect["speaker_id"],
                "content": effect["content"],
            }
        )

    def _execute_set_follow_target(self, world, effect, event):
        follow_target = effect["follow_target"]
        if self._goal_lifecycle:
            from goal_manager import apply_follow_effect
            apply_follow_effect(
                world,
                effect["npc_id"],
                follow_target,
                urgency=effect.get("urgency", "normal"),
                source=self._goal_source(world, event, effect),
                reason=effect.get("reason", ""),
            )
            return
        npc = world.get_npc(effect["npc_id"])
        following = follow_target is not None
        state = world.characters_state.setdefault(effect["npc_id"], {})
        state["following_player"] = following
        state["follow_player"] = following
        state["follow_target"] = follow_target
        if following:
            npc.add_goal("follow player")
        else:
            npc.remove_goal("follow player")


def execute_event(world, event, goal_lifecycle=None, record_history=True):
    """Convenience wrapper for the default validator and executor."""
    return EventExecutor().execute(
        world, event, goal_lifecycle=goal_lifecycle, record_history=record_history
    )


def _validate_string_list(value, label):
    if not isinstance(value, list):
        raise EventValidationError(f"{label} must be a list of strings.")
    normalized = []
    for item in value:
        normalized.append(_validate_string(item, label))
    return normalized


def _validate_vision_range(value):
    number = _validate_number(value, "vision_range")
    if number < 0.0:
        raise EventValidationError("vision_range must be non-negative.")
    return number

def _validate_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise EventValidationError(f"{label} must be a non-empty string.")
    return value


def _validate_content(value):
    return _validate_string(value, "content")


def _validate_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EventValidationError(f"{label} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise EventValidationError(f"{label} must be finite.")
    return number


def _validate_probability(value):
    number = _validate_number(value, "value")
    if not 0.0 <= number <= 1.0:
        raise EventValidationError("value must be between 0 and 1.")
    return number


def _validate_time(value):
    number = _validate_number(value, "time")
    if number < 0.0:
        raise EventValidationError("time must be non-negative.")
    return number


def _validate_coordinate(value, axis):
    number = _validate_number(value, axis)
    if not 0.0 <= number <= 100.0:
        raise EventValidationError(f"{axis} must be between 0 and 100.")
    return number


def _require_npc(world, npc_id):
    if world.get_npc(npc_id) is None:
        raise EventValidationError(f"NPC not found: {npc_id}.")
    return npc_id


def _validate_relationship_target(world, target_id):
    if target_id == "player" or world.get_npc(target_id) is not None:
        return target_id
    raise EventValidationError(f"Relationship target not found: {target_id}.")


def _validate_speaker(world, speaker_id):
    if speaker_id == "player" or world.get_npc(speaker_id) is not None:
        return speaker_id
    raise EventValidationError(f"Dialogue speaker not found: {speaker_id}.")

