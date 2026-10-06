# -*- coding: utf-8 -*-
"""Read-only Plot Deviation detection (Step 12.6).

``detect_deviations(world)`` inspects the current WorldState + StoryTimeline
and returns ``PlotDeviation`` facts. It never creates Goals, never edits a
StoryEvent's meaning, never calls an LLM and never decides the plot.

The AI Director consumes the report and decides keep / delay / modify / cancel
/ replace. Only the Director may change the story; GoalManager still owns Goal
writes.
"""

from dataclasses import dataclass, field


# -- severities -----------------------------------------------------------
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"
SEVERITY_RANK = {
    SEVERITY_LOW: 0,
    SEVERITY_MEDIUM: 1,
    SEVERITY_HIGH: 2,
    SEVERITY_CRITICAL: 3,
}

# -- kinds ----------------------------------------------------------------
KIND_NPC_DEAD = "npc_dead"
KIND_NPC_MISSING = "npc_missing"
KIND_NPC_POSITION_MISMATCH = "npc_position_mismatch"
KIND_GOAL_SUSPENDED = "goal_suspended"
KIND_GOAL_FAILED = "goal_failed"
KIND_DEPENDENCY_MISSING = "dependency_missing"
KIND_CONDITION_BLOCKED = "condition_blocked"
KIND_EXECUTION_FAILED = "execution_failed"
KIND_EVENT_OVERDUE = "event_overdue"

PLOT_DEVIATION_KINDS = frozenset({
    KIND_NPC_DEAD,
    KIND_NPC_MISSING,
    KIND_NPC_POSITION_MISMATCH,
    KIND_GOAL_SUSPENDED,
    KIND_GOAL_FAILED,
    KIND_DEPENDENCY_MISSING,
    KIND_CONDITION_BLOCKED,
    KIND_EXECUTION_FAILED,
    KIND_EVENT_OVERDUE,
})

# -- thresholds (kept simple and explicit; no prediction system) ----------
BLOCKED_DEVIATION_SECONDS = 6 * 3600      # blocked this long -> deviation
OVERDUE_DEVIATION_SECONDS = 6 * 3600      # overdue this long  -> deviation
POSITION_MISMATCH_DISTANCE = 12.0         # scripted move vs real position
DEFAULT_DELAY_SECONDS = 6 * 3600          # Director delay fallback
MAX_DEVIATIONS = 20
MAX_EVIDENCE_RECORDS = 10
MAX_DELAY_HISTORY = 20
PENDING_STATUSES = frozenset({"pending", "deferred", "blocked"})


@dataclass
class PlotDeviation:
    """A detected fact about the world deviating from a StoryEvent's script."""

    event_id: str | None = None
    kind: str = KIND_CONDITION_BLOCKED
    evidence: str = ""
    severity: str = SEVERITY_MEDIUM
    detected_at: float = 0.0
    source: str = "plot_deviation"
    npc_id: str | None = None
    item_id: str | None = None
    condition: dict | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "evidence": self.evidence,
            "severity": self.severity,
            "detected_at": self.detected_at,
            "source": self.source,
            "npc_id": self.npc_id,
            "item_id": self.item_id,
            "condition": dict(self.condition) if isinstance(self.condition, dict) else self.condition,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        return cls(
            event_id=data.get("event_id"),
            kind=str(data.get("kind", KIND_CONDITION_BLOCKED)),
            evidence=str(data.get("evidence", "")),
            severity=str(data.get("severity", SEVERITY_MEDIUM)),
            detected_at=float(data.get("detected_at", 0.0) or 0.0),
            source=str(data.get("source", "plot_deviation")),
            npc_id=data.get("npc_id"),
            item_id=data.get("item_id"),
            condition=data.get("condition"),
            metadata=dict(data.get("metadata", {}) or {}),
        )


def record_deviation_evidence(event, kind, evidence, world, limit=MAX_EVIDENCE_RECORDS):
    """Append one compact evidence record to a StoryEvent's metadata (bounded).

    This is *evidence only*: it does not change status, timing or effects.
    """
    if not isinstance(event, dict):
        return None
    meta = event.setdefault("metadata", {})
    records = meta.setdefault("deviation_events", [])
    record = {
        "kind": str(kind),
        "evidence": str(evidence),
        "time": float(getattr(world, "time", 0.0) or 0.0),
    }
    records.append(record)
    if len(records) > limit:
        del records[:-limit]
    return record


# -- helpers --------------------------------------------------------------
def _is_pending(event):
    return str(event.get("status", "pending")) in PENDING_STATUSES


def _looks_like_npc(value):
    return isinstance(value, str) and value.startswith("npc_")


def _referenced_ids(event):
    npc_ids = set()
    item_ids = set()
    evidence_ids = set()
    for effect in event.get("effects") or []:
        if not isinstance(effect, dict):
            continue
        for key in ("npc_id", "speaker_id"):
            value = effect.get(key)
            if _looks_like_npc(value):
                npc_ids.add(value)
        if _looks_like_npc(effect.get("target_id")):
            npc_ids.add(effect["target_id"])
        if effect.get("related_npc") and _looks_like_npc(effect.get("related_npc")):
            npc_ids.add(effect["related_npc"])
        if isinstance(effect.get("item_id"), str) and effect.get("item_id"):
            item_ids.add(effect["item_id"])
        if isinstance(effect.get("evidence_id"), str) and effect.get("evidence_id"):
            evidence_ids.add(effect["evidence_id"])
        if isinstance(effect.get("related_object"), str) and effect.get("related_object"):
            evidence_ids.add(effect["related_object"])
    for condition in (event.get("conditions") or []) + (event.get("cancel_conditions") or []):
        if not isinstance(condition, dict):
            continue
        if _looks_like_npc(condition.get("npc_id")):
            npc_ids.add(condition["npc_id"])
        if condition.get("type") in {"item_exists", "player_has_item"}:
            value = condition.get("item_id")
            if isinstance(value, str) and value:
                item_ids.add(value)
    return npc_ids, item_ids, evidence_ids


def _scripted_moves(event):
    moves = []
    for effect in event.get("effects") or []:
        if not isinstance(effect, dict) or effect.get("type") != "move_npc":
            continue
        if not effect.get("npc_id"):
            continue
        try:
            x = float(effect.get("x"))
            y = float(effect.get("y"))
        except (TypeError, ValueError):
            continue
        moves.append({"npc_id": effect["npc_id"], "x": x, "y": y})
    return moves


def _evidence_exists(world, evidence_id):
    for entry in getattr(world, "evidence", []) or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id") or entry.get("type") or "") == str(evidence_id):
            return True
    return False


def _referenced_event_dependencies(event):
    """(condition_type, event_id) pairs for prerequisite conditions.

    Only ``conditions`` are prerequisites; ``cancel_conditions`` are not.
    """
    deps = []
    for condition in event.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        if condition.get("type") not in {"event_completed", "event_cancelled"}:
            continue
        dependency = condition.get("event_id")
        if isinstance(dependency, str) and dependency.strip():
            deps.append((condition["type"], dependency.strip()))
    return deps


def _timeline_status(timeline, event_id):
    for entry in timeline or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("event_id") or entry.get("id")
        if str(key) == str(event_id):
            return str(entry.get("status", "pending"))
    return None


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# -- detection ------------------------------------------------------------
def detect_deviations(world, now=None):
    """Return the current plot deviations (read-only, no LLM, no decisions)."""
    if world is None:
        return []
    now = _safe_float(world.time if now is None else now)
    timeline = getattr(world, "story_timeline", None) or []
    deviations = []

    for event in timeline:
        if not isinstance(event, dict) or not _is_pending(event):
            continue
        event_id = event.get("event_id")
        meta = event.get("metadata") or {}
        npc_ids, item_ids, evidence_ids = _referenced_ids(event)

        for npc_id in sorted(npc_ids):
            npc = world.get_npc(npc_id)
            if npc is None:
                deviations.append(PlotDeviation(
                    event_id=event_id, kind=KIND_NPC_MISSING, npc_id=npc_id,
                    severity=SEVERITY_HIGH, detected_at=now,
                    evidence=f"Event {event_id} depends on missing NPC {npc_id}.",
                ))
                continue
            state = world.characters_state.get(npc_id, {})
            if not state.get("alive", True):
                deviations.append(PlotDeviation(
                    event_id=event_id, kind=KIND_NPC_DEAD, npc_id=npc_id,
                    severity=SEVERITY_CRITICAL, detected_at=now,
                    evidence=f"Event {event_id} depends on {npc.name}, who is dead.",
                ))
                continue
            goal_states = getattr(npc, "goal_states", None) or {}
            for goal in list(getattr(npc, "goals", []) or []):
                entry = goal_states.get(goal) if isinstance(goal_states, dict) else None
                status = entry.get("status") if isinstance(entry, dict) else None
                if status == "suspended":
                    deviations.append(PlotDeviation(
                        event_id=event_id, kind=KIND_GOAL_SUSPENDED, npc_id=npc_id,
                        severity=SEVERITY_MEDIUM, detected_at=now,
                        evidence=f"{npc.name}'s goal '{goal}' is suspended.",
                        metadata={"goal": goal},
                    ))
                elif status == "failed":
                    deviations.append(PlotDeviation(
                        event_id=event_id, kind=KIND_GOAL_FAILED, npc_id=npc_id,
                        severity=SEVERITY_HIGH, detected_at=now,
                        evidence=f"{npc.name}'s goal '{goal}' has failed.",
                        metadata={"goal": goal},
                    ))

        for item_id in sorted(item_ids):
            if getattr(world, "get_item", None) and world.get_item(item_id) is None:
                deviations.append(PlotDeviation(
                    event_id=event_id, kind=KIND_DEPENDENCY_MISSING, item_id=item_id,
                    severity=SEVERITY_HIGH, detected_at=now,
                    evidence=f"Event {event_id} depends on missing item {item_id}.",
                ))

        for evidence_id in sorted(evidence_ids):
            if not _evidence_exists(world, evidence_id):
                deviations.append(PlotDeviation(
                    event_id=event_id, kind=KIND_DEPENDENCY_MISSING, item_id=evidence_id,
                    severity=SEVERITY_HIGH, detected_at=now,
                    evidence=f"Event {event_id} depends on missing evidence {evidence_id}.",
                ))

        # A prerequisite event that was cancelled / failed / replaced can never
        # satisfy event_completed, so the dependent beat is not merely waiting —
        # its original condition chain is broken. This is handed to the Director
        # as a deviation; the world is NOT auto-patched back onto the script.
        for condition_type, dependency in _referenced_event_dependencies(event):
            status = _timeline_status(timeline, dependency)
            if condition_type == "event_completed":
                if status in {"triggered", "completed"}:
                    continue
                broken = status is None or status in {"cancelled", "failed", "modified"}
            else:  # event_cancelled
                if status == "cancelled":
                    continue
                broken = status is None or status in {"triggered", "completed"}
            if broken:
                deviations.append(PlotDeviation(
                    event_id=event_id, kind=KIND_DEPENDENCY_MISSING,
                    severity=SEVERITY_HIGH, detected_at=now,
                    evidence=(
                        f"Event {event_id} needs {condition_type}:{dependency}, but "
                        f"{dependency} is {status or 'missing'} and can no longer satisfy it."
                    ),
                    condition={"type": condition_type, "event_id": dependency},
                    metadata={"depends_on_event": dependency, "dependency_status": status},
                ))

        blocked_since = meta.get("blocked_since")
        if blocked_since is not None:
            blocked_for = now - _safe_float(blocked_since, now)
            if blocked_for >= BLOCKED_DEVIATION_SECONDS:
                deviations.append(PlotDeviation(
                    event_id=event_id, kind=KIND_CONDITION_BLOCKED,
                    severity=SEVERITY_MEDIUM, detected_at=now,
                    evidence=str(meta.get("blocked_reason") or "conditions have been blocked for a long time"),
                    metadata={"blocked_for": blocked_for},
                ))

        if int(meta.get("failure_count", 0) or 0) > 0:
            deviations.append(PlotDeviation(
                event_id=event_id, kind=KIND_EXECUTION_FAILED,
                severity=SEVERITY_HIGH, detected_at=now,
                evidence=str(meta.get("last_error") or "event effect execution failed"),
                metadata={"failure_count": int(meta.get("failure_count", 0) or 0)},
            ))

        trigger_at = event.get("trigger_at", event.get("trigger_time"))
        if trigger_at is not None:
            overdue_for = now - _safe_float(trigger_at, now)
            if overdue_for >= OVERDUE_DEVIATION_SECONDS:
                deviations.append(PlotDeviation(
                    event_id=event_id, kind=KIND_EVENT_OVERDUE,
                    severity=SEVERITY_HIGH, detected_at=now,
                    evidence=f"Event {event_id} is overdue by {overdue_for:.0f}s.",
                    metadata={"overdue_for": overdue_for},
                ))

        for move in _scripted_moves(event):
            npc = world.get_npc(move["npc_id"])
            if npc is None:
                continue
            if not world.characters_state.get(npc.id, {}).get("alive", True):
                continue
            try:
                distance = world.distance_between(
                    world.npc_position(npc.id), (move["x"], move["y"])
                )
            except Exception:
                continue
            if distance >= POSITION_MISMATCH_DISTANCE:
                deviations.append(PlotDeviation(
                    event_id=event_id, kind=KIND_NPC_POSITION_MISMATCH, npc_id=npc.id,
                    severity=SEVERITY_MEDIUM, detected_at=now,
                    evidence=(
                        f"{npc.name} is {distance:.1f} away from the event's assumed "
                        f"position ({move['x']:g}, {move['y']:g})."
                    ),
                    metadata={"distance": distance, "assumed_position": [move["x"], move["y"]]},
                ))

    deviations = _dedupe(deviations)
    deviations.sort(
        key=lambda d: (-SEVERITY_RANK.get(d.severity, 0), -d.detected_at, str(d.event_id))
    )
    return deviations[:MAX_DEVIATIONS]


def _dedupe(deviations):
    seen = set()
    unique = []
    for deviation in deviations:
        key = (deviation.event_id, deviation.kind, deviation.npc_id, deviation.item_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(deviation)
    return unique


def deviations_as_dicts(deviations, limit=MAX_DEVIATIONS):
    return [d.to_dict() for d in (deviations or [])[:limit]]
