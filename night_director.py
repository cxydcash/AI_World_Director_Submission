# -*- coding: utf-8 -*-
"""Night Director Phase (Step 12.5).

Between 22:00 and 24:00 ordinary progression pauses and the AI Director
re-plans the next day from a structured DailyDirectorContext, returning:

    {goal_changes, story_events, story_updates, new_mysteries, reasoning_summary}
"""

DAY_SECONDS = 86400.0
NIGHT_START_SECOND = 22 * 3600
NIGHT_END_SECOND = 24 * 3600

PHASE_DAY = "day"
PHASE_NIGHT_DIRECTOR = "night_director"

import plot_deviation

MAX_TODAY_ITEMS = 20
MAX_NPC_MEMORY = 6
MAX_TIMELINE = 24
MAX_ITEMS = 30
MAX_EVIDENCE = 20


def day_index(seconds):
    return int(float(seconds) // DAY_SECONDS)


def day_start(seconds):
    return day_index(seconds) * DAY_SECONDS


def phase_for_time(seconds):
    remainder = float(seconds) % DAY_SECONDS
    return PHASE_NIGHT_DIRECTOR if remainder >= NIGHT_START_SECOND else PHASE_DAY


def is_night(seconds):
    return phase_for_time(seconds) == PHASE_NIGHT_DIRECTOR


def seconds_until_next_boundary(seconds):
    seconds = float(seconds)
    start = day_start(seconds)
    if phase_for_time(seconds) == PHASE_DAY:
        return max(0.0, (start + NIGHT_START_SECOND) - seconds)
    return max(0.0, (start + DAY_SECONDS) - seconds)


def night_start_second(seconds):
    return day_start(seconds) + NIGHT_START_SECOND


def next_day_start_second(seconds):
    return day_start(seconds) + DAY_SECONDS


def _in_since(entry, since):
    if not isinstance(entry, dict):
        return False
    try:
        return float(entry.get("time", 0) or 0) >= float(since)
    except (TypeError, ValueError):
        return False


def _tail(entries, since, limit, fallback=3):
    today = [dict(e) for e in (entries or []) if _in_since(e, since)]
    if today:
        return today[-limit:]
    return [dict(e) for e in (entries or [])[-fallback:] if isinstance(e, dict)]


def build_daily_director_context(world):
    """Build a bounded, structured context for the night Director."""
    now = float(world.time)
    day = day_index(now)
    start = day_start(now)
    end = start + DAY_SECONDS

    player_actions = _tail(world.actions, start, MAX_TODAY_ITEMS)
    player_memory = _tail(getattr(world.player, "memory", []), start, MAX_TODAY_ITEMS)
    dialogues = _tail(world.dialogues, start, MAX_TODAY_ITEMS)
    history = _tail(world.history, start, MAX_TODAY_ITEMS * 2)

    npcs = []
    for npc in world.npcs:
        state = world.characters_state.get(npc.id, {})
        goal_states = getattr(npc, "goal_states", None) or {}
        npcs.append({
            "id": npc.id,
            "name": npc.name,
            "role": npc.role,
            "alive": bool(state.get("alive", True)),
            "position": list(world.npc_position(npc.id)),
            "location": world.npc_location(npc.id),
            "active_goal": getattr(npc, "active_goal", None),
            "goals": list(getattr(npc, "goals", []) or []),
            "goal_states": {
                str(k): {
                    "status": v.get("status"),
                    "urgency": v.get("urgency"),
                    "source": v.get("source"),
                }
                for k, v in goal_states.items() if isinstance(v, dict)
            },
            "suspended_goal_stack": [
                dict(record) for record in (getattr(npc, "suspended_goal_stack", None) or [])
            ][:MAX_NPC_MEMORY],
            "beliefs": dict(npc.beliefs),
            "relationships": dict(npc.relationships),
            "memory": _tail(npc.memory, start, MAX_NPC_MEMORY, fallback=3),
            "personality": list(npc.personality),
            "character_seed": {
                "long_term_goal": npc.character_seed.get("long_term_goal", ""),
                "secret": npc.character_seed.get("secret", ""),
                "fear": npc.character_seed.get("fear", ""),
                "conflict": npc.character_seed.get("conflict", ""),
            },
        })

    timeline = []
    counts = {
        "pending": 0, "deferred": 0, "blocked": 0, "triggered": 0,
        "completed": 0, "cancelled": 0, "failed": 0, "modified": 0,
    }
    for entry in list(world.story_timeline)[:MAX_TIMELINE]:
        status = str(entry.get("status", "pending"))
        if status in counts:
            counts[status] += 1
        timeline.append({
            "event_id": entry.get("event_id"),
            "title": entry.get("title", ""),
            "status": status,
            "trigger_at": entry.get("trigger_at"),
            "source": entry.get("source"),
        })

    deviations = plot_deviation.deviations_as_dicts(
        plot_deviation.detect_deviations(world, now=now),
        limit=plot_deviation.MAX_DEVIATIONS,
    )

    return {
        "time": {
            "now": now,
            "day": day,
            "day_start": start,
            "day_end": end,
            "night_start": start + NIGHT_START_SECOND,
            "next_day": day + 1,
        },
        "player": {
            "position": [world.player.x, world.player.y],
            "inventory": list(world.player.inventory),
            "money": getattr(world.player, "money", None),
            "energy": getattr(world.player, "energy", None),
            "actions": player_actions,
            "memory": player_memory,
            "dialogue": dialogues,
        },
        "npcs": npcs,
        "story": {
            "timeline": timeline,
            "counts": counts,
            "pending": [t for t in timeline if t["status"] == "pending"],
            "unresolved": [
                t for t in timeline if t["status"] in {"pending", "deferred", "blocked"}
            ],
            # Step 12.6: structured, read-only plot-deviation facts.
            "deviations": deviations,
        },
        "world": {
            "items": [
                {"id": item.id, "type": item.type, "holder": item.holder}
                for item in world.items[:MAX_ITEMS]
            ],
            "evidence": [
                {
                    "id": e.get("id") or e.get("type"),
                    "type": e.get("type"),
                    "location": e.get("location"),
                    "discovered_by": list(e.get("discovered_by", []) or []),
                }
                for e in world.evidence[:MAX_EVIDENCE]
            ],
            "weather": dict(world.environment_state.get("weather", {})),
            "history": history,
            "story_seeds": [dict(seed) for seed in world.story_seeds[-5:]],
        },
    }


def default_daily_plan(world=None, context=None):
    return {
        "goal_changes": [],
        "story_events": [],
        "story_updates": [],
        "new_mysteries": [],
        "reasoning_summary": "夜间编导：本夜没有新的世界编排。",
    }


def normalize_report(raw):
    if not isinstance(raw, dict):
        raw = {}

    def as_list(key):
        value = raw.get(key)
        return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    return {
        "goal_changes": as_list("goal_changes"),
        "story_events": as_list("story_events"),
        "story_updates": as_list("story_updates"),
        "new_mysteries": as_list("new_mysteries"),
        "reasoning_summary": str(raw.get("reasoning_summary", "")),
    }


def _goal_change_from_item(item):
    from director_input import (
        GOAL_CHANGE_ADD,
        GOAL_CHANGE_REMOVE,
        GoalChange,
        normalize_urgency,
        split_goal_string,
    )
    npc_id = item.get("npc_id") or item.get("npc")
    action = str(item.get("action") or item.get("change_type") or "add").lower()
    goal_string = item.get("goal") or item.get("goal_string")
    if not npc_id or not goal_string:
        return None
    goal_type, target = item.get("goal_type"), item.get("target")
    if not goal_type:
        goal_type, target = split_goal_string(goal_string)
    change_type = GOAL_CHANGE_REMOVE if action in {"remove", "cancel", "delete"} else GOAL_CHANGE_ADD
    return GoalChange(
        change_type=change_type,
        npc_id=str(npc_id),
        goal_type=goal_type,
        target=target,
        goal_string=str(goal_string),
        priority=item.get("priority"),
        urgency=normalize_urgency(item.get("urgency")),
        source=str(item.get("source") or "night_director"),
        reason=str(item.get("reason") or "night director plan"),
    )


def _add_story_event(world, item):
    from story_director import validate_story_event_proposal
    from story_timeline import ensure_story_timeline
    try:
        normalized = validate_story_event_proposal(world, item)
        timeline = ensure_story_timeline(world)
        timeline.add_event(normalized)
        return {"added": True, "event_id": normalized.get("event_id")}
    except Exception as exc:
        return {"added": False, "reason": str(exc)}


def _update_story_event(world, item):
    from story_timeline import ensure_story_timeline
    event_id = item.get("event_id") or item.get("id")
    if not event_id:
        return {"updated": False, "reason": "missing event_id"}
    timeline = ensure_story_timeline(world)
    event = timeline.get_event(event_id)
    if event is None:
        return {"updated": False, "reason": "unknown event"}
    changes = {k: v for k, v in item.items() if k not in {"event_id", "id"}}
    try:
        timeline.update_event(event_id, **changes)
        return {"updated": True, "event_id": event_id, "changes": sorted(changes)}
    except Exception as exc:
        return {"updated": False, "reason": str(exc)}


def _add_mystery(world, item):
    text = item.get("summary") or item.get("content") or item.get("title")
    if not text:
        return {"added": False, "reason": "empty mystery"}
    seed_type = str(item.get("type") or "night_director_mystery")
    try:
        record = world.add_story_seed(seed_type, str(text))
        return {"added": True, "seed": record}
    except Exception as exc:
        return {"added": False, "reason": str(exc)}


def apply_daily_report(world, report):
    """Apply a normalized night report. GoalManager still owns goal writes."""
    from goal_manager import goal_manager_for
    management = goal_manager_for(world)
    applied = {"goals": [], "story_events": [], "story_updates": [], "mysteries": []}

    for item in report.get("goal_changes", []):
        change = _goal_change_from_item(item)
        if change is None:
            applied["goals"].append({"applied": False, "reason": "invalid goal change"})
            continue
        try:
            applied["goals"].append(management.apply(world, change))
        except Exception as exc:
            applied["goals"].append({"applied": False, "reason": str(exc)})

    for item in report.get("story_events", []):
        applied["story_events"].append(_add_story_event(world, item))
    for item in report.get("story_updates", []):
        applied["story_updates"].append(_update_story_event(world, item))
    for item in report.get("new_mysteries", []):
        applied["mysteries"].append(_add_mystery(world, item))
    return applied


def run_night_director(world, director=None):
    """Run one night-director pass, return the normalized report + applied."""
    context = build_daily_director_context(world)
    raw = None
    planner = getattr(director, "plan_next_day", None) if director is not None else None
    if callable(planner):
        try:
            raw = planner(world, context)
        except Exception as exc:
            raw = {"reasoning_summary": f"night director error: {exc}"}
    if not isinstance(raw, dict):
        raw = default_daily_plan(world, context)
    report = normalize_report(raw)
    applied = apply_daily_report(world, report)
    result = dict(report)
    result["applied"] = applied
    result["day"] = day_index(world.time)
    result["generated_at"] = float(world.time)
    return result
