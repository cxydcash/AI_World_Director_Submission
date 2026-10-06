"""Minimal Evidence discovery helpers for the Step 10 closure.

StoryEvent --add_evidence--> WorldState.evidence
NPC --search / investigate--> ActionExecutor --> discover()
    --> NPC memory + belief (+ optional follow-up goal)

These helpers only touch the shared evidence list and a single NPC's own
memory / beliefs / goals. They never hand an NPC the global evidence list, and
they never let an LLM write WorldState directly.
"""

# An active search/investigate reaches a little further than passive vision.
DISCOVERY_REACH_BONUS = 3.0

# Default shift applied to the evidence's linked belief when one is declared.
DEFAULT_BELIEF_DELTA = 0.2


def make_record(effect, event):
    """Build one evidence record from a validated ``add_evidence`` effect."""
    record = {
        "id": str(effect["evidence_id"]),
        "type": str(effect["evidence_type"]),
        "location": [float(effect["x"]), float(effect["y"])],
        "description": str(effect["description"]),
        "created_at": float(event["time"]),
        "source_event": str(effect.get("source_event") or event["event_id"]),
        "investigated": False,
        "discovered_by": [],
    }
    if effect.get("linked_belief"):
        record["linked_belief"] = str(effect["linked_belief"])
        record["belief_delta"] = float(effect.get("belief_delta", DEFAULT_BELIEF_DELTA))
    if effect.get("follow_up_goal"):
        record["follow_up_goal"] = str(effect["follow_up_goal"])
    if effect.get("related_npc"):
        record["related_npc"] = str(effect["related_npc"])
    if effect.get("related_object"):
        record["related_object"] = str(effect["related_object"])
    return record


def record_id(entry, index=0):
    """Stable reference for one evidence entry (legacy entries lack an id)."""
    value = entry.get("id")
    if value:
        return str(value)
    location = entry.get("location") or [0.0, 0.0]
    try:
        x, y = float(location[0]), float(location[1])
    except (TypeError, ValueError, IndexError):
        x, y = 0.0, 0.0
    return f"{entry.get('type', 'evidence')}@{x:g},{y:g}#{index}"


def find_in_range(world, position, radius, target=None):
    """Evidence near ``position``; ``target`` optionally filters by id/type."""
    matches = []
    for index, entry in enumerate(world.evidence):
        location = entry.get("location")
        if not isinstance(location, (list, tuple)) or len(location) != 2:
            continue
        try:
            coordinates = (float(location[0]), float(location[1]))
        except (TypeError, ValueError):
            continue
        if world.distance_between(position, coordinates) > radius:
            continue
        if target not in (None, "__location__"):
            keys = {str(entry.get("id", "")).lower(), str(entry.get("type", "")).lower()}
            if str(target).lower() not in keys:
                continue
        matches.append((index, entry))
    return matches


def by_reference(world, reference):
    """Resolve an evidence id (or, failing that, a type) to one entry."""
    if not reference:
        return None
    wanted = str(reference).lower()
    for index, entry in enumerate(world.evidence):
        if str(entry.get("id", "")).lower() == wanted:
            return index, entry
    for index, entry in enumerate(world.evidence):
        if str(entry.get("type", "")).lower() == wanted:
            return index, entry
    return None


def discover(world, npc, entry, index=0):
    """Apply one evidence discovery to one NPC; idempotent per NPC.

    Produces an NPC memory record and an explainable belief shift. A
    follow_up_goal is returned as a *suggestion* only; the AI Director decides
    whether it becomes a goal (Step 12.2). Returns a summary dict.
    """
    name = record_id(entry, index)
    discovered_by = entry.setdefault("discovered_by", [])
    if npc.id in discovered_by:
        return {
            "evidence_id": name,
            "evidence_type": entry.get("type"),
            "already_known": True,
            "belief": None,
            "goal": None,
        }
    discovered_by.append(npc.id)
    entry["investigated"] = True
    entry["investigated_by"] = npc.id

    description = str(entry.get("description", "")).strip()
    content = f"发现证据 {name}（{entry.get('type', 'unknown')}）"
    if description:
        content = f"{content}：{description}"
    npc.add_memory({
        "time": float(world.time),
        "source": "evidence",
        "evidence_id": name,
        "content": content,
    })

    belief_key = entry.get("linked_belief") or f"evidence_{entry.get('type', 'unknown')}"
    delta = float(entry.get("belief_delta", DEFAULT_BELIEF_DELTA))
    current = float(npc.beliefs.get(belief_key, 0.0))
    npc.set_belief(belief_key, max(0.0, min(1.0, current + delta)))

    goal = entry.get("follow_up_goal")

    return {
        "evidence_id": name,
        "evidence_type": entry.get("type"),
        "already_known": False,
        "belief": belief_key,
        "belief_value": npc.beliefs[belief_key],
        # Suggestion only (Step 12.2). The AI Director decides whether this
        # becomes a real NPC goal; the caller routes it through GoalManager.
        "follow_up_goal": goal,
        "goal": None,
        "goal_added": False,
        "goal_decided_by": "ai_director",
    }
