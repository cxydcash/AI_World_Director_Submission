"""Unified Action Layer for V3.

The layer is intentionally thin:

Intent -> Action -> ActionValidator -> ActionExecutor -> WorldState

It does not implement Intent parsing, NPC planning, Timeline logic, or a new
Story Director. Existing implementations are adapted behind the executor.
"""

import json
from dataclasses import dataclass, field

from event import execute_event
from director_input import DirectorInput, SOURCE_NPC_DISCOVERY, SOURCE_PLAYER_ACTION
from goal_manager import route_director_input
from evidence import (
    DISCOVERY_REACH_BONUS,
    by_reference,
    discover,
    find_in_range,
    record_id,
)
from world import EXPLORE_LOCATIONS, PICKUP_RADIUS, SHOP_ITEMS
from world_objects import WorldObjectRegistry


SUPPORTED_ACTION_TYPES = {
    "move",
    "inspect",
    "search",
    "take",
    "drop",
    "open",
    "talk",
    "follow",
    "stop_follow",
    "ask",
    "investigate",
    "give",
    "buy",
    "work",
    "explore",
    "wait",
    "attack",
    "rescue",
    "hide",
    "create_rumor",
    "conversation_only",
}


@dataclass
class Action:
    """Typed, executable proposal produced from an Intent."""

    action_type: str
    actor_id: str = "player"
    target_id: str | None = None
    parameters: dict = field(default_factory=dict)
    source_intent: dict = field(default_factory=dict)
    raw_text: str = ""
    reason: str = ""

    def to_dict(self):
        return {
            "action_type": self.action_type,
            "actor_id": self.actor_id,
            "target_id": self.target_id,
            "parameters": dict(self.parameters),
            "source_intent": dict(self.source_intent),
            "raw_text": self.raw_text,
            "reason": self.reason,
        }


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""
    normalized_parameters: dict = field(default_factory=dict)


@dataclass
class ExecutionResult:
    success: bool
    message: str = ""
    action: Action | None = None
    data: dict = field(default_factory=dict)
    error: str | None = None


class ActionBuilder:
    """Map an already-resolved Intent to an Action without parsing text."""

    def build(self, intent):
        intent_type = intent.intent_type if hasattr(intent, "intent_type") else intent.get("intent_type")
        if intent_type not in SUPPORTED_ACTION_TYPES:
            intent_type = "unknown"
        parameters = dict(
            intent.parameters if hasattr(intent, "parameters") else intent.get("parameters", {})
        )
        source_intent = intent.to_dict() if hasattr(intent, "to_dict") else dict(intent)
        return Action(
            action_type=intent_type,
            actor_id=getattr(intent, "actor_id", source_intent.get("actor_id", "player")),
            target_id=getattr(intent, "target_id", source_intent.get("target_id")),
            parameters=parameters,
            source_intent=source_intent,
            raw_text=getattr(intent, "raw_text", source_intent.get("raw_text", "")),
            reason=getattr(intent, "reason", source_intent.get("reason", "")),
        )


class ActionValidator:
    """Check whether an Action is legal under the current WorldState."""

    def __init__(self, object_registry=None):
        self.object_registry = object_registry or WorldObjectRegistry()

    def validate(self, world, action):
        if not isinstance(action, Action):
            return ValidationResult(False, "Action must be an Action object.")
        if action.action_type not in SUPPORTED_ACTION_TYPES:
            return ValidationResult(False, f"Unsupported action type: {action.action_type}.")
        method = getattr(self, f"_validate_{action.action_type}", None)
        if method is None:
            return ValidationResult(False, f"Action is not executable yet: {action.action_type}.")
        try:
            return method(world, action)
        except (TypeError, ValueError, KeyError) as exc:
            return ValidationResult(False, f"Invalid action parameters: {exc}")

    def _validate_move(self, world, action):
        try:
            x = float(action.parameters.get("x", action.parameters.get("to", [None, None])[0]))
            y = float(action.parameters.get("y", action.parameters.get("to", [None, None])[1]))
        except (TypeError, ValueError, IndexError):
            return ValidationResult(False, "Move requires x and y coordinates.")
        if not 0.0 <= x <= 100.0 or not 0.0 <= y <= 100.0:
            return ValidationResult(False, "Move coordinates must be between 0 and 100.")
        return ValidationResult(True, normalized_parameters={"x": x, "y": y})

    def _validate_talk(self, world, action):
        npc = world.get_npc(action.target_id)
        if npc is None:
            return ValidationResult(False, f"NPC not found: {action.target_id}.")
        if world.distance_between(self._actor_position(world, action), world.npc_position(npc.id)) > 5.0:
            return ValidationResult(False, "NPC is not nearby.")
        return ValidationResult(True, normalized_parameters={"npc_id": npc.id})

    def _validate_follow(self, world, action):
        npc = world.get_npc(action.target_id)
        if npc is None:
            return ValidationResult(False, f"NPC not found: {action.target_id}.")
        if action.raw_text:
            # Last-resort safety net: follow is world-changing, so the player
            # text must be an explicit order, never a request/question.
            from intent import is_explicit_follow_command
            if not is_explicit_follow_command(action.raw_text):
                return ValidationResult(
                    False,
                    "follow requires an explicit command such as 跟我走 / 跟着我.",
                )
        state = world.characters_state.get(npc.id, {})
        actor_position = self._actor_position(world, action)
        if not state.get("alive", True):
            return ValidationResult(False, "Target NPC is not alive.")
        if state.get("fled"):
            return ValidationResult(False, "Target NPC has fled.")
        if npc.relationships.get("player", 0.0) < 0.3:
            return ValidationResult(False, "NPC relationship is too low for follow.")
        if world.distance_between(actor_position, world.npc_position(npc.id)) > 12.0:
            return ValidationResult(False, "Target NPC is too far away to follow.")
        return ValidationResult(True, normalized_parameters={"npc_id": npc.id, "follow_target": "player"})

    def _validate_stop_follow(self, world, action):
        npc = world.get_npc(action.target_id)
        if npc is None:
            return ValidationResult(False, f"NPC not found: {action.target_id}.")
        state = world.characters_state.get(npc.id, {})
        if not (state.get("following_player") or state.get("follow_player") or "follow player" in npc.goals):
            return ValidationResult(False, "Target NPC is not following the player.")
        return ValidationResult(True, normalized_parameters={"npc_id": npc.id, "follow_target": None})

    def _validate_inspect(self, world, action):
        obj = self.object_registry.get(action.target_id)
        if obj is None:
            return ValidationResult(False, f"Object not found: {action.target_id}.")
        if not self._object_accessible(world, action, obj):
            return ValidationResult(False, "Object is not currently visible/accessible.")
        return ValidationResult(True, normalized_parameters={"object_id": obj["id"]})

    def _validate_search(self, world, action):
        if action.target_id == "__location__":
            return ValidationResult(True, normalized_parameters={"object_id": "__location__"})
        return self._validate_inspect(world, action)

    def _validate_take(self, world, action):
        target_id = "item_ledger" if action.target_id == "ledger" else action.target_id
        item = world.get_item(target_id)
        if item is None and target_id:
            for candidate in world.items:
                if candidate.type == action.target_id:
                    item = candidate
                    break
        if item is None:
            return ValidationResult(False, f"Item not found: {action.target_id}.")
        if item.holder is not None:
            return ValidationResult(False, "Item is already held.")
        if world.distance_between(self._actor_position(world, action), item) > PICKUP_RADIUS:
            return ValidationResult(False, "Item is too far away.")
        return ValidationResult(True, normalized_parameters={"item_id": item.id})

    def _validate_open(self, world, action):
        obj = self.object_registry.get(action.target_id)
        if obj is None:
            return ValidationResult(False, f"Object not found: {action.target_id}.")
        state = getattr(world, "object_state", {}) or {}
        if action.target_id == "mayor_hidden_drawer" and not state.get("drawer_open"):
            return ValidationResult(False, "Drawer is locked.")
        if state.get(action.target_id):
            return ValidationResult(False, "Object is already open.")
        if not self._object_accessible(world, action, obj):
            return ValidationResult(False, "Object is not currently accessible.")
        return ValidationResult(True, normalized_parameters={"object_id": obj["id"]})

    def _validate_give(self, world, action):
        item_id = action.parameters.get("item_id")
        if item_id not in world.player.inventory:
            return ValidationResult(False, "Item is not in player inventory.")
        npc = world.get_npc(action.target_id)
        if npc is None:
            return ValidationResult(False, f"NPC not found: {action.target_id}.")
        if world.distance_between(world.player, world.npc_position(npc.id)) > 5.0:
            return ValidationResult(False, "Target NPC is not nearby.")
        return ValidationResult(True, normalized_parameters={"item_id": item_id, "target": npc.id})

    def _validate_drop(self, world, action):
        item_id = action.parameters.get("item_id")
        if item_id not in world.player.inventory:
            return ValidationResult(False, "Item is not in player inventory.")
        x = action.parameters.get("x")
        y = action.parameters.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return ValidationResult(False, "Drop requires numeric x and y.")
        if not 0 <= float(x) <= 100 or not 0 <= float(y) <= 100:
            return ValidationResult(False, "Drop coordinates must be between 0 and 100.")
        return ValidationResult(True, normalized_parameters={"item_id": item_id, "x": float(x), "y": float(y)})

    def _validate_wait(self, world, action):
        seconds = action.parameters.get("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0:
            return ValidationResult(False, "Wait requires a non-negative number of seconds.")
        return ValidationResult(True, normalized_parameters={"seconds": float(seconds)})

    def _validate_attack(self, world, action):
        return self._validate_npc_nearby(world, action)

    def _validate_rescue(self, world, action):
        return self._validate_npc_nearby(world, action)

    def _validate_hide(self, world, action):
        item_id = action.parameters.get("item_id")
        if item_id not in world.player.inventory:
            return ValidationResult(False, "Item is not in player inventory.")
        return ValidationResult(True, normalized_parameters={"item_id": item_id})

    def _validate_create_rumor(self, world, action):
        return self._validate_npc_nearby(world, action)

    def _validate_buy(self, world, action):
        item_id = action.parameters.get("item_id")
        if item_id not in SHOP_ITEMS:
            return ValidationResult(False, f"Unknown shop item: {item_id}.")
        if world.player.money < SHOP_ITEMS[item_id]["price"]:
            return ValidationResult(False, "Not enough money.")
        return ValidationResult(True, normalized_parameters={"item_id": item_id})

    def _validate_work(self, world, action):
        return ValidationResult(True)

    def _validate_explore(self, world, action):
        if world.current_explore_location() is None:
            return ValidationResult(False, "Player is not at an explorable location.")
        return ValidationResult(True)

    def _validate_conversation_only(self, world, action):
        if action.target_id and world.get_npc(action.target_id) is None:
            return ValidationResult(False, f"NPC not found: {action.target_id}.")
        return ValidationResult(True, normalized_parameters={"npc_id": action.target_id})

    def _validate_ask(self, world, action):
        return self._validate_npc_nearby(world, action)

    def _validate_investigate(self, world, action):
        if action.target_id is None:
            return ValidationResult(False, "Investigate requires a target.")
        normalized = {"target_id": action.target_id}
        # An NPC can only resolve evidence it can actually reach from where it
        # stands; the player keeps the legacy pass-through behaviour.
        if action.actor_id != "player":
            npc = world.get_npc(action.actor_id)
            if npc is not None:
                reach = float(npc.vision_range) + DISCOVERY_REACH_BONUS
                position = self._actor_position(world, action)
                matches = find_in_range(world, position, reach, action.target_id)
                if not matches and action.target_id == "__location__":
                    matches = find_in_range(world, position, reach, None)
                if matches:
                    index, entry = matches[0]
                    reference = record_id(entry, index)
                    normalized["evidence_id"] = reference
                    normalized["target_id"] = reference
        return ValidationResult(True, normalized_parameters=normalized)

    def _validate_npc_nearby(self, world, action):
        npc = world.get_npc(action.target_id)
        if npc is None:
            return ValidationResult(False, f"NPC not found: {action.target_id}.")
        if world.distance_between(self._actor_position(world, action), world.npc_position(npc.id)) > 5.0:
            return ValidationResult(False, "NPC is not nearby.")
        return ValidationResult(True, normalized_parameters={"npc_id": npc.id})

    def _actor_position(self, world, action):
        if action.actor_id == "player":
            return (world.player.x, world.player.y)
        npc = world.get_npc(action.actor_id)
        if npc is None:
            raise ValueError(f"Actor NPC not found: {action.actor_id}.")
        return world.npc_position(npc.id)

    def _object_accessible(self, world, action, obj):
        location = obj.get("location")
        target = EXPLORE_LOCATIONS.get(location)
        if target is None:
            return True
        return world.distance_between(self._actor_position(world, action), target) <= 12.0

    def _visible_objects(self, world):
        return self.object_registry.visible_objects(world)


class ActionExecutor:
    """Execute validated Actions. Only this layer commits Action changes."""

    def __init__(self, validator=None):
        self.validator = validator or ActionValidator()

    def execute(self, world, action, validation=None, runtime=None):
        validation = validation or self.validator.validate(world, action)
        if not validation.valid:
            return ExecutionResult(False, validation.reason, action, error=validation.reason)
        handler = getattr(self, f"_execute_{action.action_type}", None)
        if handler is None:
            return ExecutionResult(False, f"Action executor is not implemented: {action.action_type}", action)
        try:
            return handler(world, action, validation.normalized_parameters, runtime)
        except Exception as exc:
            return ExecutionResult(False, f"Action execution failed: {exc}", action, error=str(exc))

    def _execute_move(self, world, action, parameters, runtime):
        if action.actor_id == "player":
            success, message = world.move(parameters["x"], parameters["y"])
            return ExecutionResult(success, message, action)
        npc = world.get_npc(action.actor_id)
        if npc is None:
            return ExecutionResult(False, f"NPC not found: {action.actor_id}.", action)
        current = world.npc_position(npc.id)
        target = (parameters["x"], parameters["y"])
        distance = world.distance_between(current, target)
        if distance <= 1.5:
            npc.current_action = {"action_type": "move", "parameters": parameters, "complete": True}
            return ExecutionResult(True, f"{npc.name} reached the target.", action, data={"position": list(target)})
        step = min(2.0, distance)
        if distance == 0:
            return ExecutionResult(True, f"{npc.name} is already there.", action)
        npc.position_override = [
            current[0] + (target[0] - current[0]) / distance * step,
            current[1] + (target[1] - current[1]) / distance * step,
        ]
        npc.x, npc.y = npc.position_override
        npc.current_action = {"action_type": "move", "parameters": parameters, "complete": False, "source_intent": action.source_intent}
        return ExecutionResult(True, f"{npc.name} moved toward the target.", action, data={"position": list(npc.position_override)})


    def _execute_talk(self, world, action, parameters, runtime):
        if action.actor_id != "player":
            npc = world.get_npc(action.actor_id)
            if npc is None:
                return ExecutionResult(False, "Actor NPC not found.", action)
            text = action.parameters.get("text") or action.raw_text or "Hello."
            npc.add_memory({"time": world.time, "source": "npc_action", "content": text})
            world.add_history("npc_talked", parameters["npc_id"], text, actor=npc.id)
            return ExecutionResult(True, f"{npc.name} spoke to {parameters['npc_id']}.", action)
        from action import talk
        text = action.parameters.get("text") or action.raw_text or "Hello."
        success, message = talk(world, parameters["npc_id"], text)
        return ExecutionResult(success, message, action)

    def _execute_follow(self, world, action, parameters, runtime):
        if action.raw_text:
            dialogue_ok, dialogue_message = self._record_dialogue(world, action)
            if not dialogue_ok:
                return ExecutionResult(False, dialogue_message, action)
        accepted = True
        if runtime is not None and hasattr(runtime, "_npc_accepts_follow"):
            accepted = runtime._npc_accepts_follow(parameters["npc_id"])
        npc = world.get_npc(parameters["npc_id"])
        if npc is not None:
            npc.add_memory({
                "time": world.time,
                "type": "interaction",
                "content": f"Player asked {npc.name} to follow",
                "result": "accepted" if accepted else "declined",
            })
        if not accepted:
            return ExecutionResult(True, "抱歉，我现在不能离开这里。", action, data={"accepted": False, "reply": "抱歉，我现在不能离开这里。"})
        result = self._set_follow(world, action, parameters["npc_id"], "player", runtime)
        result.data["accepted"] = True
        result.data["reply"] = "可以，我跟着你。"
        return result

    def _execute_stop_follow(self, world, action, parameters, runtime):
        if action.raw_text:
            dialogue_ok, dialogue_message = self._record_dialogue(world, action)
            if not dialogue_ok:
                return ExecutionResult(False, dialogue_message, action)
        npc = world.get_npc(parameters["npc_id"])
        if npc is not None:
            npc.add_memory({
                "time": world.time,
                "type": "interaction",
                "content": "Player asked me to stop following",
                "result": "stopped",
            })
        result = self._set_follow(world, action, parameters["npc_id"], None, runtime)
        result.data["accepted"] = True
        result.data["reply"] = "好，我不跟着你了。"
        return result

    def _record_dialogue(self, world, action):
        from action import talk
        npc_id = action.target_id or action.parameters.get("npc_id")
        text = action.raw_text or action.parameters.get("text", "")
        if npc_id is None or not text:
            return False, "Dialogue action requires npc_id and text."
        return talk(world, npc_id, text)

    def _set_follow(self, world, action, npc_id, follow_target, runtime=None):
        # Step 12.2: the follow Goal is decided by the Director and written by
        # GoalManager. This helper only supplies the player-action information.
        if runtime is not None and hasattr(runtime, "_set_follow_state"):
            runtime._set_follow_state(npc_id, follow_target is not None)
        else:
            route_director_input(world, DirectorInput(
                source=SOURCE_PLAYER_ACTION,
                actor_id="player",
                target_id=npc_id,
                payload={
                    "action": "follow" if follow_target else "stop_follow",
                    "npc_id": npc_id,
                },
                context={"time": world.time},
                time=world.time,
            ))
        message = f"{npc_id} now follows player." if follow_target else f"{npc_id} stopped following."
        return ExecutionResult(True, message, action, data={"follow_target": follow_target})

    def _execute_conversation_only(self, world, action, parameters, runtime):
        dialogue_ok, dialogue_message = self._record_dialogue(world, action)
        if not dialogue_ok:
            return ExecutionResult(False, dialogue_message, action)
        if runtime is None or not hasattr(runtime, "dialogue_system"):
            return ExecutionResult(False, "Dialogue runtime is unavailable.", action)
        reply = runtime.dialogue_system.generate_reply(world, parameters.get("npc_id"), action.raw_text)
        return ExecutionResult(True, reply, action, data={"reply": reply})

    def _execute_inspect(self, world, action, parameters, runtime):
        obj = WorldObjectRegistry().get(parameters["object_id"])
        return ExecutionResult(True, f"You inspect {parameters['object_id']}: {obj.get('description', '')}", action, data={"object": obj})

    def _discover_with_director(self, world, npc, entry, index, runtime):
        """Discovery is information; the Director decides any follow-up goal."""
        summary = discover(world, npc, entry, index)
        suggestion = summary.get("follow_up_goal")
        if summary.get("already_known") or not suggestion:
            return summary
        director_input = DirectorInput(
            source=SOURCE_NPC_DISCOVERY,
            actor_id=npc.id,
            target_id=npc.id,
            text=str(entry.get("description", "")),
            payload={
                "npc_id": npc.id,
                "evidence_id": summary.get("evidence_id"),
                "evidence_type": entry.get("type"),
                "description": entry.get("description", ""),
                "follow_up_goal": suggestion,
                "linked_belief": entry.get("linked_belief"),
            },
            context={
                "time": world.time,
                "location": world.npc_position(npc.id),
            },
            time=world.time,
        )
        if runtime is not None and hasattr(runtime, "decide_and_apply_goals"):
            outcome = runtime.decide_and_apply_goals(director_input)
            results = list(outcome.get("results") or [])
        else:
            results = route_director_input(world, director_input)
        applied = [item for item in results if item.get("applied")]
        if applied:
            summary["goal"] = applied[0].get("goal_string")
            summary["goal_added"] = True
        return summary

    def _execute_search(self, world, action, parameters, runtime):
        if parameters["object_id"] == "__location__":
            objects = WorldObjectRegistry().visible_objects(world)
        else:
            objects = [WorldObjectRegistry().get(parameters["object_id"])]
        objects = [obj for obj in objects if obj]
        data = {"objects": objects}
        message = f"Found {len(objects)} object(s)."
        # An NPC searching its surroundings can also uncover local evidence.
        # The player path is unchanged: only NPC actors run this closure.
        if action.actor_id != "player":
            npc = world.get_npc(action.actor_id)
            if npc is not None:
                reach = float(npc.vision_range) + DISCOVERY_REACH_BONUS
                position = world.npc_position(npc.id)
                findings = [
                    self._discover_with_director(world, npc, entry, index, runtime)
                    for index, entry in find_in_range(world, position, reach, None)
                ]
                if findings:
                    data["evidence_discovered"] = findings
                    message = (
                        f"Found {len(objects)} object(s) and "
                        f"{len(findings)} evidence trace(s)."
                    )
        return ExecutionResult(True, message, action, data=data)

    def _execute_take(self, world, action, parameters, runtime):
        item_id = parameters["item_id"]
        if action.actor_id == "player":
            success, message = world.pick(item_id)
            return ExecutionResult(success, message, action, data={"item_id": item_id})
        item = world.get_item(item_id)
        npc = world.get_npc(action.actor_id)
        if item is None or npc is None:
            return ExecutionResult(False, "Item or NPC not found.", action)
        item.holder = npc.id
        item.x, item.y = world.npc_position(npc.id)
        return ExecutionResult(True, f"{npc.name} took {item_id}.", action, data={"item_id": item_id, "holder": npc.id})

    def _execute_open(self, world, action, parameters, runtime):
        if runtime and hasattr(runtime, "open_drawer"):
            result = runtime.open_drawer(parameters["object_id"])
            if isinstance(result, dict):
                return ExecutionResult(result.get("success", False), result.get("message", ""), action, data={"legacy_result": result})
            return ExecutionResult(False, "Legacy open action returned an invalid result.", action)
        return ExecutionResult(False, "Open runtime is unavailable.", action)

    def _execute_ask(self, world, action, parameters, runtime):
        dialogue_ok, dialogue_message = self._record_dialogue(world, action)
        if not dialogue_ok:
            return ExecutionResult(False, dialogue_message, action)
        if runtime is None or not hasattr(runtime, "dialogue_system"):
            return ExecutionResult(False, "Dialogue runtime is unavailable.", action)
        reply = runtime.dialogue_system.generate_reply(world, parameters["npc_id"], action.raw_text)
        return ExecutionResult(True, reply, action, data={"reply": reply})

    def _execute_investigate(self, world, action, parameters, runtime):
        evidence_id = parameters.get("evidence_id")
        if evidence_id and action.actor_id != "player":
            npc = world.get_npc(action.actor_id)
            found = by_reference(world, evidence_id)
            if npc is not None and found is not None:
                index, entry = found
                summary = self._discover_with_director(world, npc, entry, index, runtime)
                message = (
                    f"{npc.name} investigated {entry.get('type')}: "
                    f"{entry.get('description', '')}".strip()
                )
                return ExecutionResult(True, message, action, data=summary)
        evidence = [dict(entry) for entry in world.evidence if entry.get("type") == parameters.get("target_id")]
        return ExecutionResult(
            True,
            f"Investigation target: {parameters.get('target_id')}.",
            action,
            data={"target_id": parameters.get("target_id"), "evidence": evidence},
        )

    def _execute_give(self, world, action, parameters, runtime):
        from action import give_item
        success, message = give_item(world, parameters["item_id"], parameters["target"])
        return ExecutionResult(success, message, action)

    def _execute_drop(self, world, action, parameters, runtime):
        success, message = world.drop(parameters["item_id"], parameters["x"], parameters["y"])
        return ExecutionResult(success, message, action)

    def _execute_wait(self, world, action, parameters, runtime):
        success, message = world.wait(parameters["seconds"])
        return ExecutionResult(success, message, action)

    def _execute_attack(self, world, action, parameters, runtime):
        from action import attack
        success, message = attack(world, parameters["npc_id"])
        return ExecutionResult(success, message, action)

    def _execute_rescue(self, world, action, parameters, runtime):
        from action import rescue
        success, message = rescue(world, parameters["npc_id"])
        return ExecutionResult(success, message, action)

    def _execute_hide(self, world, action, parameters, runtime):
        from action import hide_item
        success, message = hide_item(world, parameters["item_id"], action.parameters.get("location", [world.player.x, world.player.y]))
        return ExecutionResult(success, message, action)

    def _execute_create_rumor(self, world, action, parameters, runtime):
        from action import create_rumor
        success, message = create_rumor(world, parameters["npc_id"], action.parameters.get("content", action.raw_text))
        return ExecutionResult(success, message, action)

    def _execute_buy(self, world, action, parameters, runtime):
        success, message = world.buy(parameters["item_id"])
        return ExecutionResult(success, message, action)

    def _execute_work(self, world, action, parameters, runtime):
        success, message = world.work()
        return ExecutionResult(success, message, action)

    def _execute_explore(self, world, action, parameters, runtime):
        success, message = world.explore()
        return ExecutionResult(success, message, action)



