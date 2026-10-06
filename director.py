"""LLM and offline directors that generate Event DSL for the v2 world."""

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

import npc_movement_intent
import director_input as director_input_mod
from effect_normalizer import condition_schema_text, effect_schema_text, goal_rules_text
from schedule import location_coordinates as default_location_coordinates
from event import EventValidationError, EventValidator
from scenario import timeline_summary, validate_timeline_change


DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_LLM_TIMEOUT = 30.0
MAX_CONTEXT_ENTRIES = 5

LLM_SYSTEM_PROMPT = """You are the AI World Director for a small world simulation.

Your job is not to chat with the player and not to write a story. Your job is to
look at the current world state, NPC knowledge, and recent event history, then
generate one Event that changes the world.

You must return only one JSON object and no explanation. Do not output Python,
do not call functions, and do not invent effects that do not exist in the DSL.

The JSON object must have this shape:
{
  "event_id": "event_001",
  "time": 100.0,
  "effects": []
}

""" + effect_schema_text() + """

""" + goal_rules_text() + """

For create_npc, the program assigns the NPC ID. You must not include an id
field, and you may use this effect only when the world state actually calls
for a new character.

For each NPC, character_seed explains why the NPC behaves this way:
- personality: stable traits
- long_term_goal: what the NPC wants over the long term
- secret: something the NPC knows or hides
- fear: what the NPC wants to avoid
- conflict: the internal tension that can produce a meaningful event
Use character_seed together with memory, beliefs, relationships, goals, player
history, time, and location. Do not create random drama that cannot be explained
by those existing states. Never invent facts that contradict the world.

Rules:
- create_npc.personality must be a JSON array of strings, never a single string.
- To change a belief, the effect type must be exactly set_belief, never update_belief or any alias.
- event_id, npc_id, item_id, speaker_id, target_id, key, goal, content, and
  item_type must be non-empty strings.
- time must be a non-negative number.
- x and y must be numbers from 0 to 100.
- value and relationship values must be numbers from 0 to 1.
- delta may be any finite number.
- npc_id must reference an existing NPC.
- item_id must reference a new item ID that does not exist yet.
- speaker_id and target_id must be "player" or an existing NPC id.
- Do not modify entities that do not exist.
- Use only the fields listed above; do not add extra fields.

Choose the Event yourself based on the world state. Do not wait for user
instructions."""
LLM_TIMELINE_PROMPT = """You maintain a predefined story timeline while respecting player actions and NPC state.

Return only one JSON object with this shape:
{
  "type": "timeline_change",
  "changes": [{"event": "villagers_find_ren", "status": "cancelled"}],
  "npc_changes": [
    {"npc_id": "npc_1", "belief": "player_saved_Ren", "value": 1.0,
     "relationship_delta": 0.3, "goal": "thank_player"}
  ]
}

Only use statuses cancelled or triggered. Only reference timeline event IDs and
NPC IDs present in the input. Do not output Python or any other schema. If the
player actions do not change the predefined timeline, return empty lists."""
LLM_OPPORTUNITY_PROMPT = """You are deciding whether to insert one optional world event right now.

Consider whether the player's recent actions, active quest, nearby NPCs, and NPC
character_seed conflicts make an event meaningful. If the situation does not
touch a real NPC goal, fear, secret, relationship, belief, or conflict, return
should_trigger=false. Do not create random drama.

Do not force a story. Return only one JSON object with this shape:
{
  "should_trigger": false,
  "reason": "short explanation",
  "event": null
}

If should_trigger is true, event must be a valid Event DSL object nested under
the "event" field, using only the same effect types and rules as normal event
generation. If false, event must be null. Never return an Event object directly
at the top level. The top-level keys must be exactly should_trigger, reason, and
event. Never output Python or explanations outside the JSON.

Example when true:
{"should_trigger": true, "reason": "Mira's conflict is touched.", "event": {"event_id": "event_001", "time": 100, "effects": [{"type": "add_goal", "npc_id": "npc_1", "goal": "ask_player_about_ren"}]}}

Example when false:
{"should_trigger": false, "reason": "Nothing meaningful is touched.", "event": null}"""


def load_environment():
    """Load simple KEY=value entries from the project .env file if present."""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue

        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def build_world_context(world, context=None):
    """Build a bounded summary of the currently relevant world state."""
    npcs = []
    for npc in world.npcs:
        x, y = world.npc_position(npc.id)
        npcs.append(
            {
                "id": npc.id,
                "name": npc.name,
                "position": [x, y],
                "location": world.npc_location(npc.id),
                "vision_range": npc.vision_range,
                "memory": [dict(entry) for entry in npc.memory[-MAX_CONTEXT_ENTRIES:]],
                "beliefs": dict(npc.beliefs),
                "relationships": dict(npc.relationships),
                "goals": list(npc.goals),
                "personality": list(npc.personality),
                "character_seed": {
                    "personality": list(npc.character_seed["personality"]),
                    "long_term_goal": npc.character_seed["long_term_goal"],
                    "secret": npc.character_seed["secret"],
                    "fear": npc.character_seed["fear"],
                    "conflict": npc.character_seed["conflict"],
                },
            }
        )

    items = [
        {
            "id": item.id,
            "type": item.type,
            "position": [item.x, item.y],
            "holder": item.holder,
        }
        for item in world.items
    ]

    player = {
        "position": [world.player.x, world.player.y],
        "inventory": list(world.player.inventory),
    }
    if hasattr(world.player, "money"):
        player["money"] = world.player.money
    if hasattr(world.player, "energy"):
        player["energy"] = world.player.energy

    payload = {
        "time": world.time,
        "player": player,
        "npcs": npcs,
        "items": items,
        "event_history": [
            dict(entry) for entry in world.event_history[-MAX_CONTEXT_ENTRIES:]
        ],
    }
    if hasattr(world, "quests"):
        payload["active_quests"] = [
            quest.to_dict() for quest in world.quests if quest.status == "active"
        ]
    if hasattr(world, "history"):
        payload["recent_history"] = [
            dict(entry) for entry in world.history[-MAX_CONTEXT_ENTRIES:]
        ]
    if hasattr(world, "nearby_npcs"):
        payload["nearby_npcs"] = [npc.id for npc in world.nearby_npcs()]
    if hasattr(world, "story_timeline"):
        payload["story_timeline"] = timeline_summary(world)
    if hasattr(world, "actions"):
        payload["player_actions"] = [
            dict(entry) for entry in world.actions[-MAX_CONTEXT_ENTRIES:]
        ]
        payload["natural_actions"] = [
            dict(entry)
            for entry in world.actions
            if "parsed_action" in entry
        ][-MAX_CONTEXT_ENTRIES:]
    if hasattr(world, "characters_state"):
        payload["characters_state"] = {
            npc_id: dict(state) for npc_id, state in world.characters_state.items()
        }
    if hasattr(world, "evidence"):
        payload["evidence"] = [
            dict(entry) for entry in world.evidence[-MAX_CONTEXT_ENTRIES:]
        ]
    if hasattr(world, "environment_state"):
        from environment import environment_changes
        payload["environment_state"] = {
            "weather": dict(world.environment_state.get("weather", {})),
            "terrain": {
                name: dict(data)
                for name, data in world.environment_state.get("terrain", {}).items()
            },
        }
        payload["environment_changes"] = environment_changes(
            world.environment_state, world.time
        )
    if context:
        payload["context"] = context
    return payload


def summarize_context(payload):
    """Return a compact, non-sensitive input summary for logs."""
    player = payload["player"]
    inventory = player.get("inventory", [])
    return (
        f"time={payload['time']}, "
        f"player={player.get('position')}, "
        f"inventory={inventory}, "
        f"npcs={len(payload.get('npcs', []))}, "
        f"items={len(payload.get('items', []))}, "
        f"history={len(payload.get('event_history', []))}"
    )


def _goal_change(change_type, npc_id, goal_string, goal_type=None, target=None,
                 reason="", source="ai_director"):
    goal_type = goal_type or director_input_mod.split_goal_string(goal_string)[0]
    target = target if target is not None else director_input_mod.split_goal_string(goal_string)[1]
    return director_input_mod.GoalChange(
        change_type=change_type,
        npc_id=npc_id,
        goal_type=goal_type,
        target=target,
        goal_string=goal_string,
        reason=reason,
        source=source,
    )


def _decide_npc_discovery_goals(world, director_input):
    """Director policy: accept (or reject) a discovery follow-up suggestion.

    The evidence record carries ``follow_up_goal`` as *information only*. The
    Director decides whether it becomes a real NPC goal.
    """
    payload = dict(getattr(director_input, "payload", {}) or {})
    npc_id = getattr(director_input, "target_id", None) or payload.get("npc_id")
    suggestion = payload.get("follow_up_goal")
    if not npc_id or not suggestion:
        return []
    npc = world.get_npc(npc_id)
    state = world.characters_state.get(npc_id, {})
    if npc is None or not state.get("alive", True):
        return []
    goal_string = str(suggestion).strip()
    if not goal_string or goal_string in (npc.goals or []):
        return []
    return [_goal_change(
        director_input_mod.GOAL_CHANGE_ADD, npc_id, goal_string,
        reason=f"AI Director accepted discovery follow-up for {npc_id}",
    )]


def _follow_change(npc_id, following):
    return _goal_change(
        director_input_mod.GOAL_CHANGE_ADD if following else director_input_mod.GOAL_CHANGE_REMOVE,
        npc_id,
        "follow player",
        goal_type="follow_player",
        target="player",
        reason="player ordered follow" if following else "player ended follow",
    )


def _decide_take_ledger_goals(world, director_input, payload):
    """Director policy: the owner of a taken item may start recovering it.

    The caller only supplies affected-NPC *information*; the Director decides.
    """
    context = dict(getattr(director_input, "context", {}) or {})
    affected = context.get("affected_npcs") or payload.get("affected_npcs") or []
    item_id = payload.get("item_id") or "item_ledger"
    changes = []
    for entry in affected:
        if isinstance(entry, dict):
            npc_id = entry.get("npc_id")
            suggestion = entry.get("suggested_goal")
        else:
            npc_id = entry
            suggestion = None
        if not npc_id:
            continue
        npc = world.get_npc(npc_id)
        if npc is None or not world.characters_state.get(npc_id, {}).get("alive", True):
            continue
        goal_string = str(suggestion or f"find_item:{item_id}").strip()
        if not goal_string or goal_string in (npc.goals or []):
            continue
        changes.append(_goal_change(
            director_input_mod.GOAL_CHANGE_ADD, npc_id, goal_string,
            reason=f"AI Director has {npc_id} react to the missing {item_id}",
        ))
    return changes


def _decide_player_action_goals(world, director_input):
    payload = dict(getattr(director_input, "payload", {}) or {})
    action = str(payload.get("action") or "").strip()
    npc_id = getattr(director_input, "target_id", None) or payload.get("npc_id")
    if action == "follow" and npc_id:
        return [_follow_change(npc_id, True)]
    if action == "stop_follow" and npc_id:
        return [_follow_change(npc_id, False)]
    if action == "take_ledger":
        return _decide_take_ledger_goals(world, director_input, payload)
    return []


def _decide_world_event_goals(world, director_input):
    payload = dict(getattr(director_input, "payload", {}) or {})
    kind = str(payload.get("kind") or "").strip()
    npc_id = getattr(director_input, "target_id", None) or payload.get("npc_id")
    if not npc_id:
        return []
    npc = world.get_npc(npc_id)
    if npc is None:
        return []
    if kind == "commitment_wait":
        if "wait" in (npc.goals or []):
            return []
        return [_goal_change(
            director_input_mod.GOAL_CHANGE_ADD, npc_id, "wait",
            reason="AI Director holds the NPC to wait for the player",
            source="commitment",
        )]
    if kind in {"commitment_release", "goal_release"}:
        goal_string = str(payload.get("goal_string") or "wait").strip()
        if goal_string not in (npc.goals or []):
            return []
        return [_goal_change(
            director_input_mod.GOAL_CHANGE_REMOVE, npc_id, goal_string,
            reason="AI Director releases the committed goal",
            source="commitment",
        )]
    return []


def decide_goals_for_input(world, director_input):
    """Deterministic Director policies for non-LLM input sources.

    This is the Director's own decision logic (not a second AI). The dialogue
    source is handled by OpenWorldDirector, which reuses the LLM goal provider.
    """
    if director_input is None:
        return []
    source = getattr(director_input, "source", None)
    if source == director_input_mod.SOURCE_NPC_DISCOVERY:
        return _decide_npc_discovery_goals(world, director_input)
    if source == director_input_mod.SOURCE_PLAYER_ACTION:
        return _decide_player_action_goals(world, director_input)
    if source == director_input_mod.SOURCE_WORLD_EVENT:
        return _decide_world_event_goals(world, director_input)
    return []


class Director(ABC):
    """Interface shared by every event-generating director."""

    @abstractmethod
    def generate_event(self, world, context=None):
        """Return one Event DSL object for the current world state."""
        raise NotImplementedError

    def plan_next_day(self, world, context):
        """Night Director entry point (Step 12.5).

        Return a structured plan dict, or None to keep the current plan.
        The base director proposes no changes.
        """
        return None

    def decide_goals(self, world, director_input):
        """Unified NPC Goal decision entry point (Step 12.1).

        The Director is the single decider of NPC goals. ``director_input`` is a
        ``director_input.DirectorInput`` describing one world signal (only
        player_dialogue is wired so far). Return a list of
        ``director_input.GoalChange`` objects; an empty list means "no change".

        The base director changes nothing. Concrete directors (e.g.
        ``OpenWorldDirector``) override this and reuse the existing NPC goal
        provider/prompt/validation as an implementation backend.
        """
        return []

    def check_opportunity(self, world):
        """Return whether an optional event should trigger right now."""
        return {
            "should_trigger": False,
            "reason": "Opportunity checks are not implemented for this director.",
            "event": None,
        }

    def propose_story_event(self, world):
        """Propose and register one future StoryEvent (opt-in per director)."""
        return {
            "success": False,
            "added": False,
            "event": None,
            "reason": "story event proposals are not supported by this director",
        }

    def evaluate_story_event(self, world, event):
        """Decide whether a story event should keep, modify, or cancel.

        A preset event carries assumptions about where NPCs are. When the
        player has changed the world so those assumptions no longer hold, the
        event is *modified* and re-planned around the real WorldState instead
        of force-moving the NPC back onto the script.
        """
        ren_state = world.characters_state.get("npc_2", {})
        if event["id"] in {"villagers_find_ren", "ren_worsens", "ren_dies"}:
            if ren_state.get("rescued", False):
                return {
                    "decision": "cancel",
                    "reason": "player rescued Ren",
                    "modified_event": None,
                }

        divergence = self.movement_divergence(world, event)
        if divergence is not None:
            return {
                "decision": "modify",
                "reason": divergence["reason"],
                "modified_event": divergence["event"],
            }

        return {
            "decision": "keep",
            "reason": "no player-driven change",
            "modified_event": None,
        }

    def movement_divergence(self, world, event):
        """Re-plan an event whose scripted NPC move contradicts active goals.

        Returns {"reason", "event", "moves"} or None. The adapted event keeps
        every narrative effect, drops only the contradictory teleport, and
        re-asserts the NPC's own committed goal so the change survives.
        """
        effects = event.get("effects") or []
        moves = npc_movement_intent.scripted_moves(world, event)
        conflicting = []
        for move in moves:
            npc = move["npc"]
            conflict = npc_movement_intent.movement_conflict(
                world, npc, (move["x"], move["y"]), self._resolve_location
            )
            if conflict is not None:
                conflicting.append({"move": move, "conflict": conflict})
        if not conflicting:
            return None

        contradicted_ids = {
            (item["move"]["npc_id"], item["move"]["effect"].get("x"), item["move"]["effect"].get("y"))
            for item in conflicting
        }
        adapted_effects = []
        for effect in effects:
            if not isinstance(effect, dict):
                adapted_effects.append(effect)
                continue
            if effect.get("type") == "move_npc" and (
                effect.get("npc_id"), effect.get("x"), effect.get("y")
            ) in contradicted_ids:
                continue
            adapted_effects.append(dict(effect))

        for item in conflicting:
            committed = item["conflict"]
            goal = committed.get("goal")
            if goal and all(
                not (isinstance(e, dict) and e.get("type") == "add_goal"
                     and e.get("npc_id") == item["move"]["npc_id"] and e.get("goal") == goal)
                for e in adapted_effects
            ):
                adapted_effects.append({
                    "type": "add_goal",
                    "npc_id": item["move"]["npc_id"],
                    "goal": goal,
                })

        timeline = getattr(world, "story_timeline", None)
        adapted = dict(event)
        adapted["event_id"] = npc_movement_intent.adapted_event_id(
            str(event.get("event_id", event.get("id", "event"))), timeline
        )
        adapted["id"] = adapted["event_id"]
        adapted["title"] = f"{event.get('title', event.get('name', ''))} (adapted)"
        adapted["description"] = (
            f"{event.get('description', '')} The director re-planned this beat: "
            + "; ".join(
                f"{item['move']['npc'].name} kept {item['conflict']['goal']}"
                for item in conflicting
            )
            + "."
        )
        move_names = ", ".join(item["move"]["npc"].name for item in conflicting)
        adapted["metadata"] = dict(event.get("metadata") or {})
        adapted["metadata"].update({
            "adapted_from": event.get("event_id", event.get("id")),
            "adaptation_reason": f"{move_names} movement contradicted active goals",
        })
        if adapted.get("trigger_at") is not None:
            adapted["trigger_at"] = float(world.time) + 1.0
        else:
            adapted["trigger_at"] = None
        adapted["trigger_time"] = adapted["trigger_at"]
        adapted["status"] = "pending"
        adapted["effects"] = adapted_effects
        return {
            "reason": (
                f"AI Director re-planned {event.get('event_id', event.get('id'))}: "
                + "; ".join(
                    f"{item['move']['npc'].name} is committed to {item['conflict']['goal']}"
                    for item in conflicting
                )
            ),
            "event": adapted,
            "moves": conflicting,
        }

    def _resolve_location(self, location):
        planner = getattr(self, "_planner", None)
        if planner is not None:
            resolved = planner.resolve_location(location)
            if resolved is not None:
                return resolved
        return default_location_coordinates(location)


class MockDirector(Director):
    """Offline deterministic director that always returns a legal Event."""

    def __init__(self):
        self.last_log = {}

    def generate_event(self, world, context=None):
        payload = build_world_context(world, context)
        effects = [
            {
                "type": "dialogue",
                "speaker_id": "player",
                "content": "MockDirector observed the world state.",
            }
        ]
        if world.npcs:
            effects.append(
                {
                    "type": "add_memory",
                    "npc_id": world.npcs[0].id,
                    "content": "MockDirector notes the current world state.",
                }
            )

        event = {
            "event_id": f"mock_event_{world.time:g}",
            "time": world.time,
            "effects": effects,
        }
        self.last_log = {
            "source": "MockDirector",
            "model": None,
            "input_summary": summarize_context(payload),
            "raw_json": json.dumps(event, ensure_ascii=False),
            "validator_result": "valid",
            "executed": False,
            "failure_reason": None,
        }
        return event


# Keep the fallback name requested by the project while reusing MockDirector.
RuleBasedDirector = MockDirector


class LLMDirector(Director):
    """Generate Event DSL through an OpenAI-compatible API, then validate."""

    def __init__(
        self,
        api_key=None,
        model=None,
        base_url=None,
        timeout=None,
        fallback_director=None,
    ):
        load_environment()
        self.api_key = os.getenv("LLM_API_KEY") if api_key is None else api_key
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.base_url = (
            base_url
            or os.getenv("LLM_BASE_URL")
            or DEFAULT_LLM_BASE_URL
        ).rstrip("/")
        self.timeout = timeout or DEFAULT_LLM_TIMEOUT
        self.fallback_director = fallback_director or MockDirector()
        self.used_fallback = False
        self.failure_reason = None
        self.last_log = {}
        self._last_opportunity_time = -1.0
        self._last_history_count = -1

    def generate_event(self, world, context=None):
        """Return a validated Event, falling back safely on any failure."""
        payload = build_world_context(world, context)
        input_summary = summarize_context(payload)
        validator = EventValidator()
        raw_json = None

        try:
            raw_text = self._request_event_text(payload)
            raw_json = raw_text[:500]
            event = _normalize_event_aliases(json.loads(raw_text))
            validator.validate(world, event)

            self.used_fallback = False
            self.failure_reason = None
            self.last_log = {
                "source": "LLM",
                "model": self.model,
                "input_summary": input_summary,
                "raw_json": raw_json,
                "validator_result": "valid",
                "executed": False,
                "failure_reason": None,
            }
            return event
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            fallback_event = self.fallback_director.generate_event(world, context)
            validator.validate(world, fallback_event)

            self.used_fallback = True
            self.failure_reason = reason
            self.last_log = {
                "source": "RuleBased",
                "model": self.model,
                "input_summary": input_summary,
                "raw_json": raw_json or json.dumps(fallback_event, ensure_ascii=False),
                "validator_result": "valid",
                "executed": False,
                "failure_reason": reason,
            }
            return fallback_event

    def check_opportunity(self, world):
        """Decide whether to insert one optional event, without forcing it."""
        if not self._should_check_opportunity(world):
            return {
                "should_trigger": False,
                "reason": "No meaningful change yet or still in cooldown.",
                "event": None,
            }

        payload = build_world_context(world, "opportunity_check")
        payload["conflict_context"] = self._conflict_context(world)
        try:
            raw_text = self._request_opportunity(payload)
            decision = json.loads(raw_text)
            should_trigger = bool(decision.get("should_trigger", False))
            if not should_trigger:
                return {
                    "should_trigger": False,
                    "reason": decision.get("reason", "The AI chose not to trigger."),
                    "event": None,
                }

            event = _normalize_event_aliases(decision.get("event"))
            EventValidator().validate(world, event)
            return {
                "should_trigger": True,
                "reason": decision.get("reason", "The AI decided to trigger."),
                "event": event,
            }
        except Exception as exc:
            return {
                "should_trigger": False,
                "reason": _safe_failure_reason(exc),
                "event": None,
            }
        finally:
            self._last_opportunity_time = world.time
            self._last_history_count = len(getattr(world, "history", []))

    def check_timeline_change(self, world):
        """Ask the LLM how player actions alter the predefined timeline."""
        payload = build_world_context(world, "timeline_change")
        try:
            raw_text = self._request_timeline_change(payload)
            proposal = json.loads(raw_text)
            validate_timeline_change(world, proposal)
            self.last_log = {
                "source": "LLM",
                "model": self.model,
                "input_summary": summarize_context(payload),
                "raw_json": raw_text[:500],
                "validator_result": "valid",
                "executed": False,
                "failure_reason": None,
            }
            return proposal
        except Exception as exc:
            self.last_log = {
                "source": "RuleBased",
                "model": self.model,
                "input_summary": summarize_context(payload),
                "raw_json": None,
                "validator_result": "invalid",
                "executed": False,
                "failure_reason": _safe_failure_reason(exc),
            }
            return None

    def _request_timeline_change(self, payload):
        return self._request_event_text(payload, system_prompt=LLM_TIMELINE_PROMPT)
    def _should_check_opportunity(self, world):
        if world.time - self._last_opportunity_time < 300:
            return False

        has_active_quest = any(
            quest.status == "active" for quest in getattr(world, "quests", [])
        )
        history_changed = (
            len(getattr(world, "history", [])) > self._last_history_count
        )
        nearby = world.nearby_npcs() if hasattr(world, "nearby_npcs") else []
        conflict_context = self._conflict_context(world)
        return bool(
            conflict_context and (has_active_quest or history_changed or nearby)
        )

    def _conflict_context(self, world):
        """Find NPC conflicts touched by current player context."""
        recent_history = list(getattr(world, "history", []))[-MAX_CONTEXT_ENTRIES:]
        active_quests = [
            quest
            for quest in getattr(world, "quests", [])
            if quest.status == "active"
        ]
        nearby_ids = {
            npc.id for npc in world.nearby_npcs()
        } if hasattr(world, "nearby_npcs") else set()

        contexts = []
        for npc in world.npcs:
            seed = npc.character_seed
            if not any(seed[field] for field in ("secret", "fear", "conflict")):
                continue

            reasons = []
            if npc.id in nearby_ids:
                reasons.append("npc_nearby")
            if any(quest.giver_id == npc.id for quest in active_quests):
                reasons.append("active_quest_giver")
            if any(entry.get("target") == npc.id for entry in recent_history):
                reasons.append("recent_history_target")
            if npc.beliefs or npc.goals:
                reasons.append("has_relevant_cognition")

            if reasons:
                contexts.append(
                    {
                        "npc_id": npc.id,
                        "reasons": reasons,
                        "conflict": seed["conflict"],
                    }
                )
        return contexts

    def _request_opportunity(self, payload):
        return self._request_event_text(payload, system_prompt=LLM_OPPORTUNITY_PROMPT)

    def _request_event_text(self, payload, system_prompt=LLM_SYSTEM_PROMPT):
        """Call the API and return the assistant's raw JSON text."""
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is not set")

        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"LLM API request failed with HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise RuntimeError("LLM API request failed") from None
        except json.JSONDecodeError:
            raise RuntimeError("LLM API returned invalid JSON") from None

        try:
            content = response_body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("LLM API returned an invalid response") from None

        if isinstance(content, dict):
            return json.dumps(content)
        if not isinstance(content, str):
            raise RuntimeError("LLM API returned non-text content")
        return content


def _normalize_event_aliases(event):
    """Normalize known LLM aliases to the canonical Event DSL names."""
    if not isinstance(event, dict):
        return event
    normalized = dict(event)
    effects = normalized.get("effects")
    if not isinstance(effects, list):
        return normalized
    aliases = {
        "add_belief": "set_belief",
        "update_belief": "set_belief",
        "adjust_relationship": "change_relationship",
        "add_relationship": "change_relationship",
    }
    normalized_effects = []
    for effect in effects:
        if isinstance(effect, dict):
            effect = dict(effect)
            effect["type"] = aliases.get(effect.get("type"), effect.get("type"))
        normalized_effects.append(effect)
    normalized["effects"] = normalized_effects
    return normalized

def _safe_failure_reason(error):
    """Return a safe diagnostic without exposing API keys or raw responses."""
    message = str(error).replace("\r", " ").replace("\n", " ").strip()
    lowered = message.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "sk-")):
        return "LLM API request failed"

    safe_prefixes = (
        "LLM_API_KEY is not set",
        "LLM API request failed",
        "LLM API returned",
        "LLM returned",
        "Event",
        "Unknown event",
        "Unknown effect",
        "Missing field",
        "NPC not found",
        "Item already exists",
        "Relationship target",
        "Dialogue speaker",
        "value must",
        "delta must",
        "x must",
        "y must",
        "time must",
        "event_id must",
        "npc_id must",
        "item_id must",
        "speaker_id must",
        "target_id must",
        "key must",
        "goal must",
        "content must",
        "item_type must",
    )
    if not message.startswith(safe_prefixes):
        message = "LLM API request failed"
    if len(message) > 160:
        message = message[:157] + "..."
    return message


LLM_NIGHT_PLAN_PROMPT = """You are the AI World Director running the nightly planning phase (22:00-24:00) for a small village simulation.
You receive a structured daily context: today's player actions/dialogue/memory, every NPC's goals/beliefs/relationships, the StoryTimeline and the world evidence.
Decide how the world should continue tomorrow. Return ONE JSON object with exactly these keys:
{"goal_changes": [], "story_events": [], "story_updates": [], "new_mysteries": [], "reasoning_summary": ""}
- goal_changes: [{"npc_id": "npc_2", "action": "add"|"remove", "goal": "investigate:mayor", "urgency": "normal"|"urgent"|"critical", "priority": 0.5, "reason": "..."}]
- story_events: an array of COMPLETE, executable StoryEvent objects (see the STRICT schema below). Optional.
- story_updates: [{"event_id": "...", "status": "...", "trigger_at": ...}] (optional)
- new_mysteries: [{"type": "...", "summary": "..."}] (optional)
- reasoning_summary: one short paragraph.

STORY_EVENT SCHEMA (STRICT). Every item of story_events is a structured event that goes straight into the StoryTimeline. It is NOT a summary for the reader. Each item MUST contain:
{
  "event_id": "unique_snake_case_id",
  "title": "short title",
  "description": "non-empty sentence describing what happens",
  "trigger_at": 12345.0,
  "conditions": [],
  "cancel_conditions": [],
  "effects": [{"type": "...", ...}]
}
Hard rules for story_events:
- "description" is REQUIRED and must be non-empty.
- "effects" is REQUIRED and must contain at least one effect.
- Effects MUST follow the EFFECT SCHEMA below exactly. Never write a natural-language summary in place of effects; never emit a summary-only event.
- To introduce a new character, use the create_npc effect exactly as specified below.
- To create a discoverable trace, use the add_evidence effect exactly as specified below.
- Do NOT include an "id" field; the program assigns ids.
- "trigger_at" must be a number greater than the current world time.

""" + condition_schema_text() + """

""" + effect_schema_text() + """

""" + goal_rules_text() + """

Only mark a goal urgent/critical when it must interrupt the current plan. story.deviations lists detected plot deviations (dead/missing NPCs, suspended/failed goals, blocked/overdue/execution-failed events, position mismatches); use story_updates to delay, modify or cancel the affected event, and story_events to create a replacement. You decide the plot rewrite; the program applies it. Keep the plan grounded in the given world state.

PLOT DRIFT (allowed and expected):
- Old storylines may end. If a prerequisite StoryEvent was cancelled, failed or replaced, its dependents may simply never happen; do NOT manufacture a replacement just to keep an old condition chain alive.
- You MAY keep, delay, modify, cancel, replace or branch a storyline, start an unrelated new one, or add nothing at all.
- Do not force the world back onto the original script.
- Prefer a small number of meaningful changes. If today already produced many important changes, it is fine to add fewer or no new events."""


LLM_STORY_EVENT_PROMPT = """You are the AI World Director for a small world simulation.

Your job is to propose exactly ONE future StoryEvent, expressed only through the
existing Event DSL. You do not rewrite the world, you do not control NPCs
step-by-step, and you do not invent effect types.

Return only one JSON object with this shape:
{
  "event_id": "generated_event_xxx",
  "title": "short title",
  "description": "what this future event is about",
  "trigger_at": 12345.0,
  "conditions": [{"type": "npc_at_location", "npc_id": "npc_1", "location": "forest"}],
  "cancel_conditions": [],
  "effects": [{"type": "add_goal", "npc_id": "npc_1", "goal": "search the mine"}],
  "priority": 0.0,
  "metadata": {"generated_by": "ai_director"}
}

Rules:
- trigger_at must be a number greater than the current world time.
- conditions and effects may use the flat form above or a nested
  {"type": "...", "parameters": {...}} form.
""" + condition_schema_text() + """

""" + effect_schema_text() + """

""" + goal_rules_text() + """

- add_evidence creates a discoverable trace. NPCs only find evidence near them;
  do not hand any NPC the evidence directly.
- Reference only NPC ids and item ids that already exist in the context, unless
  you intentionally spawn a brand new item with a new item_id.
- To make an NPC act, use add_goal; a separate NPC Planner decides the motion.
- Ground the event in the provided world state. Do not create random drama.
- Return at most one event, and no explanation."""


class DirectorProvider(ABC):
    """Turn a bounded world context into one raw StoryEvent proposal."""

    @abstractmethod
    def generate_story_event(self, context):
        """Return a dict (or JSON string) describing one StoryEvent."""
        raise NotImplementedError


class LLMDirectorProvider(DirectorProvider):
    """Production provider backed by an OpenAI-compatible chat completion API."""

    def __init__(self, api_key=None, model=None, base_url=None, timeout=None):
        load_environment()
        self.api_key = os.getenv("LLM_API_KEY") if api_key is None else api_key
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.base_url = (
            base_url or os.getenv("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL
        ).rstrip("/")
        self.timeout = timeout or DEFAULT_LLM_TIMEOUT
        self.last_raw = None

    def generate_story_event(self, context):
        raw = self._request_story_event(context)
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith("```json"):
                text = text[len("```json"):]
            elif text.startswith("```"):
                text = text[len("```"):]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        return raw

    def plan_next_day(self, context):
        """Ask the LLM for the nightly structured plan (same provider/agent)."""
        raw = self._request_night_plan(context)
        if isinstance(raw, str):
            text = raw.strip()
            if text.startswith("```json"):
                text = text[len("```json"):]
            elif text.startswith("```"):
                text = text[len("```"):]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        return raw

    def _request_night_plan(self, context):
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is not set")
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": LLM_NIGHT_PLAN_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "temperature": 0.6,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"LLM API request failed with HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise RuntimeError("LLM API request failed") from None
        except json.JSONDecodeError:
            raise RuntimeError("LLM API returned invalid JSON") from None
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("LLM API returned an invalid response") from None
        self.last_raw = content
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False)
        if not isinstance(content, str):
            raise RuntimeError("LLM API returned non-text content")
        return content

    def _request_story_event(self, context):
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is not set")
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": LLM_STORY_EVENT_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"LLM API request failed with HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise RuntimeError("LLM API request failed") from None
        except json.JSONDecodeError:
            raise RuntimeError("LLM API returned invalid JSON") from None
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("LLM API returned an invalid response") from None
        self.last_raw = content
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False)
        if not isinstance(content, str):
            raise RuntimeError("LLM API returned non-text content")
        return content


class FakeDirectorProvider(DirectorProvider):
    """Deterministic offline provider used by tests and demos."""

    def __init__(self, proposal=None, error=None):
        self.proposal = proposal
        self.error = error
        self.last_context = None
        self.calls = 0

    def generate_story_event(self, context):
        self.calls += 1
        self.last_context = context
        if self.error is not None:
            raise self.error
        return json.loads(json.dumps(self.proposal)) if self.proposal is not None else None




