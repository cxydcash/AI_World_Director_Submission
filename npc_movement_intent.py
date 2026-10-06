"""NPC movement intent vs. scripted movement.

This module holds the one rule that keeps NPC movement under the AI/Planner's
authority instead of a preset script:

    A scripted ``move_npc`` is an *expectation*, not a command. If it would
    drop an NPC far away from where the NPC's own active goals are already
    taking it, the script has diverged from the world. The movement must be
    handed to the director to re-plan, not force-teleported.

Everything here is read-only and depends only on the world's schedule table,
so the Event DSL, the Story Planner and the Director can all share it without
creating a second movement system.
"""

from schedule import location_coordinates

# Small tolerance so a scripted position next to the committed destination
# does not count as a divergence.
DIVERGENCE_TOLERANCE = 1.5


def _parse_goal(raw):
    """Return (goal_type, target_id) for a string or dict goal."""
    if isinstance(raw, dict):
        goal_type = str(raw.get("goal_type", raw.get("type", "")) or "").strip().lower()
        target_id = str(raw.get("target_id", raw.get("target", "")) or "").strip()
        return goal_type, target_id
    text = str(raw or "").strip()
    goal_type, _, target_id = text.partition(":")
    return goal_type.strip().lower(), target_id.strip()


def _goal_is_active(goal_states, raw):
    if not isinstance(goal_states, dict):
        return True
    entry = goal_states.get(raw)
    if not isinstance(entry, dict):
        return True
    return entry.get("status") not in {"completed", "failed"}


def _position_of(entity):
    if entity is None:
        return None
    if hasattr(entity, "x") and hasattr(entity, "y"):
        return (float(entity.x), float(entity.y))
    if isinstance(entity, (list, tuple)) and len(entity) == 2:
        return (float(entity[0]), float(entity[1]))
    return None


def committed_destination(world, npc, resolve_location):
    """Where the NPC's own active goals are currently taking it, if anywhere.

    Returns {"goal": raw, "goal_type": type, "kind": "player"|"npc"|
    "location", "target": id, "position": (x, y) | None, "distance": float}.
    Priority mirrors the Planner: lead/follow/reach goals first.
    """
    from npc_planner import ACTIONABLE_GOAL_TYPES  # local import avoids cycles

    if npc is None or not resolve_location:
        return None
    goal_states = getattr(npc, "goal_states", None) or {}
    own_position = None
    try:
        own_position = _position_of(world.npc_position(npc.id))
    except Exception:
        own_position = None

    movement_goals = {"lead_player_to", "follow_npc", "reach_location", "escape", "work"}
    best = None
    for raw in list(getattr(npc, "goals", []) or []):
        goal_type, target_id = _parse_goal(raw)
        if goal_type not in ACTIONABLE_GOAL_TYPES or goal_type not in movement_goals:
            continue
        if not _goal_is_active(goal_states, raw):
            continue

        kind = None
        position = None
        label = target_id
        if goal_type in {"lead_player_to", "reach_location", "escape", "work"}:
            position = resolve_location(target_id)
            kind = "location"
        elif goal_type == "follow_npc":
            if target_id in {"", "player"}:
                position = _position_of(world.player)
                kind = "player"
                label = "player"
            else:
                target = world.get_npc(target_id)
                position = _position_of(target) if target is not None else None
                kind = "npc"
        if position is None:
            continue

        distance = None
        if own_position is not None:
            dx = position[0] - own_position[0]
            dy = position[1] - own_position[1]
            distance = (dx * dx + dy * dy) ** 0.5
        candidate = {
            "goal": raw,
            "goal_type": goal_type,
            "kind": kind,
            "target": label,
            "position": position,
            "distance": distance,
        }
        if best is None or (distance or 0.0) < (best["distance"] or 0.0):
            best = candidate
    return best


def movement_conflict(world, npc, destination, resolve_location):
    """Return the NPC's committed movement intent if a scripted move conflicts.

    ``destination`` is the scripted (x, y). Returns the committed-descriptor when
    the script would pull the NPC far away from its active goal, or ``None``
    when the script is compatible (or the NPC has no active movement goal).
    """
    committed = committed_destination(world, npc, resolve_location)
    if committed is None:
        return None
    try:
        dest_x = float(destination[0])
        dest_y = float(destination[1])
    except (TypeError, ValueError, IndexError):
        return committed
    position = committed["position"]
    dx = dest_x - position[0]
    dy = dest_y - position[1]
    if (dx * dx + dy * dy) ** 0.5 > DIVERGENCE_TOLERANCE:
        return committed
    return None


def scripted_moves(world, event):
    """Return [{"npc_id", "x", "y", "effect"}] for move_npc effects in event."""
    moves = []
    for effect in event.get("effects") or []:
        if not isinstance(effect, dict) or effect.get("type") != "move_npc":
            continue
        npc = world.get_npc(effect.get("npc_id"))
        if npc is None:
            continue
        moves.append({
            "npc_id": npc.id,
            "npc": npc,
            "x": effect.get("x"),
            "y": effect.get("y"),
            "effect": dict(effect),
        })
    return moves


def adapted_event_id(base_id, timeline):
    """A collision-free id for a director-adapted copy of an event."""
    existing = {
        entry.get("event_id") or entry.get("id")
        for entry in (timeline or [])
    }
    candidate = f"{base_id}__adapted"
    suffix = 2
    while candidate in existing:
        candidate = f"{base_id}__adapted_{suffix}"
        suffix += 1
    return candidate
