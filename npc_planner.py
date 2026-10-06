"""Rule-based NPC Planner for V3 Step 3.

The planner reads NPC-local information (own goals, own memory, own beliefs, and
what the NPC can currently observe) and returns one Action. It never mutates
WorldState, executes an Action, or calls an LLM.
"""

import re
from dataclasses import dataclass, field

from action_layer import Action
from schedule import location_at_time, location_coordinates
from world import EXPLORE_LOCATIONS, LOCATION_ALIASES


# A goal queued by the AI Director gets a short-term scheduling edge so the
# story beat is actually acted on, without becoming a permanent top priority.
# The boost is derived from goal_states[goal]["source"], so it is set once when
# the AI event lands and never grows from tick to tick.
AI_GOAL_PRIORITY = 0.8
AI_GOAL_SOURCE = "ai_director"

# Goal families this planner knows how to turn into an Action. A goal outside
# this set is treated as background, which lets the NPC fall back to schedule.
ACTIONABLE_GOAL_TYPES = {
    "reach_location",
    "lead_player_to",
    "find_item",
    "investigate",
    "talk_to",
    "help_npc",
    "find_npc",
    "follow_npc",
    "escape",
    "work",
    "wait",
}


GOAL_ALIASES = {
    "find ledger": ("find_item", "ledger"),
    "recover ledger": ("find_item", "item_ledger"),
    "find item_1": ("find_item", "item_1"),
    "follow player": ("follow_npc", "player"),
    "help Ren": ("help_npc", "npc_2"),
    "help_npc": ("help_npc", None),
    "find Ren": ("find_npc", "npc_2"),
    "search Ren": ("investigate", "npc_2"),
    "investigate mayor": ("investigate", "npc_1"),
    "stop Ren": ("investigate", "npc_2"),
    "frame Ren": ("investigate", "npc_2"),
    "demand answers": ("investigate", "npc_1"),
    "confront mayor": ("talk_to", "npc_1"),
    "protect_office": ("reach_location", "town"),
    "observe village": ("reach_location", "town"),
    "watch_items": ("wait", None),
    "talk_to_mira": ("talk_to", "npc_1"),
    "rest": ("wait", None),
}


@dataclass
class Goal:
    goal_type: str
    target_id: str | None = None
    priority: float = 0.5
    parameters: dict = field(default_factory=dict)
    raw: str = ""

    def to_dict(self):
        return {
            "goal_type": self.goal_type,
            "target_id": self.target_id,
            "priority": self.priority,
            "parameters": dict(self.parameters),
            "raw": self.raw,
        }


class NPCPlanner:
    """Convert NPC goals into one safe Action at a time."""

    def __init__(self):
        # Raw goals the last plan_next_action() call could not turn into Action.
        self.last_unplanned_goals = []

    def parse_goal(self, goal):
        if isinstance(goal, Goal):
            if not goal.raw:
                goal.raw = goal.goal_type
            return goal
        if isinstance(goal, dict):
            parsed = Goal(
                goal_type=str(goal.get("goal_type", goal.get("type", "unknown"))),
                target_id=goal.get("target_id", goal.get("target")),
                priority=float(goal.get("priority", 0.5)),
                parameters=dict(goal.get("parameters", {})),
            )
            parsed.raw = str(goal.get("raw") or goal.get("goal") or parsed.goal_type)
            return parsed
        text = str(goal)
        parsed = self._parse_goal_text(text)
        parsed.raw = text
        return parsed

    def _parse_goal_text(self, text):
        if text in GOAL_ALIASES:
            goal_type, target_id = GOAL_ALIASES[text]
            return Goal(goal_type, target_id)
        lowered = text.lower()
        if ":" in text:
            prefix, _, raw_target = text.partition(":")
            prefix = prefix.strip()
            raw_target = raw_target.strip()
            if prefix == "go_to":
                # npc_action_decision encodes "go somewhere" as go_to:<place>;
                # the Planner executes it with the reach_location behaviour.
                prefix = "reach_location"
            if prefix in ACTIONABLE_GOAL_TYPES and raw_target:
                # A requested "lead_player_to" outranks background goals so the
                # NPC actually starts leading instead of resuming its old plan.
                priority = 0.9 if prefix == "lead_player_to" else 0.5
                return Goal(prefix, raw_target, priority=priority)
        bare = lowered.strip()
        if bare in ACTIONABLE_GOAL_TYPES:
            # Bare goals such as "wait" / "work" / "escape" carry no target.
            return Goal(bare)
        for phrase, (goal_type, target_id) in GOAL_ALIASES.items():
            # Match whole words only: substring matching made "rest" match
            # inside "forest", which silently turned reach-forest into wait.
            pattern = r"(?<![a-z0-9_])" + re.escape(phrase.lower()) + r"(?![a-z0-9_])"
            if re.search(pattern, lowered):
                return Goal(goal_type, target_id)
        if lowered.startswith("reach "):
            return Goal("reach_location", lowered[6:].strip())
        if lowered.startswith("find "):
            return Goal("find_item", lowered[5:].strip())
        return Goal("unknown", parameters={"raw_goal": text})

    def _apply_source_priority(self, npc, goal):
        """Give an AI-Director goal its short-term scheduling edge.

        Only raises an already-lower priority up to AI_GOAL_PRIORITY; a goal the
        parser already ranks higher (e.g. lead_player_to at 0.9) is untouched.
        """
        if goal.priority >= AI_GOAL_PRIORITY:
            return goal
        states = getattr(npc, "goal_states", None)
        entry = states.get(goal.raw) if isinstance(states, dict) else None
        if isinstance(entry, dict) and entry.get("source") == AI_GOAL_SOURCE:
            goal.priority = AI_GOAL_PRIORITY
        return goal

    def can_plan_goal(self, goal):
        """True when parse_goal maps this goal onto a Planner-executable type."""
        try:
            parsed = self.parse_goal(goal)
        except (TypeError, ValueError):
            return False
        return parsed.goal_type in ACTIONABLE_GOAL_TYPES

    def active_goal_of(self, npc):
        """The explicit current goal, if it can still be executed.

        This is a read-only lookup. The Planner never decides to suspend,
        resume or preempt a goal — GoalManager/lifecycle does that.
        """
        raw = getattr(npc, "active_goal", None)
        if not raw or raw not in (getattr(npc, "goals", None) or []):
            return None
        entry = (getattr(npc, "goal_states", None) or {}).get(raw, {})
        if entry.get("status") in {"completed", "failed", "suspended", "cancelled"}:
            return None
        return raw

    def plan_next_action(self, world, npc_id):
        """Return one Action from local state, or None when no action is needed."""
        npc = world.get_npc(npc_id)
        if npc is None:
            return None

        states = npc.goal_states if isinstance(getattr(npc, "goal_states", None), dict) else {}
        goals = [self._apply_source_priority(npc, self.parse_goal(goal)) for goal in npc.goals]
        # Raw goals the Planner looked at but could not turn into an Action this
        # tick. The engine uses this to run the goal lifecycle for AI goals that
        # stay blocked, so a blocked goal cannot outrank work forever.
        self.last_unplanned_goals = []

        active_raw = self.active_goal_of(npc)
        active_goal = None
        if active_raw is not None:
            active_goal = next((goal for goal in goals if goal.raw == active_raw), None)
            if active_goal is None:
                active_goal = self._apply_source_priority(npc, self.parse_goal(active_raw))

        # Continue an in-progress movement, unless the goal is to follow a
        # moving target (follow re-plans against the latest target position) or
        # the explicit active goal changed to a different goal.
        current = npc.current_action or {}
        current_intent = current.get("source_intent") or {}
        current_goal_type = current_intent.get("goal_type")
        if (
            current.get("action_type") == "move"
            and not current.get("complete")
            and current_goal_type != "follow_npc"
        ):
            current_raw = str(current_intent.get("raw") or "")
            if active_raw is not None:
                # Plan continuity: never let a queued goal steal the tick from
                # the explicit active goal.
                if current_raw == active_raw:
                    params = dict(current.get("parameters", {}))
                    return Action(
                        "move",
                        actor_id=npc_id,
                        parameters=params,
                        source_intent=current_intent,
                        reason="continue current movement",
                    )
            else:
                current_priority = 0.0
                for goal in goals:
                    if goal.raw == current_raw:
                        current_priority = goal.priority
                        break
                pending = {
                    goal.raw for goal in goals
                    if goal.goal_type in ACTIONABLE_GOAL_TYPES
                    and states.get(goal.raw, {}).get("status") not in {"completed", "failed", "suspended", "cancelled"}
                }
                outranks = any(
                    goal.priority > current_priority and goal.raw in pending
                    for goal in goals if goal.raw != current_raw
                )
                if not outranks:
                    params = dict(current.get("parameters", {}))
                    return Action(
                        "move",
                        actor_id=npc_id,
                        parameters=params,
                        source_intent=current_intent,
                        reason="continue current movement",
                    )

        observation = self._observe(world, npc)

        # The explicit active goal owns the tick. Queued goals wait for it to
        # finish, fail or be interrupted by the lifecycle layer.
        if active_goal is not None:
            if active_goal.goal_type in ACTIONABLE_GOAL_TYPES:
                action = self._plan_goal(world, npc, active_goal, observation)
                if action is not None:
                    return action
                self.last_unplanned_goals.append(active_goal.raw)
            return None

        # No explicit active goal (e.g. initial world goals or plain fixtures):
        # keep the original priority-based selection.
        goals.sort(key=lambda goal: goal.priority, reverse=True)
        has_actionable_goal = False
        for goal in goals:
            if goal.goal_type not in ACTIONABLE_GOAL_TYPES:
                continue
            if states.get(goal.raw, {}).get("status") in {"completed", "failed", "suspended", "cancelled"}:
                continue
            has_actionable_goal = True
            action = self._plan_goal(world, npc, goal, observation)
            if action is not None:
                return action
            self.last_unplanned_goals.append(goal.raw)

        # An explicit goal that is complete or currently blocked means the NPC is
        # not free to fall back to its schedule. It waits for a re-plan instead.
        if has_actionable_goal:
            return None

        return self._schedule_action(world, npc)

    def on_action_result(self, world, npc_id, action, result):
        """Return feedback metadata without mutating WorldState."""
        return {
            "npc_id": npc_id,
            "action_type": action.action_type,
            "success": bool(result.success),
            "message": result.message,
        }

    MAX_GOAL_ATTEMPTS = 3

    def evaluate_goals(self, world, npc_id):
        """Read-only lifecycle check: which goals are completed or failed."""
        npc = world.get_npc(npc_id)
        if npc is None:
            return []
        outcomes = []
        for raw in list(npc.goals):
            goal = self.parse_goal(raw)
            status, reason = self._goal_outcome(world, npc, goal)
            if status:
                outcomes.append({"goal": goal.raw or str(raw), "status": status, "reason": reason})
        return outcomes

    def _goal_outcome(self, world, npc, goal):
        if goal.goal_type not in ACTIONABLE_GOAL_TYPES:
            # The Planner has no behaviour for this goal, so it can never be
            # satisfied. Fail it instead of leaving it active forever.
            return ("failed", f"unsupported goal: {goal.raw or goal.goal_type}")
        position = world.npc_position(npc.id)
        if goal.goal_type in {"reach_location", "escape", "work", "lead_player_to"}:
            location = goal.target_id
            if not location and goal.goal_type == "work":
                location = goal.parameters.get("location", "town")
            if not location and goal.goal_type == "escape":
                location = goal.parameters.get("safe_location", "home")
            target = self._location_coordinates(location, goal.parameters)
            if target is None:
                return ("failed", f"unknown location: {goal.target_id}")
            if world.distance_between(position, target) <= 1.5:
                return ("completed", "arrived")
            return (None, "")
        if goal.goal_type == "find_item":
            if self._held_item(world, npc, goal.target_id) is not None:
                return ("completed", "item obtained")
            if not self._target_item_exists(world, goal.target_id):
                return ("failed", f"item does not exist: {goal.target_id}")
            return (None, "")
        if goal.goal_type in {"talk_to", "help_npc", "find_npc"}:
            if goal.target_id and world.get_npc(goal.target_id) is None:
                return ("failed", f"npc does not exist: {goal.target_id}")
            return (None, "")
        if goal.goal_type == "follow_npc":
            if goal.target_id and goal.target_id != "player" and world.get_npc(goal.target_id) is None:
                return ("failed", f"npc does not exist: {goal.target_id}")
            return (None, "")
        return (None, "")

    def _target_item_exists(self, world, target_id):
        needle = str(target_id or "").lower()
        if not needle:
            return False
        for item in world.items:
            if needle in {str(item.id).lower(), str(item.type).lower()}:
                return True
        return False

    def _plan_goal(self, world, npc, goal, observation):
        position = world.npc_position(npc.id)

        if goal.goal_type == "lead_player_to":
            target = self._location_coordinates(goal.target_id, goal.parameters)
            if target is None:
                return None
            if world.distance_between(position, target) <= 1.5:
                return None
            return self._move_action(npc, target, "lead_player_to", goal)

        if goal.goal_type == "reach_location":
            target = self._location_coordinates(goal.target_id, goal.parameters)
            if target is None:
                return None
            if world.distance_between(position, target) <= 1.5:
                return None
            return self._move_action(npc, target, "reach_location", goal)

        if goal.goal_type == "find_item":
            if self._held_item(world, npc, goal.target_id) is not None:
                return None
            item = self._find_observed_item(world, npc, goal.target_id, observation)
            if item is not None:
                if world.distance_between(position, item) <= 5.0:
                    return Action(
                        "take",
                        actor_id=npc.id,
                        target_id=item.id,
                        source_intent=goal.to_dict(),
                        reason="item observed nearby",
                    )
                return self._move_action(npc, (item.x, item.y), "find_item", goal)
            if self._believes_item_missing(npc, goal.target_id):
                # A belief that the item is gone overrides a stale known
                # location: the NPC searches instead of trusting memory.
                return Action(
                    "search",
                    actor_id=npc.id,
                    target_id="__location__",
                    source_intent=goal.to_dict(),
                    reason="believes item is missing",
                )
            location = self._known_location(npc, goal.target_id)
            target = EXPLORE_LOCATIONS.get(location) if location else None
            if target is not None and world.distance_between(position, target) > 1.5:
                return self._move_action(npc, target, "find_item", goal)
            return Action(
                "search",
                actor_id=npc.id,
                target_id="__location__",
                source_intent=goal.to_dict(),
                reason="item location unknown",
            )

        if goal.goal_type == "investigate":
            target_id = goal.target_id
            observed_ids = {entry["id"] for entry in observation["npcs"]}
            target_npc = world.get_npc(target_id) if target_id else None
            if target_npc is not None and target_npc.id in observed_ids:
                if world.distance_between(position, world.npc_position(target_npc.id)) <= 5.0:
                    return Action(
                        "investigate",
                        actor_id=npc.id,
                        target_id=target_npc.id,
                        source_intent=goal.to_dict(),
                        reason="target visible",
                    )
                return self._move_action(npc, world.npc_position(target_npc.id), "investigate", goal)
            evidence = self._first_visible_evidence(npc, observation)
            if evidence is not None:
                # Discovery must run through the Action Layer (ActionExecutor
                # commits the memory/belief/goal shift), not in the planner.
                reference = evidence.get("id") or evidence.get("type")
                return Action(
                    "investigate",
                    actor_id=npc.id,
                    target_id=reference,
                    source_intent=goal.to_dict(),
                    reason="evidence visible",
                )
            return Action(
                "search",
                actor_id=npc.id,
                target_id="__location__",
                source_intent=goal.to_dict(),
                reason="investigation target unknown",
            )

        if goal.goal_type in {"talk_to", "help_npc"}:
            target = world.get_npc(goal.target_id)
            if target is None:
                return None
            observed_ids = {entry["id"] for entry in observation["npcs"]}
            if target.id not in observed_ids:
                # The NPC cannot act on someone it cannot perceive.
                return None
            if world.distance_between(position, world.npc_position(target.id)) <= 5.0:
                return Action(
                    "talk",
                    actor_id=npc.id,
                    target_id=target.id,
                    parameters={"text": f"I need to talk to {target.name}."},
                    source_intent=goal.to_dict(),
                    reason=goal.goal_type,
                )
            return self._move_action(npc, world.npc_position(target.id), goal.goal_type, goal)

        if goal.goal_type == "follow_npc":
            if goal.target_id in {"player", None}:
                target_position = (world.player.x, world.player.y)
            else:
                target = world.get_npc(goal.target_id)
                if target is None:
                    return None
                target_position = world.npc_position(target.id)
            if world.distance_between(position, target_position) <= 1.5:
                return None
            return self._move_action(npc, target_position, "follow_npc", goal)

        if goal.goal_type == "find_npc":
            target = world.get_npc(goal.target_id)
            if target is None:
                return None
            observed_ids = {entry["id"] for entry in observation["npcs"]}
            if target.id not in observed_ids:
                return None
            if world.distance_between(position, world.npc_position(target.id)) <= 5.0:
                return Action(
                    "talk",
                    actor_id=npc.id,
                    target_id=target.id,
                    parameters={"text": f"{target.name}?"},
                    source_intent=goal.to_dict(),
                    reason="find_npc",
                )
            return self._move_action(npc, world.npc_position(target.id), "find_npc", goal)

        if goal.goal_type == "escape":
            target = self._location_coordinates(goal.parameters.get("safe_location", "home"), goal.parameters)
            if target is None:
                return None
            if world.distance_between(position, target) <= 1.5:
                return None
            return self._move_action(npc, target, "escape", goal)

        if goal.goal_type == "work":
            target = self._location_coordinates(goal.parameters.get("location", "town"), goal.parameters)
            if target is None:
                return None
            if world.distance_between(position, target) <= 1.5:
                return None
            return self._move_action(npc, target, "work", goal)

        if goal.goal_type == "wait":
            return None

        return None

    def _schedule_action(self, world, npc):
        location = location_at_time(npc.schedule, world.time)
        target = location_coordinates(location)
        if world.distance_between(world.npc_position(npc.id), target) <= 1.5:
            return None
        return self._move_action(npc, target, "schedule", Goal("reach_location", location))

    def _move_action(self, npc, target, goal_type, goal):
        return Action(
            "move",
            actor_id=npc.id,
            parameters={"x": float(target[0]), "y": float(target[1])},
            source_intent=goal.to_dict() if isinstance(goal, Goal) else {"goal_type": goal_type},
            reason=f"{goal_type} movement",
        )

    def _observe(self, world, npc):
        position = world.npc_position(npc.id)
        radius = float(npc.vision_range)
        items = [
            {"id": item.id, "type": item.type, "position": [item.x, item.y]}
            for item in world.items
            if item.holder is None and world.distance_between(position, item) <= radius
        ]
        npcs = [
            {"id": other.id, "name": other.name, "position": list(world.npc_position(other.id))}
            for other in world.npcs
            if other.id != npc.id
            and world.distance_between(position, world.npc_position(other.id)) <= radius
        ]
        evidence = [
            dict(entry)
            for entry in world.evidence
            if world.distance_between(position, entry.get("location", position)) <= radius
        ]
        return {"items": items, "npcs": npcs, "evidence": evidence}

    def _believes_item_missing(self, npc, target_id):
        needle = str(target_id or "").lower()
        missing_words = ("missing", "gone", "taken", "stolen", "lost")
        for key, value in npc.beliefs.items():
            text = str(key).lower()
            if needle and needle not in text:
                continue
            if any(word in text for word in missing_words) and float(value) >= 0.5:
                return True
        return False

    def _held_item(self, world, npc, target_id):
        target = str(target_id or "").lower()
        for item in world.items:
            if item.holder != npc.id:
                continue
            if target in {str(item.id).lower(), str(item.type).lower()}:
                return item
        return None

    def _find_observed_item(self, world, npc, target_id, observation):
        target = str(target_id or "").lower()
        for entry in observation["items"]:
            keys = {str(entry["id"]).lower(), str(entry["type"]).lower()}
            if target in keys:
                return world.get_item(entry["id"])
        return None

    def _first_visible_evidence(self, npc, observation):
        return observation["evidence"][0] if observation["evidence"] else None

    def _known_location(self, npc, target_id):
        needle = str(target_id or "").lower()
        for record in reversed(npc.memory):
            text = str(record.get("content", record.get("observed", ""))).lower()
            for location in EXPLORE_LOCATIONS:
                if location.lower() in text and (needle in text or needle in {"ledger", "item"}):
                    return location
        return None

    def _location_coordinates(self, location, parameters=None):
        parameters = parameters or {}
        if "x" in parameters and "y" in parameters:
            return (float(parameters["x"]), float(parameters["y"]))
        if location:
            location = LOCATION_ALIASES.get(str(location).lower(), location)
        if location in EXPLORE_LOCATIONS:
            return EXPLORE_LOCATIONS[location]
        return None

    def resolve_location(self, location):
        """Public read-only resolver shared with movement-intent inspection."""
        return self._location_coordinates(location)





