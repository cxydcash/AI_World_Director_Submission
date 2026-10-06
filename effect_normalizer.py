"""Best-effort normalizer for LLM-authored Event DSL payloads.

The AI Director is allowed to *propose* data only. Models regularly drift on
field names ("effect" instead of "type", "belief" instead of "key",
"npc" instead of "npc_id", a place name instead of x/y, ...). This module
repairs those format-level mistakes before the strict EventValidator runs.

Rule of thumb::

    格式错误可以修；语义缺失不能猜。

The normalizer NEVER invents an npc_id, evidence_id, coordinate, belief value
or goal. If a required fact is missing, the payload is passed through untouched
so the strict validator rejects it with an explicit reason.
"""

# Alias -> canonical DSL field name. Only applied when the canonical field is
# absent, so a payload that already uses the real name is never overwritten.
FIELD_ALIASES = {
    "npc": "npc_id",
    "memory": "content",
    "text": "content",
}

# Out-of-schema chatter that carries no world meaning and is safe to drop.
DROPPABLE_FIELDS = frozenset(
    {"reason", "rationale", "explanation", "note", "why", "comment"}
)

# Effects that queue an NPC goal. The goal string must stay Planner-readable.
GOAL_EFFECT_TYPES = frozenset({"add_goal"})

EFFECT_EXAMPLES = {
    "add_memory": {"npc_id": "npc_2", "content": "我看见村长夜里去了磨坊。"},
    "set_belief": {"npc_id": "npc_2", "key": "mayor_is_corrupt", "value": 0.8},
    "change_relationship": {"npc_id": "npc_2", "target_id": "player", "delta": 0.2},
    "add_goal": {"npc_id": "npc_2", "goal": "investigate:ev_ledger_copy"},
    "remove_goal": {"npc_id": "npc_2", "goal": "wait"},
    "move_npc": {"npc_id": "npc_2", "x": 40.0, "y": 40.0},
    "spawn_item": {"item_id": "item_note", "item_type": "note", "x": 40.0, "y": 40.0},
    "add_evidence": {
        "evidence_id": "ev_ledger_copy",
        "evidence_type": "ledger_copy",
        "x": 50.0,
        "y": 34.0,
        "description": "一张被雨水泡皱的账本复印件。",
    },
    "dialogue": {"speaker_id": "npc_2", "content": "我得把这件事查清楚。"},
    "set_follow_target": {"npc_id": "npc_2", "follow_target": "player"},
    "create_npc": {
        "name": "Ivy",
        "role": "auditor",
        "personality": ["strict"],
        "goal": "reach_location:town",
        "x": 5.0,
        "y": 95.0,
    },
    "add_history": {
        "action": "rumor_spread",
        "target": "npc_3",
        "content": "A rumor about the ledger reached Mira.",
    },
    "set_character_state": {"npc_id": "npc_1", "key": "exposed", "value": True},
    "change_character_state": {"npc_id": "npc_1", "key": "health", "delta": -10},
    "fail_quest": {"quest_id": "quest_ledger"},
}

FIELD_DOCS = {
    "npc_id": "string, an existing npc id",
    "target_id": "string, an existing npc id or \"player\"",
    "npc": "string, an existing npc id",
    "item_id": "string, an existing or newly spawned item id",
    "item_type": "string",
    "evidence_id": "string, a brand new unique evidence id",
    "evidence_type": "string",
    "speaker_id": "string, an existing npc id or \"player\"",
    "quest_id": "string",
    "key": "string, the state/belief key",
    "goal": "string, a Planner goal (see the goal rules)",
    "content": "string",
    "description": "string",
    "name": "string",
    "role": "string",
    "personality": "array of strings",
    "background": "string",
    "follow_target": "string npc id, \"player\", or null",
    "x": "number 0..100",
    "y": "number 0..100",
    "delta": "number",
    "value": "number 0..1 for beliefs, otherwise string|number|boolean",
    "vision_range": "number",
    "actor": "string",
    "action": "string",
    "target": "string",
    "source_event": "string, optional event id",
    "linked_belief": "string, the belief key this evidence strengthens",
    "belief_delta": "number, belief shift (default 0.2)",
    "follow_up_goal": "string, a Planner goal queued when the evidence is found",
    "related_object": "string, an object id",
    "related_npc": "string, an existing npc id",
}



def _supported_effect_types():
    from event import SUPPORTED_EFFECT_TYPES

    return SUPPORTED_EFFECT_TYPES


def _unwrap_nested(effect, notes):
    """Turn {"add_memory": {...}} into {"type": "add_memory", ...}."""
    if "type" in effect or "effect" in effect or len(effect) != 1:
        return effect
    key, value = next(iter(effect.items()))
    if key in _supported_effect_types() and isinstance(value, dict):
        merged = dict(value)
        merged["type"] = key
        notes.append(f"unwrapped nested {key}")
        return merged
    return effect


def _lookup_location(value):
    """Map a place name or [x, y] pair onto coordinates, or return None."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    from world import EXPLORE_LOCATIONS, LOCATION_ALIASES

    key = value.strip().lower()
    key = LOCATION_ALIASES.get(key, key)
    coordinates = EXPLORE_LOCATIONS.get(key)
    if coordinates is None:
        return None
    return (float(coordinates[0]), float(coordinates[1]))


def _resolve_location(effect, notes):
    if "location" not in effect:
        return effect
    location = effect.pop("location")
    has_xy = isinstance(effect.get("x"), (int, float)) and isinstance(effect.get("y"), (int, float))
    if has_xy:
        notes.append("dropped redundant location")
        return effect
    coordinates = _lookup_location(location)
    if coordinates is None:
        # Never guess: leave x/y missing so the validator reports it.
        notes.append(f"unmapped location {location!r} dropped")
        return effect
    effect["x"], effect["y"] = coordinates
    notes.append(f"location {location!r}->{coordinates}")
    return effect


def normalize_effect(raw, notes=None):
    """Return a repaired copy of one effect. Never invents missing facts."""
    if not isinstance(raw, dict):
        return raw
    if notes is None:
        notes = []
    effect = _unwrap_nested(dict(raw), notes)

    if "type" not in effect and "effect" in effect:
        effect["type"] = effect.pop("effect")
        notes.append("effect->type")

    parameters = effect.pop("parameters", None)
    if isinstance(parameters, dict):
        for key, value in parameters.items():
            effect.setdefault(key, value)
        notes.append("flattened parameters")

    if "belief" in effect and "key" not in effect:
        effect["key"] = effect.pop("belief")
        notes.append("belief->key")

    for alias, canonical in FIELD_ALIASES.items():
        if alias in effect and canonical not in effect:
            effect[canonical] = effect.pop(alias)
            notes.append(f"{alias}->{canonical}")

    for field in list(effect):
        if field in DROPPABLE_FIELDS:
            effect.pop(field)
            notes.append(f"dropped {field}")

    return _resolve_location(effect, notes)


def normalize_effects(raw_effects):
    """Normalize a list of effects, returning (effects, notes)."""
    notes = []
    if not isinstance(raw_effects, list):
        return raw_effects, notes
    return [normalize_effect(item, notes) for item in raw_effects], notes


def unsupported_goals(effects):
    """Goal strings in this effect list that the NPCPlanner cannot execute."""
    from npc_planner import NPCPlanner

    planner = NPCPlanner()
    rejected = []
    for effect in effects or []:
        if not isinstance(effect, dict):
            continue
        if effect.get("type") not in GOAL_EFFECT_TYPES:
            continue
        goal = effect.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            continue  # the strict validator owns missing/invalid goal text
        if not planner.can_plan_goal(goal):
            rejected.append(goal)
    return rejected


def goal_rules_text():
    """The Planner-compatible goal vocabulary, as prompt text."""
    from npc_planner import ACTIONABLE_GOAL_TYPES

    lines = [
        "GOAL FORMAT (add_goal / remove_goal / create_npc.goal):",
        "- A goal must be a mechanical, Planner-readable string. Never a sentence.",
        "- Supported targeted formats: reach_location:<location>, find_item:<item_id>,",
        "  find_npc:<npc_id>, talk_to:<npc_id>, follow_npc:<npc_id>,",
        "  investigate:<evidence_id|npc_id|object_id>, help_npc:<npc_id>, lead_player_to:<location>.",
        "- Supported bare goals: " + ", ".join(sorted(ACTIONABLE_GOAL_TYPES - {
            "reach_location", "find_item", "find_npc", "talk_to", "follow_npc",
            "investigate", "help_npc", "lead_player_to",
        })) + ".",
        "- WRONG: \"bring ledger copy to Ren\" (a sentence, not a goal).",
        "  RIGHT: \"help_npc:npc_2\" or \"talk_to:npc_2\".",
        "- remove_goal must quote an existing goal string exactly; it is not parsed.",
        "- An unsupported goal is rejected; it never becomes an active NPC goal.",
    ]
    return "\n".join(lines)


def condition_schema_text():
    """The StoryCondition vocabulary, as prompt text."""
    return "\n".join([
        "CONDITION SCHEMA (STRICT). A condition is {\"type\": ..., ...fields}.",
        "- character_state: npc_id (existing), key (string), value (any)",
        "- npc_goal_exists: npc_id (existing), goal (string)",
        "- npc_at_location: npc_id (existing), location (home|forest|town|stone_road|village)",
        "- item_exists: item_id (existing)",
        "- player_has_item: item_id (existing)",
        "- belief_true: npc_id (existing), key (string), value or threshold (number 0..1, optional)",
        "- memory_contains: npc_id (existing), text (string)",
        "- event_completed: event_id (MUST already exist in story_state.existing_event_ids)",
        "- event_cancelled: event_id (MUST already exist in story_state.existing_event_ids)",
        "- time_reached: time (number)",
        "- escorting_player: npc_id (existing), radius (number, optional)",
        "RULES FOR CONDITIONS:",
        "- Use [] (no condition) or time_reached whenever you are not certain the",
        "  referenced fact already exists in the given world state.",
        "- event_completed / event_cancelled are ONLY allowed when the event_id",
        "  appears verbatim in story_state.existing_event_ids. Never invent ids",
        "  such as ev_day1_* or a name for the event you are proposing now.",
        "- Never invent an event_id, npc_id or item_id.",
        "- Never add fields that are not listed above.",
    ])


def effect_schema_text():
    """Build the prompt schema block straight from the Event DSL tables."""
    from event import EFFECT_OPTIONAL_FIELDS, EFFECT_REQUIRED_FIELDS

    lines = [
        "EFFECT SCHEMA (STRICT). Every effect is one JSON object with a \"type\" key.",
        "Follow this schema exactly. Do not invent field names from the meaning of",
        "the sentence. Never use \"effect\" instead of \"type\". Never wrap an effect",
        "as {\"add_memory\": {...}}. Never put a place name in \"location\".",
        "",
    ]
    for effect_type in sorted(EFFECT_REQUIRED_FIELDS):
        required = sorted(EFFECT_REQUIRED_FIELDS[effect_type])
        optional = sorted(EFFECT_OPTIONAL_FIELDS.get(effect_type, ()))
        example = {"type": effect_type, **EFFECT_EXAMPLES.get(effect_type, {})}
        lines.append(f"{effect_type}")
        lines.append(
            "  required: "
            + ", ".join(f"{f} ({FIELD_DOCS.get(f, 'value')})" for f in required)
        )
        if optional:
            lines.append(
                "  optional: "
                + ", ".join(f"{f} ({FIELD_DOCS.get(f, 'value')})" for f in optional)
            )
        else:
            lines.append("  optional: none")
        import json as _json

        lines.append("  example: " + _json.dumps(example, ensure_ascii=False))
        lines.append("")
    return "\n".join(lines).rstrip()






