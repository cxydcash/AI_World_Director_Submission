"""Director -> StoryEvent proposal pipeline for V3 Step 5.

WorldState -> Director context -> provider -> validated StoryEvent ->
StoryTimeline.add_event -> (future) trigger -> EventExecutor -> WorldState.

The Director only proposes data. It never mutates WorldState directly and it
never drives NPC behaviour: NPC effects are written as Goals/Beliefs and then
picked up by the NPC Planner.
"""

import json
import math

from director import build_world_context
from effect_normalizer import normalize_effect, normalize_effects
from event import EventValidator
from story_timeline import (
    CONDITION_TYPES,
    StoryCondition,
    ensure_story_timeline,
    normalize_story_event,
)


MAX_EVENTS_PER_CALL = 1
MAX_PENDING_EVENTS = 8
CONTEXT_MEMORY_LIMIT = 5
CONTEXT_ACTION_LIMIT = 8
CONTEXT_EVENT_LIMIT = 8


class StoryEventProposalError(ValueError):
    """Raised when an AI StoryEvent proposal is unsafe or malformed."""


def build_story_director_context(world):
    """Build a bounded context for a future StoryEvent proposal."""
    context = build_world_context(world, {"purpose": "story_event_proposal"})
    timeline = ensure_story_timeline(world)

    pending = [
        {
            "event_id": event["event_id"],
            "title": event.get("title", ""),
            "trigger_at": event.get("trigger_at"),
            "conditions": [dict(item) for item in event.get("conditions", [])],
        }
        for event in timeline
        if event.get("status") == "pending"
    ]
    resolved = [
        {
            "event_id": entry.get("event_id"),
            "final_status": entry.get("final_status"),
            "reason": entry.get("reason"),
        }
        for entry in getattr(world, "story_event_history", [])[-CONTEXT_EVENT_LIMIT:]
    ]
    context["story_state"] = {
        "pending_events": pending,
        "recent_resolved_events": resolved,
        "existing_event_ids": [event["event_id"] for event in timeline],
        "pending_count": len(pending),
    }
    context["constraints"] = {
        "max_events_per_call": MAX_EVENTS_PER_CALL,
        "max_pending_events": MAX_PENDING_EVENTS,
        "trigger_at_must_be_in_the_future": True,
        "allowed_condition_types": sorted(CONDITION_TYPES),
        "allowed_effect_types": sorted(_supported_effect_types()),
    }

    for key in ("event_history", "recent_history", "player_actions", "natural_actions"):
        if isinstance(context.get(key), list):
            context[key] = context[key][-CONTEXT_EVENT_LIMIT:]
    for npc in context.get("npcs", []):
        if isinstance(npc.get("memory"), list):
            npc["memory"] = npc["memory"][-CONTEXT_MEMORY_LIMIT:]
    return context


def _supported_effect_types():
    from event import SUPPORTED_EFFECT_TYPES
    return SUPPORTED_EFFECT_TYPES


def _flatten_payload(raw):
    """Accept {"type": ..., "parameters": {...}} and flat condition/effect dicts."""
    payload = dict(raw)
    parameters = payload.pop("parameters", None)
    if isinstance(parameters, dict):
        for key, value in parameters.items():
            payload.setdefault(key, value)
    return payload


def normalize_condition_payload(raw):
    """Validate one condition against the real StoryCondition schema."""
    if not isinstance(raw, dict):
        raise StoryEventProposalError("condition must be an object")
    payload = _flatten_payload(raw)
    condition_type = payload.get("type")
    if condition_type not in CONDITION_TYPES:
        raise StoryEventProposalError(f"unsupported condition type: {condition_type}")
    return StoryCondition.from_dict(payload).to_dict()


def normalize_effect_payload(raw, notes=None):
    """Repair format drift, then check the effect type against the DSL schema."""
    if not isinstance(raw, dict):
        raise StoryEventProposalError("effect must be an object")
    payload = normalize_effect(raw, notes)
    effect_type = payload.get("type")
    if effect_type not in _supported_effect_types():
        raise StoryEventProposalError(f"unsupported effect type: {effect_type}")
    return payload


def _validate_condition_references(world, timeline, condition):
    condition_type = condition.condition_type
    parameters = condition.parameters

    if condition_type in {"npc_goal_exists", "npc_at_location", "belief_true", "memory_contains", "character_state"}:
        npc_id = parameters.get("npc_id")
        if world.get_npc(npc_id) is None:
            raise StoryEventProposalError(f"condition references unknown NPC: {npc_id}")
    if condition_type == "character_state" and not parameters.get("key"):
        raise StoryEventProposalError("character_state condition requires key")
    if condition_type in {"item_exists", "player_has_item"}:
        item_id = parameters.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise StoryEventProposalError(f"{condition_type} condition requires item_id")
    if condition_type in {"event_completed", "event_cancelled"}:
        event_id = parameters.get("event_id")
        if timeline is None or timeline.get_event(event_id) is None:
            raise StoryEventProposalError(f"condition references unknown event: {event_id}")
    if condition_type == "npc_goal_exists" and not parameters.get("goal"):
        raise StoryEventProposalError("npc_goal_exists condition requires goal")


def event_signature(event):
    """Deterministic duplicate key: conditions + effects, ignoring wording."""
    def canonical(items):
        return tuple(
            sorted(
                json.dumps(item, sort_keys=True, ensure_ascii=False)
                for item in (items or [])
            )
        )

    return (canonical(event.get("conditions")), canonical(event.get("effects")))


def validate_story_event_proposal(world, proposal, timeline=None, now=None):
    """Validate a raw proposal and return a normalized StoryEvent dict."""
    timeline = timeline if timeline is not None else ensure_story_timeline(world)
    now = world.time if now is None else float(now)

    if not isinstance(proposal, dict):
        raise StoryEventProposalError("proposal must be a JSON object")
    if isinstance(proposal.get("events"), list):
        raise StoryEventProposalError(f"at most {MAX_EVENTS_PER_CALL} event per call")

    event_id = proposal.get("event_id", proposal.get("id"))
    if not isinstance(event_id, str) or not event_id.strip():
        raise StoryEventProposalError("event_id is required")
    event_id = event_id.strip()
    if timeline.get_event(event_id) is not None:
        raise StoryEventProposalError(f"duplicate event_id: {event_id}")
    if any(entry.get("event_id") == event_id for entry in getattr(world, "story_event_history", [])):
        raise StoryEventProposalError(f"event_id already used: {event_id}")

    title = str(proposal.get("title", "")).strip()
    description = str(proposal.get("description", "")).strip()
    if not title:
        raise StoryEventProposalError("title is required")
    if not description:
        raise StoryEventProposalError("description is required")

    trigger_at = proposal.get("trigger_at", proposal.get("trigger_time"))
    if trigger_at is None:
        trigger_at = now
    if isinstance(trigger_at, bool) or not isinstance(trigger_at, (int, float)):
        raise StoryEventProposalError("trigger_at must be a number")
    trigger_at = float(trigger_at)
    if not math.isfinite(trigger_at) or trigger_at < 0:
        raise StoryEventProposalError("trigger_at must be a non-negative finite number")
    if trigger_at < now:
        trigger_at = now

    raw_conditions = proposal.get("conditions") or []
    raw_cancels = proposal.get("cancel_conditions") or proposal.get("cancel_when") or []
    raw_effects = proposal.get("effects") or []
    if not isinstance(raw_conditions, list) or not isinstance(raw_cancels, list) or not isinstance(raw_effects, list):
        raise StoryEventProposalError("conditions, cancel_conditions and effects must be lists")
    if not raw_effects:
        raise StoryEventProposalError("at least one effect is required")

    conditions = [normalize_condition_payload(item) for item in raw_conditions]
    cancel_conditions = [normalize_condition_payload(item) for item in raw_cancels]
    effects = []
    normalization_notes = []
    for item in raw_effects:
        notes = []
        effects.append(normalize_effect_payload(item, notes))
        normalization_notes.extend(notes)

    for condition in conditions + cancel_conditions:
        parsed = StoryCondition.from_dict(condition)
        _validate_condition_references(world, timeline, parsed)
    for condition in conditions:
        if condition.get("event_id") == event_id:
            raise StoryEventProposalError("event cannot depend on itself")

    effects_event = {"event_id": event_id, "time": now, "effects": effects}
    EventValidator().validate(world, effects_event)

    metadata = dict(proposal.get("metadata", {}))
    metadata["generated_by"] = "ai_director"
    if normalization_notes:
        metadata["normalized_effects"] = normalization_notes
    normalized = normalize_story_event({
        "event_id": event_id,
        "title": title,
        "description": description,
        "status": "pending",
        "created_at": now,
        "trigger_at": trigger_at,
        "conditions": conditions,
        "cancel_conditions": cancel_conditions,
        "effects": effects,
        "priority": float(proposal.get("priority", 0.0) or 0.0),
        "source": "ai_director",
        "metadata": metadata,
    })

    signature = event_signature(normalized)
    for existing in timeline:
        if event_signature(existing) == signature:
            raise StoryEventProposalError("duplicate story event (same conditions/effects)")

    return normalized


def _rejection_record(raw, exc=None, reason=None):
    """Structured, persistent record of one rejected AI proposal."""
    record = {
        "source": "ai_director",
        "status": "rejected",
        "reason": reason or f"{type(exc).__name__}: {exc}",
        "validation_error": type(exc).__name__ if exc is not None else "rejected",
        "original": raw,
        "normalized": None,
    }
    preview = _normalized_preview(raw)
    if preview is not None:
        record["normalized"] = preview
    return record


def _normalized_preview(raw):
    """Best-effort normalized effects, for logging only. Never raises."""
    if not isinstance(raw, dict):
        return None
    effects = raw.get("effects")
    if not isinstance(effects, list):
        return None
    try:
        normalized, notes = normalize_effects(effects)
    except Exception:
        return None
    return {"effects": normalized, "notes": notes}


def propose_story_event(world, provider, timeline=None, now=None):
    """Run provider -> validate -> StoryTimeline.add_event and return a result."""
    timeline = timeline if timeline is not None else ensure_story_timeline(world)
    now = world.time if now is None else float(now)
    result = {
        "success": False,
        "added": False,
        "event": None,
        "reason": "",
        "raw": None,
        "normalization": [],
        "rejection": None,
    }

    if len(timeline.list_pending()) >= MAX_PENDING_EVENTS:
        result["reason"] = "pending_event_limit_reached"
        result["rejection"] = _rejection_record(None, reason="pending_event_limit_reached")
        return result

    context = build_story_director_context(world)
    try:
        raw = provider.generate_story_event(context)
    except Exception as exc:
        result["reason"] = f"provider_error: {type(exc).__name__}"
        result["rejection"] = _rejection_record(None, exc=exc, reason=result["reason"])
        return result
    result["raw"] = raw

    try:
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict) and isinstance(raw.get("events"), list):
            raise StoryEventProposalError(f"at most {MAX_EVENTS_PER_CALL} event per call")
        proposal = validate_story_event_proposal(world, raw, timeline=timeline, now=now)
    except StoryEventProposalError as exc:
        result["reason"] = f"rejected: {exc}"
        result["rejection"] = _rejection_record(raw, exc=exc)
        return result
    except Exception as exc:
        result["reason"] = f"rejected: {type(exc).__name__}"
        result["rejection"] = _rejection_record(raw, exc=exc, reason=f"{type(exc).__name__}: {exc}")
        return result

    try:
        timeline.add_event(proposal)
    except Exception as exc:
        result["reason"] = f"rejected: {type(exc).__name__}"
        result["rejection"] = _rejection_record(raw, exc=exc, reason=f"{type(exc).__name__}: {exc}")
        return result

    result.update({"success": True, "added": True, "event": proposal, "reason": "added"})
    result["normalization"] = list(proposal.get("metadata", {}).get("normalized_effects", []))
    return result


