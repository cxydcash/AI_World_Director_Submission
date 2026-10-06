"""Data-driven Story Timeline engine for V3 Step 4.

StoryEvent -> Trigger / Condition -> StoryTimeline -> Event DSL -> WorldState

Timeline nodes are pure data. The engine never branches on a specific
event_id: it only evaluates world conditions and forwards an event's
effects to the shared Event DSL (EventValidator + EventExecutor), exactly
like the Action Layer already does for player/NPC actions.
"""

import math
from dataclasses import dataclass, field

import npc_movement_intent
from event import EventExecutor, EventValidationError, EventValidator
import plot_deviation


STORY_STATUSES = frozenset(
    {"pending", "deferred", "triggered", "completed", "cancelled", "blocked", "failed", "modified"}
)

# A successfully executed event. "triggered" is the historical terminal
# status this codebase already persisted, so it stays the default. "completed"
# is accepted everywhere as an equivalent terminal state.
EXECUTED_STATUSES = frozenset({"triggered", "completed"})

CONDITION_TYPES = frozenset(
    {
        "character_state",
        "npc_goal_exists",
        "npc_at_location",
        "item_exists",
        "player_has_item",
        "belief_true",
        "memory_contains",
        "event_completed",
        "event_cancelled",
        "time_reached",
        "escorting_player",
    }
)

DEFAULT_PRIORITY = 0.0


@dataclass
class TriggerResult:
    """Outcome of trying to trigger one StoryEvent."""

    event_id: str
    status: str
    triggered: bool = False
    executed: bool = False
    reason: str = ""
    error: str | None = None
    phase: str = ""
    deferred_moves: list = field(default_factory=list)


@dataclass
class StoryCondition:
    """A single predicate over WorldState / Timeline state."""

    condition_type: str
    parameters: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, StoryCondition):
            return data
        if not isinstance(data, dict):
            raise ValueError("Story condition must be an object.")
        payload = dict(data)
        condition_type = payload.pop("type", None)
        if condition_type not in CONDITION_TYPES:
            raise ValueError(f"Unknown story condition type: {condition_type}.")
        return cls(condition_type, payload)

    def to_dict(self):
        payload = {"type": self.condition_type}
        payload.update(self.parameters)
        return payload

    def evaluate(self, world, timeline):
        return evaluate_condition(
            world, timeline, self.condition_type, self.parameters
        )


@dataclass
class StoryEvent:
    """Pure data description of one timeline node."""

    event_id: str
    title: str = ""
    description: str = ""
    status: str = "pending"
    created_at: float = 0.0
    trigger_at: float | None = None
    conditions: list = field(default_factory=list)
    cancel_conditions: list = field(default_factory=list)
    effects: list = field(default_factory=list)
    priority: float = DEFAULT_PRIORITY
    source: str = "scenario"
    metadata: dict = field(default_factory=dict)

    @property
    def name(self):
        return self.title

    @classmethod
    def from_dict(cls, data):
        normalized = normalize_story_event(data)
        return cls(
            event_id=normalized["event_id"],
            title=normalized["title"],
            description=normalized["description"],
            status=normalized["status"],
            created_at=normalized["created_at"],
            trigger_at=normalized["trigger_at"],
            conditions=normalized["conditions"],
            cancel_conditions=normalized["cancel_conditions"],
            effects=normalized["effects"],
            priority=normalized["priority"],
            source=normalized["source"],
            metadata=normalized["metadata"],
        )

    def to_dict(self):
        return normalize_story_event(self)

    def to_legacy_dict(self):
        payload = self.to_dict()
        return {
            "id": payload["event_id"],
            "trigger_time": payload["trigger_at"],
            "status": payload["status"],
            "description": payload["description"],
        }


def normalize_story_event(event):
    """Return a canonical StoryEvent dict, accepting StoryEvent or dict input.

    Legacy timeline entries only know id / trigger_time / status. They are
    upgraded in place-compatible form: the canonical keys are filled in while
    the legacy aliases are preserved so every existing reader keeps working.
    """
    if isinstance(event, StoryEvent):
        event = {
            "event_id": event.event_id,
            "title": event.title,
            "description": event.description,
            "status": event.status,
            "created_at": event.created_at,
            "trigger_at": event.trigger_at,
            "conditions": event.conditions,
            "cancel_conditions": event.cancel_conditions,
            "effects": event.effects,
            "priority": event.priority,
            "source": event.source,
            "metadata": event.metadata,
        }
    if not isinstance(event, dict):
        raise ValueError("Story event must be an object.")

    normalized = dict(event)
    event_id = event.get("event_id", event.get("id"))
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("Story event requires a non-empty event_id.")

    trigger_at = event.get("trigger_at", event.get("trigger_time"))
    if trigger_at is not None:
        try:
            trigger_at = float(trigger_at)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid trigger_at for {event_id}: {exc}") from exc
        if not math.isfinite(trigger_at):
            raise ValueError(f"trigger_at must be finite for {event_id}.")

    status = event.get("status", "pending")
    if status not in STORY_STATUSES:
        raise ValueError(f"Unknown story status: {status}.")

    title = event.get("title", event.get("name", "")) or ""
    description = event.get("description", "") or ""
    created_at = event.get("created_at", 0.0) or 0.0
    try:
        created_at = float(created_at)
        priority = float(event.get("priority", DEFAULT_PRIORITY) or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid created_at/priority for {event_id}: {exc}") from exc

    conditions = [
        StoryCondition.from_dict(item).to_dict()
        for item in (event.get("conditions") or [])
    ]
    cancel_source = event.get("cancel_conditions", event.get("cancel_when", [])) or []
    cancel_conditions = [
        StoryCondition.from_dict(item).to_dict() for item in cancel_source
    ]
    effects = [dict(effect) for effect in (event.get("effects") or [])]

    normalized.update(
        {
            "event_id": event_id,
            "id": event_id,
            "title": str(title),
            "name": str(title),
            "description": str(description),
            "status": status,
            "created_at": created_at,
            "trigger_at": trigger_at,
            "trigger_time": trigger_at,
            "conditions": conditions,
            "cancel_conditions": cancel_conditions,
            "effects": effects,
            "priority": priority,
            "source": str(event.get("source", "scenario")),
            "metadata": dict(event.get("metadata", {})),
        }
    )
    return normalized


def evaluate_condition(world, timeline, condition_type, parameters):
    """Evaluate one generic condition against WorldState / Timeline."""
    parameters = parameters or {}

    if condition_type == "time_reached":
        try:
            return world.time >= float(parameters["time"])
        except (KeyError, TypeError, ValueError):
            return False

    if condition_type == "character_state":
        npc_id = parameters.get("npc_id")
        key = parameters.get("key")
        state = world.characters_state.get(npc_id, {})
        if key not in state:
            return False
        return state.get(key) == parameters.get("value")

    if condition_type == "npc_goal_exists":
        npc = world.get_npc(parameters.get("npc_id"))
        if npc is None:
            return False
        goal = str(parameters.get("goal", "")).lower()
        if not goal:
            return False
        return any(
            goal == str(item).lower() or goal in str(item).lower()
            for item in npc.goals
        )

    if condition_type == "npc_at_location":
        npc = world.get_npc(parameters.get("npc_id"))
        if npc is None:
            return False
        location = parameters.get("location")
        state = world.characters_state.get(npc.id, {})
        if state.get("location") == location:
            return True
        try:
            return world.npc_location(npc.id) == location
        except Exception:
            return False

    if condition_type == "item_exists":
        return world.get_item(parameters.get("item_id")) is not None

    if condition_type == "player_has_item":
        return parameters.get("item_id") in world.player.inventory

    if condition_type == "belief_true":
        npc = world.get_npc(parameters.get("npc_id"))
        if npc is None:
            return False
        key = parameters.get("key")
        if key not in npc.beliefs:
            return False
        threshold = parameters.get("value", parameters.get("threshold", 0.5))
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            threshold = 0.5
        return float(npc.beliefs[key]) >= threshold

    if condition_type == "memory_contains":
        npc = world.get_npc(parameters.get("npc_id"))
        if npc is None:
            return False
        needle = str(parameters.get("text", "")).lower()
        if not needle:
            return False
        return any(needle in str(record).lower() for record in npc.memory)

    if condition_type == "escorting_player":
        npc = world.get_npc(parameters.get("npc_id"))
        if npc is None:
            return False
        escorting = any(
            str(goal).strip() == "follow player"
            or str(goal).strip().startswith("lead_player_to:")
            for goal in npc.goals
        )
        if not escorting:
            return False
        radius = parameters.get("radius", 12.0)
        try:
            radius = float(radius)
        except (TypeError, ValueError):
            radius = 12.0
        return world.distance_between(
            world.npc_position(npc.id), (world.player.x, world.player.y)
        ) <= radius

    if condition_type == "event_completed":
        return _timeline_status(timeline, parameters.get("event_id")) in EXECUTED_STATUSES

    if condition_type == "event_cancelled":
        return _timeline_status(timeline, parameters.get("event_id")) == "cancelled"

    return False


def _event_touches_npc(event, npc_id):
    """True when a StoryEvent would read or change this NPC."""
    if not npc_id:
        return False
    for condition in (event.get("conditions") or []) + (event.get("cancel_conditions") or []):
        if isinstance(condition, dict) and condition.get("npc_id") == npc_id:
            return True
    for effect in event.get("effects") or []:
        if not isinstance(effect, dict):
            continue
        if effect.get("npc_id") == npc_id or effect.get("speaker_id") == npc_id:
            return True
    return False


def _dialogue_defer_reason(world, event):
    """Return a reason when an active dialogue session protects this event."""
    session = getattr(world, "dialogue_session", None)
    if not isinstance(session, dict) or not session.get("active"):
        return ""
    npc_id = session.get("npc_id")
    if not npc_id or not _event_touches_npc(event, npc_id):
        return ""
    return f"active dialogue session with {npc_id}"


def _timeline_status(timeline, event_id):
    if timeline is None or event_id is None:
        return None
    for event in timeline:
        if event.get("event_id", event.get("id")) == event_id:
            return event.get("status")
    return None


class StoryTimeline(list):
    """A list of StoryEvent dicts plus the data-driven trigger engine.

    Subclassing list keeps backward compatibility: existing code iterates
    world.story_timeline as a list of dicts and may mutate entry["status"].
    """

    def __init__(self, events=None):
        super().__init__()
        if events:
            for event in events:
                super().append(normalize_story_event(event))

    # -- CRUD ---------------------------------------------------------
    def add_event(self, event):
        normalized = normalize_story_event(event)
        if self.get_event(normalized["event_id"]) is not None:
            raise ValueError(f"Story event already exists: {normalized['event_id']}.")
        super().append(normalized)
        return normalized

    def remove_event(self, event_id):
        for index, event in enumerate(self):
            if event.get("event_id") == event_id:
                del self[index]
                return True
        return False

    def get_event(self, event_id):
        for event in self:
            if event.get("event_id") == event_id:
                return event
        return None

    def update_event(self, event_id, **changes):
        event = self.get_event(event_id)
        if event is None:
            raise KeyError(f"Unknown story event: {event_id}.")
        for key, value in changes.items():
            if key in {"id", "event_id"}:
                event["event_id"] = value
                event["id"] = value
            elif key in {"trigger_time", "trigger_at"}:
                event["trigger_at"] = value
                event["trigger_time"] = value
            elif key in {"name", "title"}:
                event["title"] = value
                event["name"] = value
            else:
                event[key] = value
        return event

    def cancel_event(self, event_id, reason=""):
        event = self.get_event(event_id)
        if event is None:
            return False
        if event.get("status") in EXECUTED_STATUSES:
            return False
        event["status"] = "cancelled"
        if reason:
            event.setdefault("metadata", {})["cancel_reason"] = reason
        return True

    def list_pending(self):
        return [event for event in self if event.get("status") == "pending"]

    # -- Engine -------------------------------------------------------
    def check_due_events(self, world, evaluator=None):
        """Evaluate, resolve and execute due events. Returns history entries."""
        history_entries = []
        existing_ids = {
            entry.get("event_id", entry.get("id"))
            for entry in getattr(world, "story_event_history", [])
        }

        for event in sorted(self, key=self._sort_key):
            event_id = event.get("event_id")
            status = event.get("status")
            if status not in {"pending", "deferred"}:
                continue
            if event_id in existing_ids:
                continue
            if not self._is_due(world, event):
                continue
            original_status = status or "pending"

            if self._any_satisfied(world, event.get("cancel_conditions")):
                entry = self._record_resolution(
                    world, event, original_status, "cancelled", "cancel condition met"
                )
                history_entries.append(entry)
                existing_ids.add(event_id)
                continue

            # Do not let a world-changing event remove the NPC the player is
            # currently talking to. The event stays due and is retried later.
            defer_reason = _dialogue_defer_reason(world, event)
            if defer_reason:
                event["status"] = "deferred"
                event.setdefault("metadata", {})["defer_reason"] = defer_reason
                continue

            conditions_ok, reason = self._all_satisfied(world, event.get("conditions"))
            if not conditions_ok:
                # Conditions may become true later: keep the event pending.
                # Step 12.6: record evidence only — no auto cancel/modify/retry.
                metadata = event.setdefault("metadata", {})
                blocked_reason = reason or "conditions pending"
                metadata["blocked_reason"] = blocked_reason
                metadata.setdefault("blocked_since", float(world.time))
                metadata["last_blocked_at"] = float(world.time)
                plot_deviation.record_deviation_evidence(
                    event, plot_deviation.KIND_CONDITION_BLOCKED, blocked_reason, world
                )
                continue

            decision = {
                "decision": "keep",
                "reason": "no player-driven change",
                "modified_event": None,
            }
            if evaluator is not None:
                decision = evaluator.evaluate_story_event(world, event) or decision
            choice = decision.get("decision", "keep")

            if choice == "cancel":
                entry = self._record_resolution(
                    world, event, original_status, "cancelled", decision.get("reason", "director cancelled")
                )
                history_entries.append(entry)
                existing_ids.add(event_id)
                continue

            if choice == "modify":
                modified = decision.get("modified_event")
                if not isinstance(modified, dict) or not (
                    modified.get("effects") or modified.get("event_id") or modified.get("id")
                ):
                    # No usable re-plan -> fall back to executing the original
                    # event rather than silently dropping the story beat.
                    choice = "keep"
                else:
                    event["status"] = "modified"
                    event["modified_event"] = modified
                    adapted = dict(modified)
                    adapted.setdefault("source", "ai_director")
                    adapted["status"] = "pending"
                    if not (adapted.get("event_id") or adapted.get("id")):
                        adapted["event_id"] = npc_movement_intent.adapted_event_id(event_id, self)
                    if adapted.get("trigger_at") is None and adapted.get("trigger_time") is None:
                        adapted["trigger_at"] = float(world.time) + 1.0
                    entry = self._record_resolution(
                        world, event, original_status, "modified",
                        decision.get("reason", "director modified"),
                        event_id=adapted.get("event_id") or adapted.get("id"),
                    )
                    history_entries.append(entry)
                    adapted_id = adapted.get("event_id") or adapted.get("id")
                    if self.get_event(adapted_id) is None:
                        self.add_event(adapted)
                    existing_ids.add(event_id)
                    continue

            if choice == "delay":
                delay_until = decision.get("delay_until", decision.get("trigger_at"))
                try:
                    delay_until = float(delay_until)
                except (TypeError, ValueError):
                    delay_until = float(world.time) + plot_deviation.DEFAULT_DELAY_SECONDS
                if delay_until <= float(world.time):
                    delay_until = float(world.time) + plot_deviation.DEFAULT_DELAY_SECONDS
                event["trigger_at"] = delay_until
                event["trigger_time"] = delay_until
                event["status"] = "pending"
                metadata = event.setdefault("metadata", {})
                metadata["last_deviation"] = decision.get("deviation") or decision.get("reason", "")
                history = metadata.setdefault("delay_history", [])
                history.append({
                    "at": float(world.time),
                    "until": delay_until,
                    "reason": str(decision.get("reason", "")),
                })
                if len(history) > plot_deviation.MAX_DELAY_HISTORY:
                    del history[:-plot_deviation.MAX_DELAY_HISTORY]
                entry = {
                    "event_id": event_id,
                    "original_status": original_status,
                    "final_status": "delayed",
                    "timestamp": world.time,
                    "reason": decision.get("reason", "director delayed"),
                    "id": event_id,
                    "original": original_status,
                    "final": "delayed",
                }
                history_entries.append(entry)
                # Deliberately NOT added to existing_ids: the event stays
                # pending and may run later at its new trigger_at.
                continue

            result = self._execute_effects(world, event)
            if result.executed:
                event["status"] = "triggered"
                event.setdefault("metadata", {})["execution_status"] = "completed"
                entry = self._record_resolution(
                    world, event, original_status, "triggered", "conditions satisfied"
                )
                existing_ids.add(event_id)
            else:
                # Never claim completion when the world did not change.
                event["status"] = "pending"
                metadata = event.setdefault("metadata", {})
                metadata["last_error"] = result.error
                metadata["last_failure_at"] = float(world.time)
                metadata["failure_count"] = int(metadata.get("failure_count", 0) or 0) + 1
                plot_deviation.record_deviation_evidence(
                    event, plot_deviation.KIND_EXECUTION_FAILED,
                    str(result.error or "event execution failed"), world,
                )
                entry = {
                    "event_id": event_id,
                    "original_status": "pending",
                    "final_status": "pending",
                    "timestamp": world.time,
                    "reason": f"execution failed: {result.error}",
                    "id": event_id,
                    "original": "pending",
                    "final": "pending",
                }
            history_entries.append(entry)

        return history_entries

    def trigger_event(self, world, event_id):
        """Force one specific event to execute now (still condition-checked)."""
        event = self.get_event(event_id)
        if event is None:
            return TriggerResult(event_id, "missing", reason="unknown event")
        if event.get("status") in {"cancelled", "modified"} or event.get("status") in EXECUTED_STATUSES:
            return TriggerResult(event_id, event["status"], reason="already resolved")
        defer_reason = _dialogue_defer_reason(world, event)
        if defer_reason:
            event["status"] = "deferred"
            event.setdefault("metadata", {})["defer_reason"] = defer_reason
            return TriggerResult(event_id, "deferred", reason=defer_reason)
        conditions_ok, reason = self._all_satisfied(world, event.get("conditions"))
        if not conditions_ok:
            return TriggerResult(event_id, event.get("status", "pending"), reason=reason)
        result = self._execute_effects(world, event)
        if result.executed:
            event["status"] = "triggered"
            event.setdefault("metadata", {})["execution_status"] = "completed"
        return result

    # -- Helpers ------------------------------------------------------
    @staticmethod
    def _trigger_time(event):
        # trigger_time is the legacy alias and is still written directly by a
        # few older callers, so it wins when present. New code writes both
        # aliases through add_event/update_event, keeping them in sync.
        trigger_time = event.get("trigger_time")
        if trigger_time is not None:
            return trigger_time
        return event.get("trigger_at")

    @classmethod
    def _sort_key(cls, event):
        trigger_at = cls._trigger_time(event)
        if trigger_at is None:
            trigger_at = math.inf
        return (float(trigger_at), -float(event.get("priority", DEFAULT_PRIORITY)))

    @classmethod
    def _is_due(cls, world, event):
        trigger_at = cls._trigger_time(event)
        if trigger_at is None:
            return True
        return world.time >= float(trigger_at)

    def _all_satisfied(self, world, conditions):
        for condition in conditions or []:
            parsed = StoryCondition.from_dict(condition)
            if not parsed.evaluate(world, self):
                return False, f"condition not met: {parsed.to_dict()}"
        return True, ""

    def _any_satisfied(self, world, conditions):
        for condition in conditions or []:
            parsed = StoryCondition.from_dict(condition)
            if parsed.evaluate(world, self):
                return True
        return False

    @staticmethod
    def _execute_effects(world, event):
        effects = event.get("effects") or []
        if not effects:
            return TriggerResult(
                event["event_id"], "triggered", triggered=True, executed=True,
                phase="completed", reason="marker event without effects",
            )
        payload = {"event_id": event["event_id"], "time": world.time, "effects": effects}
        executor = EventExecutor()
        try:
            executor.execute(world, payload)
        except Exception as exc:  # never fake completion on failure
            return TriggerResult(
                event["event_id"], "pending", triggered=False, executed=False,
                phase="failed", error=str(exc),
            )
        return TriggerResult(
            event["event_id"], "triggered", triggered=True, executed=True,
            deferred_moves=list(executor.last_move_defers),
        )

    @staticmethod
    def _record_resolution(world, event, original_status, final_status, reason, event_id=None):
        event["status"] = final_status
        record_id = event_id or event["event_id"]
        entry = {
            "event_id": record_id,
            "original_event_id": event["event_id"],
            "original_status": original_status,
            "final_status": final_status,
            "timestamp": world.time,
            "reason": reason,
            "id": record_id,
            "original": original_status,
            "final": final_status,
        }
        world.story_event_history.append(entry)
        return entry


def ensure_story_timeline(world):
    """Return world.story_timeline as a StoryTimeline, wrapping if needed."""
    timeline = getattr(world, "story_timeline", None)
    if not isinstance(timeline, StoryTimeline):
        timeline = StoryTimeline(timeline or [])
        world.story_timeline = timeline
    return timeline
