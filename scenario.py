"""Predefined story timelines expressed as data-driven StoryEvent records.

Every node carries its own trigger time, conditions and Event DSL effects. The
engine forwards those effects to EventValidator/EventExecutor, so no branch on
a specific event_id is needed to change the world.
"""

from story_timeline import ensure_story_timeline


TIMELINE_STATUSES = {
    "pending", "triggered", "completed", "cancelled", "blocked", "failed", "modified",
}


def _event(
    event_id,
    trigger_at,
    title,
    effects,
    conditions=None,
    cancel_conditions=None,
    priority=0.0,
    source="scenario",
    description="",
):
    """Build one plain-data StoryEvent with legacy id/trigger_time aliases."""
    return {
        "event_id": event_id,
        "id": event_id,
        "title": title,
        "name": title,
        "description": description,
        "status": "pending",
        "created_at": 0.0,
        "trigger_at": trigger_at,
        "trigger_time": trigger_at,
        "conditions": list(conditions or []),
        "cancel_conditions": list(cancel_conditions or []),
        "effects": list(effects),
        "priority": priority,
        "source": source,
        "metadata": {},
    }


def _history(action, target, content):
    return {"type": "add_history", "actor": "story", "action": action, "target": target, "content": content}


def default_timeline():
    """Return the fixed village story timeline for a new world."""
    return [
        _event(
            "ren_missing",
            8 * 3600,
            "Ren missing",
            [
                {"type": "set_character_state", "npc_id": "npc_2", "key": "location", "value": "forest"},
                _history("ren_missing", "npc_2", "Ren was found missing."),
            ],
            description="Ren entered the forest and lost contact.",
        ),
        _event(
            "mira_search",
            12 * 3600,
            "Mira search",
            [
                {"type": "add_goal", "npc_id": "npc_1", "goal": "find Ren"},
                {"type": "set_belief", "npc_id": "npc_1", "key": "ren_may_be_in_danger", "value": 0.7},
                {"type": "add_memory", "npc_id": "npc_1", "content": "Ren is missing."},
                _history("villagers_search", "npc_1", "Mira began searching for Ren."),
            ],
            description="Mira starts looking for Ren.",
        ),
        _event(
            "villagers_find_ren",
            18 * 3600,
            "Villagers find Ren",
            [
                {"type": "set_character_state", "npc_id": "npc_2", "key": "injured", "value": True},
                {"type": "set_character_state", "npc_id": "npc_2", "key": "location", "value": "forest"},
                _history("villagers_found_ren", "npc_2", "Ren was found trapped."),
            ],
            conditions=[{"type": "character_state", "npc_id": "npc_2", "key": "rescued", "value": False}],
            cancel_conditions=[{"type": "character_state", "npc_id": "npc_2", "key": "rescued", "value": True}],
            description="Villagers find Ren trapped in the forest.",
        ),
        _event(
            "ren_worsens",
            36 * 3600,
            "Ren worsens",
            [
                {"type": "set_character_state", "npc_id": "npc_2", "key": "injured", "value": True},
                {"type": "change_character_state", "npc_id": "npc_2", "key": "health", "delta": -20},
                _history("ren_worsens", "npc_2", "Ren condition worsened."),
            ],
            conditions=[
                {"type": "character_state", "npc_id": "npc_2", "key": "rescued", "value": False},
                {"type": "character_state", "npc_id": "npc_2", "key": "alive", "value": True},
            ],
            cancel_conditions=[
                {"type": "character_state", "npc_id": "npc_2", "key": "rescued", "value": True},
                {"type": "character_state", "npc_id": "npc_2", "key": "alive", "value": False},
            ],
            description="Ren condition worsens.",
        ),
        _event(
            "ren_dies",
            60 * 3600,
            "Ren dies",
            [
                {"type": "set_character_state", "npc_id": "npc_2", "key": "alive", "value": False},
                {"type": "fail_quest", "quest_id": "quest_001"},
                _history("ren_died", "npc_2", "Ren died in the forest."),
            ],
            conditions=[{"type": "character_state", "npc_id": "npc_2", "key": "rescued", "value": False}],
            cancel_conditions=[{"type": "character_state", "npc_id": "npc_2", "key": "rescued", "value": True}],
            description="Ren dies because nobody rescued him.",
        ),
    ]


def corruption_timeline():
    """Return the hidden-conflict corruption timeline for the open-world demo."""
    return [
        _event(
            "ren_discovers_ledger",
            8 * 3600,
            "Ren discovers the ledger",
            [
                {"type": "set_character_state", "npc_id": "npc_2", "key": "location", "value": "town"},
                {"type": "set_belief", "npc_id": "npc_2", "key": "mayor_is_corrupt", "value": 0.8},
                {"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"},
                {"type": "add_memory", "npc_id": "npc_2", "content": "The village ledger contains altered entries."},
                _history("ren_discovers_ledger", "npc_2", "Ren found altered ledger entries."),
            ],
            description="Ren discovers altered entries in the village ledger.",
        ),
        _event(
            "ren_seeks_evidence",
            32 * 3600,
            "Ren seeks evidence",
            [
                {"type": "set_character_state", "npc_id": "npc_2", "key": "location", "value": "town"},
                {"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"},
                {"type": "move_npc", "npc_id": "npc_2", "x": 70.0, "y": 70.0},
                _history("ren_seeks_evidence", "npc_2", "Ren began searching for proof."),
            ],
            conditions=[{"type": "event_completed", "event_id": "ren_discovers_ledger"}],
            description="Ren searches for the hidden ledger.",
        ),
        _event(
            "mayor_notices",
            56 * 3600,
            "Mayor notices",
            [
                {"type": "set_belief", "npc_id": "npc_1", "key": "ren_is_investigating", "value": 0.9},
                {"type": "add_goal", "npc_id": "npc_1", "goal": "stop Ren"},
                {"type": "add_memory", "npc_id": "npc_1", "content": "Ren is getting close to the truth."},
                _history("mayor_notices", "npc_1", "The mayor learned Ren is investigating."),
            ],
            conditions=[{"type": "event_completed", "event_id": "ren_seeks_evidence"}],
            description="The mayor notices Ren is investigating.",
        ),
        _event(
            "ren_pursued",
            80 * 3600,
            "Ren pursued",
            [
                {"type": "set_character_state", "npc_id": "npc_2", "key": "hunted", "value": True},
                {"type": "add_goal", "npc_id": "npc_1", "goal": "frame Ren"},
                _history("ren_pursued", "npc_2", "The mayor sent people after Ren."),
            ],
            conditions=[{"type": "event_completed", "event_id": "mayor_notices"}],
            description="Ren is hunted by the mayor people.",
        ),
        _event(
            "ren_lost",
            104 * 3600,
            "Ren lost",
            [
                {"type": "set_character_state", "npc_id": "npc_2", "key": "alive", "value": False},
                _history("ren_lost", "npc_2", "Ren disappeared while being hunted."),
            ],
            conditions=[{"type": "character_state", "npc_id": "npc_2", "key": "rescued", "value": False}],
            cancel_conditions=[
                {"type": "character_state", "npc_id": "npc_2", "key": "rescued", "value": True},
                {"type": "character_state", "npc_id": "npc_2", "key": "protected", "value": True},
                # The player travelling with Ren keeps him from vanishing.
                {"type": "escorting_player", "npc_id": "npc_2", "radius": 12.0},
            ],
            description="Ren disappears before exposing the mayor.",
        ),
        _event(
            "external_audit",
            128 * 3600,
            "External audit",
            [
                {"type": "add_goal", "npc_id": "npc_4", "goal": "investigate mayor"},
                {"type": "set_belief", "npc_id": "npc_4", "key": "village_funds_suspicious", "value": 0.6},
                _history("external_audit", "npc_4", "An external auditor arrived in the village."),
            ],
            description="An external audit reaches the village.",
        ),
        _event(
            "mayor_flees",
            152 * 3600,
            "Mayor flees",
            [
                {"type": "set_character_state", "npc_id": "npc_1", "key": "fled", "value": True},
                _history("mayor_flees", "npc_1", "The mayor fled before the audit concluded."),
            ],
            cancel_conditions=[
                {"type": "character_state", "npc_id": "npc_1", "key": "exposed", "value": True},
                {"type": "character_state", "npc_id": "npc_1", "key": "arrested", "value": True},
            ],
            description="The mayor flees before being caught.",
        ),
    ]


def process_timeline(world):
    """Trigger pending timeline events whose time and conditions are met."""
    timeline = ensure_story_timeline(world)
    history = timeline.check_due_events(world)
    return [entry["event_id"] for entry in history if entry.get("final_status") == "triggered"]


def cancel_timeline_events(world, event_ids):
    """Cancel future pending timeline events."""
    timeline = ensure_story_timeline(world)
    cancelled = []
    for event_id in event_ids:
        if timeline.cancel_event(event_id):
            cancelled.append(event_id)
    return cancelled


def timeline_summary(world):
    """Return a compact timeline summary for Director context."""
    return [
        {
            "id": entry["id"],
            "event_id": entry["event_id"],
            "trigger_time": entry["trigger_time"],
            "trigger_at": entry["trigger_at"],
            "status": entry["status"],
            "title": entry.get("title", ""),
            "description": entry.get("description", ""),
            "source": entry.get("source", "scenario"),
            "priority": entry.get("priority", 0.0),
        }
        for entry in world.story_timeline
    ]


def validate_timeline_change(world, proposal):
    """Validate a safe timeline-change proposal from the Director."""
    if not isinstance(proposal, dict):
        raise ValueError("Timeline change must be an object.")
    if proposal.get("type") != "timeline_change":
        raise ValueError("Timeline change type must be timeline_change.")

    allowed = {"type", "changes", "npc_changes"}
    unknown = set(proposal) - allowed
    if unknown:
        raise ValueError(f"Unknown timeline change fields: {sorted(unknown)}.")

    changes = proposal.get("changes", [])
    npc_changes = proposal.get("npc_changes", [])
    if not isinstance(changes, list) or not isinstance(npc_changes, list):
        raise ValueError("changes and npc_changes must be lists.")

    timeline_ids = {entry["id"] for entry in world.story_timeline}
    normalized_changes = []
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("Each timeline change must be an object.")
        if set(change) != {"event", "status"}:
            raise ValueError("Timeline changes require exactly event and status.")
        event_name = change["event"]
        if event_name not in timeline_ids:
            raise ValueError(f"Unknown timeline event: {event_name}.")
        if change["status"] not in {"cancelled", "triggered"}:
            raise ValueError("Timeline status must be cancelled or triggered.")
        entry = next(item for item in world.story_timeline if item["id"] == event_name)
        if entry["status"] != "pending" and entry["status"] != change["status"]:
            raise ValueError(f"Timeline event cannot change: {event_name}.")
        normalized_changes.append(dict(change))

    normalized_npc_changes = []
    for change in npc_changes:
        if not isinstance(change, dict):
            raise ValueError("Each NPC change must be an object.")
        allowed_npc = {"npc_id", "npc", "belief", "value", "relationship_delta", "goal"}
        unknown_npc = set(change) - allowed_npc
        if unknown_npc:
            raise ValueError(f"Unknown NPC change fields: {sorted(unknown_npc)}.")
        npc_id = change.get("npc_id", change.get("npc"))
        if world.get_npc(npc_id) is None:
            raise ValueError(f"NPC not found: {npc_id}.")
        normalized = {"npc_id": npc_id}
        if "belief" in change:
            if "value" not in change:
                raise ValueError("belief changes require value.")
            normalized["belief"] = str(change["belief"])
            normalized["value"] = float(change["value"])
        if "relationship_delta" in change:
            normalized["relationship_delta"] = float(change["relationship_delta"])
        if "goal" in change:
            normalized["goal"] = str(change["goal"])
        normalized_npc_changes.append(normalized)

    return {
        "type": "timeline_change",
        "changes": normalized_changes,
        "npc_changes": normalized_npc_changes,
    }


def apply_timeline_change(world, proposal):
    """Apply a validated timeline change without arbitrary code execution."""
    normalized = validate_timeline_change(world, proposal)
    for change in normalized["changes"]:
        event_name = change["event"]
        entry = next(
            item for item in world.story_timeline if item["id"] == event_name
        )
        if entry["status"] == change["status"]:
            continue
        if entry["status"] != "pending":
            raise ValueError(f"Timeline event is not pending: {event_name}.")
        entry["status"] = change["status"]

    for change in normalized["npc_changes"]:
        npc = world.get_npc(change["npc_id"])
        if "belief" in change:
            npc.set_belief(change["belief"], change["value"])
        if "relationship_delta" in change:
            npc.change_relationship("player", change["relationship_delta"])
        if "goal" in change:
            # A timeline change is a Director-decided proposal; GoalManager is
            # still the only writer (Step 12.2).
            from goal_manager import GoalManager
            from director_input import GoalChange, GOAL_CHANGE_ADD
            GoalManager().apply(world, GoalChange(
                change_type=GOAL_CHANGE_ADD,
                npc_id=change["npc_id"],
                goal_string=str(change["goal"]),
                reason="AI Director timeline change",
                source="ai_director",
            ))

    world.add_history(
        "timeline_changed",
        "story",
        "Director adjusted the village timeline.",
    )
    return normalized
