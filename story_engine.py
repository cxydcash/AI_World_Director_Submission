"""Story timeline engine: due events, history, and Director-driven changes."""

from story_timeline import ensure_story_timeline


STORY_DESCRIPTIONS = {
    "ren_missing": "Ren entered the forest and lost contact.",
    "mira_search": "Mira starts looking for Ren.",
    "villagers_find_ren": "Villagers find Ren trapped in the forest.",
    "ren_worsens": "Ren condition worsens.",
    "ren_dies": "Ren dies because nobody rescued him.",
    "ren_discovers_ledger": "Ren discovers altered entries in the village ledger.",
    "ren_seeks_evidence": "Ren searches for the hidden ledger.",
    "mayor_notices": "The mayor notices Ren is investigating.",
    "ren_pursued": "Ren is hunted by the mayor people.",
    "ren_lost": "Ren disappears before exposing the mayor.",
    "external_audit": "An external audit reaches the village.",
    "mayor_flees": "The mayor flees before being caught.",
    "player_saved_Ren": "The player protected Ren before the mayor hunt.",
    "village_investigation": "Villagers investigate the mayor corruption.",
    "mira_seeks_truth": "Mira decides to keep searching for the truth.",
    "village_manhunt": "Villagers hunt the fleeing mayor.",
    "new_arrival": "A stranger arrives with new clues.",
    "village_heals": "The village begins to recover.",
    "second_chapter": "A new chapter begins.",
}


def keep_decision(reason="no change"):
    return {"decision": "keep", "reason": reason, "modified_event": None}


def timeline_summary(world):
    """Return a display-friendly story timeline."""
    summary = []
    for entry in world.story_timeline:
        event_id = entry.get("id", entry.get("event_id"))
        summary.append(
            {
                "id": event_id,
                "event_id": entry.get("event_id", event_id),
                "trigger_time": entry.get("trigger_time", entry.get("trigger_at")),
                "trigger_at": entry.get("trigger_at", entry.get("trigger_time")),
                "status": entry["status"],
                "description": entry.get("description") or STORY_DESCRIPTIONS.get(event_id, ""),
                "title": entry.get("title", ""),
                "conditions": [dict(item) for item in entry.get("conditions", [])],
                "effects": [dict(item) for item in entry.get("effects", [])],
                "source": entry.get("source", "scenario"),
                "priority": entry.get("priority", 0.0),
            }
        )
    return summary


def check_story_events(world, evaluator=None):
    """Evaluate and trigger due story events once (data-driven)."""
    timeline = ensure_story_timeline(world)
    return timeline.check_due_events(world, evaluator=evaluator)
