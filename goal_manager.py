"""Goal Manager (Step 12.1).

The AI Director decides *what* goal changes to make. GoalManager is the only
component allowed to turn a ``GoalChange`` into WorldState, and it does so by
emitting the existing Event DSL effects. It never decides goals, never plans,
never executes NPC behaviour and never runs the goal lifecycle.
"""

from director_input import (
    GOAL_CHANGE_ADD,
    GOAL_CHANGE_NONE,
    GOAL_CHANGE_REMOVE,
    URGENCY_RANK,
    GoalChange,
    normalize_urgency,
    split_goal_string,
)
from event import execute_event


class GoalManager:
    """Translate Director GoalChange objects into the Event DSL."""

    def apply(self, world, change, record_history=True):
        if change is None:
            return self._result(False, GOAL_CHANGE_NONE, reason="no_change")
        if isinstance(change, dict):
            change = GoalChange.from_dict(change)

        change_type = str(getattr(change, "change_type", GOAL_CHANGE_NONE) or GOAL_CHANGE_NONE)
        if change_type == GOAL_CHANGE_NONE:
            return self._result(False, change_type, reason="no_change")
        if not change.npc_id:
            return self._result(False, change_type, reason="missing_npc_id")
        if world.get_npc(change.npc_id) is None:
            return self._result(False, change_type, npc_id=change.npc_id, reason="unknown_npc")

        if change_type == GOAL_CHANGE_ADD:
            return self._apply_add(world, change, record_history=record_history)
        if change_type == GOAL_CHANGE_REMOVE:
            return self._apply_remove(world, change, record_history=record_history)
        return self._result(
            False, change_type, npc_id=change.npc_id,
            reason=f"unsupported_change_type:{change_type}",
        )

    def apply_all(self, world, changes):
        return [self.apply(world, change) for change in (changes or [])]

    # -- internals ---------------------------------------------------------
    def _apply_add(self, world, change, record_history=True):
        goal_string = change.resolved_goal_string()
        if not goal_string:
            return self._result(False, GOAL_CHANGE_ADD, npc_id=change.npc_id, reason="empty_goal")
        npc = world.get_npc(change.npc_id)
        states = self._states(npc)
        urgency = normalize_urgency(getattr(change, "urgency", "normal"))

        # Never create a duplicate of a goal the NPC already has. A goal that
        # already exists (even terminal) is not revived here — this matches the
        # original npc.add_goal() no-op and avoids a StoryEvent re-adding a
        # failed/completed goal every time it triggers.
        if goal_string in (npc.goals or []):
            return self._result(
                False, GOAL_CHANGE_ADD, npc_id=change.npc_id,
                goal_string=goal_string, reason="already_present",
            )

        active = self._current_active_goal(npc)
        preempt = active is not None and self._preempts(active["urgency"], urgency)
        status = "active" if (active is None or preempt) else "pending"

        # Suspend the current plan before activating the interrupting goal.
        if preempt:
            self.suspend_goal(
                world, npc, active["goal"],
                interrupted_by=goal_string,
                reason=change.reason or f"{urgency} goal interrupted the current plan",
            )

        self._write_add(world, change, goal_string, record_history=record_history)

        entry = states.setdefault(
            goal_string, {"status": "active", "attempts": 0, "reason": ""}
        )
        entry["status"] = status
        entry["urgency"] = urgency
        entry["reason"] = str(change.reason or entry.get("reason", ""))
        if change.source:
            entry["source"] = str(change.source)
        if change.priority is not None:
            entry["priority"] = float(change.priority)
        entry["activated_at"] = float(world.time)
        if status == "active":
            npc.active_goal = goal_string

        # An explicit follow order is the NPC current top goal, so the Planner
        # schedules it before equal-priority background goals.
        if goal_string == "follow player" and "follow player" in npc.goals:
            npc.goals.remove("follow player")
            npc.goals.insert(0, "follow player")

        return self._result(
            True, GOAL_CHANGE_ADD, npc_id=change.npc_id, goal_string=goal_string,
            reason=entry["reason"], status=status,
            preempted=active["goal"] if preempt else None,
            urgency=urgency,
        )

    def _apply_remove(self, world, change, record_history=True):
        goal_string = change.resolved_goal_string()
        if not goal_string:
            return self._result(False, GOAL_CHANGE_REMOVE, npc_id=change.npc_id, reason="empty_goal")
        npc = world.get_npc(change.npc_id)
        states = self._states(npc)
        self._write_remove(world, change, goal_string, record_history=record_history)

        entry = states.get(goal_string)
        if isinstance(entry, dict):
            entry["status"] = "cancelled"
            entry["reason"] = str(change.reason or "removed")
        npc.suspended_goal_stack = [
            record for record in (getattr(npc, "suspended_goal_stack", None) or [])
            if record.get("goal") != goal_string
        ]
        resumed = None
        if getattr(npc, "active_goal", None) == goal_string:
            npc.active_goal = None
            resumed = self.resume_next_suspended(world, npc)
        return self._result(
            True, GOAL_CHANGE_REMOVE, npc_id=change.npc_id, goal_string=goal_string,
            reason=str(change.reason or "removed"), resumed=resumed,
        )

    # -- lifecycle helpers -------------------------------------------------
    def _states(self, npc):
        if not isinstance(getattr(npc, "goal_states", None), dict):
            npc.goal_states = {}
        return npc.goal_states

    def _current_active_goal(self, npc):
        """The explicit active goal, if it is still executable."""
        if npc is None:
            return None
        raw = getattr(npc, "active_goal", None)
        if not raw or raw not in (getattr(npc, "goals", None) or []):
            return None
        entry = self._states(npc).get(raw, {})
        if entry.get("status") in {"completed", "failed", "suspended", "cancelled"}:
            return None
        return {"goal": raw, "urgency": normalize_urgency(entry.get("urgency"))}

    @staticmethod
    def _preempts(current_urgency, new_urgency):
        """Only a strictly higher urgency may interrupt the current goal."""
        return URGENCY_RANK.get(normalize_urgency(new_urgency), 0) > URGENCY_RANK.get(
            normalize_urgency(current_urgency), 0
        )

    def suspend_goal(self, world, npc, goal, interrupted_by="", reason=""):
        """Move the active goal to suspended and push it on the stack."""
        states = self._states(npc)
        entry = states.setdefault(goal, {"status": "active", "attempts": 0, "reason": ""})
        entry["status"] = "suspended"
        entry["interrupted_by"] = str(interrupted_by or "")
        entry["interrupt_reason"] = str(reason or "")
        entry["interrupted_at"] = float(world.time)
        record = {
            "goal": goal,
            "urgency": normalize_urgency(entry.get("urgency")),
            "source": str(entry.get("source") or ""),
            "priority": float(entry.get("priority") or 0.0),
            "suspended_at": float(world.time),
            "interrupted_by": str(interrupted_by or ""),
            "reason": str(reason or ""),
        }
        if getattr(npc, "current_action", None):
            record["action"] = dict(npc.current_action)
        if getattr(npc, "action_progress", None):
            record["progress"] = dict(npc.action_progress)
        stack = list(getattr(npc, "suspended_goal_stack", None) or [])
        stack.append(record)
        npc.suspended_goal_stack = stack
        npc.current_action = {}
        npc.add_memory({
            "time": world.time,
            "source": "goal_lifecycle",
            "content": f"Goal suspended: {goal} (interrupted by {interrupted_by}).",
            "goal": goal,
            "interrupted_by": str(interrupted_by or ""),
            "reason": str(reason or ""),
        })
        world.add_history(
            "npc_goal_suspended", npc.id,
            f"{npc.name}: {goal} suspended, interrupted by {interrupted_by}.",
            actor=npc.id,
        )
        if self._is_player_related(goal):
            # Leaving the player must never be silent.
            npc.add_memory({
                "time": world.time,
                "source": "goal_lifecycle",
                "content": (
                    f"我因为{reason or '新的紧急事情'}暂时离开玩家，去处理{interrupted_by}。"
                ),
                "interrupted_goal": goal,
                "new_goal": str(interrupted_by or ""),
                "reason": str(reason or ""),
            })
            world.add_history(
                "npc_left_player", npc.id,
                f"{npc.name} temporarily left the player to handle {interrupted_by}.",
                actor=npc.id,
            )
        return record

    def resume_next_suspended(self, world, npc):
        """Re-activate the most recent suspended goal, skipping dead ones."""
        states = self._states(npc)
        stack = list(getattr(npc, "suspended_goal_stack", None) or [])
        while stack:
            record = stack.pop()
            goal = str(record.get("goal") or "")
            if not goal or goal not in (npc.goals or []):
                continue
            entry = states.get(goal, {})
            if entry.get("status") in {"completed", "failed", "cancelled"}:
                continue
            entry["status"] = "active"
            entry["resumed_at"] = float(world.time)
            npc.active_goal = goal
            npc.suspended_goal_stack = stack
            npc.add_memory({
                "time": world.time,
                "source": "goal_lifecycle",
                "content": f"Goal resumed: {goal}.",
                "goal": goal,
            })
            world.add_history(
                "npc_goal_resumed", npc.id,
                f"{npc.name}: resumed {goal}.", actor=npc.id,
            )
            return goal
        npc.suspended_goal_stack = stack
        return None

    @staticmethod
    def _is_player_related(goal):
        goal = str(goal or "")
        return goal == "follow player" or goal.startswith("lead_player_to")

    def _write_add(self, world, change, goal_string, record_history=True):
        if change.goal_type == "follow_player" or goal_string == "follow player":
            effects = [{
                "type": "set_follow_target",
                "npc_id": change.npc_id,
                "follow_target": "player",
            }]
        else:
            effects = [{
                "type": "add_goal",
                "npc_id": change.npc_id,
                "goal": goal_string,
            }]
        event = {
            "event_id": f"goal_manager_add_{change.npc_id}_{int(world.time)}",
            "time": world.time,
            "effects": effects,
        }
        execute_event(world, event, goal_lifecycle=False, record_history=record_history)

    def _write_remove(self, world, change, goal_string, record_history=True):
        if change.goal_type == "follow_player" or goal_string == "follow player":
            effects = [{
                "type": "set_follow_target",
                "npc_id": change.npc_id,
                "follow_target": None,
            }]
        else:
            effects = [{
                "type": "remove_goal",
                "npc_id": change.npc_id,
                "goal": goal_string,
            }]
        event = {
            "event_id": f"goal_manager_remove_{change.npc_id}_{int(world.time)}",
            "time": world.time,
            "effects": effects,
        }
        execute_event(world, event, goal_lifecycle=False, record_history=record_history)

    @staticmethod
    def _result(applied, change_type, npc_id=None, goal_string=None, reason="", event_id=None, **extra):
        result = {
            "applied": bool(applied),
            "change_type": change_type,
            "npc_id": npc_id,
            "goal_string": goal_string,
            "reason": reason,
            "event_id": event_id,
        }
        result.update(extra)
        return result

def route_director_input(world, director_input, director=None):
    """Director decides, GoalManager writes — the single funnel helper.

    Used by callers that have no live engine/director instance (e.g. a bare
    ActionExecutor in unit code). With a director it uses that director; without
    one it uses the Director's deterministic policies.
    """
    if director is not None:
        changes = director.decide_goals(world, director_input) or []
    else:
        from director import decide_goals_for_input
        changes = decide_goals_for_input(world, director_input) or []
    manager = GoalManager()
    return [manager.apply(world, change) for change in changes]

def goal_manager_for(world):
    """Return the world's shared GoalManager (create one if absent)."""
    manager = getattr(world, "goal_manager", None)
    if manager is None:
        manager = GoalManager()
        try:
            world.goal_manager = manager
        except Exception:
            pass
    return manager


def apply_goal_effect(world, npc_id, goal_string, change_type=GOAL_CHANGE_ADD,
                      priority=None, urgency="normal", source="story_event", reason=""):
    """Event DSL -> GoalManager entry for StoryEvent goals (Step 12.4).

    The inner Event DSL write is performed with record_history=False because the
    caller (StoryEvent / EventExecutor) records the original event itself, so a
    goal is produced exactly once and history is not duplicated.
    """
    manager = goal_manager_for(world)
    goal_type, target = split_goal_string(goal_string)
    change = GoalChange(
        change_type=change_type,
        npc_id=npc_id,
        goal_type=goal_type,
        target=target,
        goal_string=str(goal_string),
        priority=priority,
        urgency=urgency,
        source=source,
        reason=reason,
    )
    return manager.apply(world, change, record_history=False)


def apply_follow_effect(world, npc_id, follow_target, urgency="normal",
                        source="story_event", reason=""):
    """StoryEvent set_follow_target -> GoalManager follow-goal lifecycle."""
    manager = goal_manager_for(world)
    following = follow_target is not None
    change = GoalChange(
        change_type=GOAL_CHANGE_ADD if following else GOAL_CHANGE_REMOVE,
        npc_id=npc_id,
        goal_type="follow_player",
        target="player",
        goal_string="follow player",
        urgency=urgency,
        source=source,
        reason=reason,
    )
    return manager.apply(world, change, record_history=False)
