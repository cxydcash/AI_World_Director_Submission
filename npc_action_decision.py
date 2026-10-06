"""AI NPC Action Decision layer (Step 8, phase 1).

Player natural language -> bounded context -> provider (LLM / Fake) -> strict
schema validation -> a structured NPC Goal proposal.

This module never mutates WorldState: it only proposes what this NPC should
do next. Committing happens through the existing Event DSL (add_goal), after
which NPCPlanner turns the goal into an Action.

Not every dialogue produces a goal: casual chat returns no goal at all.
"""

import copy
import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from conversation_memory import ConversationMemoryManager
from director import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, DEFAULT_LLM_TIMEOUT, load_environment
from event import execute_event
from world import EXPLORE_LOCATIONS
from world_objects import WorldObjectRegistry


GOAL_FOLLOW_PLAYER = "follow_player"
GOAL_LEAD_PLAYER_TO = "lead_player_to"
GOAL_GO_TO = "go_to"
GOAL_INVESTIGATE = "investigate"
GOAL_TALK_TO = "talk_to"
GOAL_WAIT = "wait"

NPC_GOAL_TYPES = frozenset({
    GOAL_FOLLOW_PLAYER,
    GOAL_LEAD_PLAYER_TO,
    GOAL_GO_TO,
    GOAL_INVESTIGATE,
    GOAL_TALK_TO,
    GOAL_WAIT,
})

# goals that must name a target
TARGET_REQUIRED = frozenset({GOAL_LEAD_PLAYER_TO, GOAL_GO_TO, GOAL_INVESTIGATE, GOAL_TALK_TO})

# 语言行动的"兑现方式"：NPC 可以立刻去做、稍后再去（可能忘）、
# 或者只是口头答应而根本不打算动身。
COMMITMENT_IMMEDIATE = "immediate"
COMMITMENT_LATER = "later"
COMMITMENT_DECEIVE = "deceive"
COMMITMENT_DECLINE = "decline"
COMMITMENTS = frozenset({COMMITMENT_IMMEDIATE, COMMITMENT_LATER, COMMITMENT_DECEIVE, COMMITMENT_DECLINE})

DEFAULT_CONFIDENCE_THRESHOLD = 0.4
MAX_CONTEXT_MESSAGES = 8
MAX_CONTEXT_MEMORY = 5
MAX_CONTEXT_ENTRIES = 6


class NPCGoalDecisionError(ValueError):
    """Raised when a provider proposal is malformed or unsafe."""


def _clean(value):
    return str(value or "").strip()


def _valid_target(value):
    return bool(re.fullmatch(r"[\w\- ]{1,64}", _clean(value), re.UNICODE))


def _clean_history(history, limit=MAX_CONTEXT_MESSAGES):
    view = []
    for message in (history or [])[-max(1, int(limit)):]:
        if not isinstance(message, dict):
            continue
        text = _clean(message.get("text"))
        if not text:
            continue
        view.append({"speaker": _clean(message.get("speaker")), "text": text})
    return view


def _nearby_objects(world, location):
    if not location:
        return []
    registry = WorldObjectRegistry()
    out = []
    for obj in registry.objects_for_location(location):
        out.append({
            "id": obj.get("id"),
            "location": location,
            "description": obj.get("description", ""),
        })
    return out[:MAX_CONTEXT_ENTRIES]


def build_npc_decision_context(world, npc_id, message):
    """Bounded, read-only context for the NPC action decision."""
    npc = world.get_npc(npc_id)
    if npc is None:
        raise ValueError(f"NPC not found: {npc_id}.")
    manager = ConversationMemoryManager()
    position = world.npc_position(npc_id)
    player_position = (world.player.x, world.player.y)
    npc_location = world.npc_location(npc_id)

    nearby_places = [
        {"id": name, "position": list(coords)}
        for name, coords in EXPLORE_LOCATIONS.items()
    ]
    nearby_npcs = [
        {
            "id": other.id,
            "name": other.name,
            "distance": round(world.distance_between(position, world.npc_position(other.id)), 2),
        }
        for other in world.npcs
        if other.id != npc_id
    ]
    nearby_npcs.sort(key=lambda entry: entry["distance"])
    timeline = [
        {"id": event.get("event_id"), "status": event.get("status")}
        for event in getattr(world, "story_timeline", [])
    ]
    return {
        "npc": {
            "id": npc.id,
            "name": npc.name,
            "role": npc.role,
            "personality": list(npc.personality),
            "location": npc_location,
            "position": list(position),
            "goals": list(npc.goals),
            "beliefs": dict(npc.beliefs),
            "memory": [
                _clean(record.get("content", record.get("observed", "")))
                for record in npc.memory[-MAX_CONTEXT_MEMORY:]
                if isinstance(record, dict)
            ],
        },
        "player": {
            "position": list(player_position),
            "distance_to_npc": round(world.distance_between(position, player_position), 2),
            "message": _clean(message),
        },
        "world": {"time": world.time, "places": nearby_places},
        "nearby_npcs": nearby_npcs[:MAX_CONTEXT_ENTRIES],
        "nearby_objects": _nearby_objects(world, npc_location),
        "conversation_history": _clean_history(manager.peek_history(npc, limit=MAX_CONTEXT_MESSAGES)),
        "timeline": timeline[:MAX_CONTEXT_ENTRIES],
    }


def validate_npc_goal(proposal, context=None):
    """Validate a raw provider proposal, returning a goal or None (no action)."""
    if proposal is None:
        return None
    if not isinstance(proposal, dict):
        raise NPCGoalDecisionError("goal proposal must be an object")
    if proposal.get("action_required") is False or proposal.get("action") is False:
        return None

    goal_type = _clean(proposal.get("goal_type", proposal.get("action")))
    if goal_type not in NPC_GOAL_TYPES:
        raise NPCGoalDecisionError(f"unsupported goal_type: {goal_type}")

    target = _clean(proposal.get("target", proposal.get("target_id")))
    if goal_type == GOAL_FOLLOW_PLAYER:
        target = target or "player"
        if target != "player":
            raise NPCGoalDecisionError("follow_player target must be player")
    elif goal_type == GOAL_WAIT:
        target = target or None
    else:
        if not target:
            raise NPCGoalDecisionError(f"{goal_type} requires a target")
        if not _valid_target(target):
            raise NPCGoalDecisionError(f"invalid target: {target}")

    confidence = proposal.get("confidence", 1.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < DEFAULT_CONFIDENCE_THRESHOLD:
        return None

    commitment = _clean(proposal.get("commitment")).lower() or COMMITMENT_IMMEDIATE
    if commitment not in COMMITMENTS:
        # 不认识的兑现方式按"立刻去做"处理，避免因为措辞被拒
        commitment = COMMITMENT_IMMEDIATE

    from director_input import normalize_urgency

    return {
        "goal_type": goal_type,
        "target": target,
        "reason": _clean(proposal.get("reason")),
        "confidence": confidence,
        "commitment": commitment,
        # Optional goal urgency (Step 12.3). default normal; only urgent/
        # critical may interrupt the current active goal.
        "urgency": normalize_urgency(proposal.get("urgency")),
    }


def encode_goal_string(goal):
    """Encode a validated goal into the string form stored in npc.goals.

    Delegates to director_input.encode_goal so the canonical goal-string shape
    lives in exactly one place.
    """
    from director_input import encode_goal
    return encode_goal(goal.get("goal_type"), goal.get("target"))


def apply_npc_goal(world, npc_id, goal, source="npc_action_decision"):
    """Backwards-compatible commit helper.

    Step 12.1 moved goal writing to GoalManager. This wrapper is kept only so
    older callers/tests keep working; it routes through GoalManager so there is
    still exactly one commit path (Director -> GoalManager -> Event DSL).
    """
    from director_input import GOAL_CHANGE_ADD, GoalChange
    from goal_manager import GoalManager

    change = GoalChange(
        change_type=GOAL_CHANGE_ADD,
        npc_id=npc_id,
        goal_type=goal.get("goal_type"),
        target=goal.get("target"),
        goal_string=encode_goal_string(goal),
        commitment=str(goal.get("commitment") or "immediate"),
        urgency=str(goal.get("urgency") or "normal"),
        reason=str(goal.get("reason") or ""),
        source=source,
    )
    result = GoalManager().apply(world, change)
    return result.get("goal_string") or change.resolved_goal_string()


def decide_npc_action(world, npc_id, message, provider=None):
    """Ask the provider what this NPC should do; never mutates WorldState."""
    result = {
        "action_required": False,
        "goal": None,
        "reason": "",
        "raw": None,
        "error": None,
    }
    if provider is None:
        result["reason"] = "no_provider"
        return result

    context = build_npc_decision_context(world, npc_id, message)
    try:
        raw = provider.decide_npc_goal(context)
    except Exception as exc:
        result["reason"] = f"provider_error: {type(exc).__name__}"
        result["error"] = str(exc)
        return result
    result["raw"] = raw

    try:
        goal = validate_npc_goal(raw, context)
    except NPCGoalDecisionError as exc:
        result["reason"] = f"rejected: {exc}"
        result["error"] = str(exc)
        return result

    if goal is None:
        result["reason"] = "no_action"
        return result

    result["action_required"] = True
    result["goal"] = goal
    result["commitment"] = goal.get("commitment", COMMITMENT_IMMEDIATE)
    result["reason"] = goal["reason"] or "npc goal proposed"
    return result


class NPCGoalProvider(ABC):
    """Turn a bounded context into a raw NPC goal proposal."""

    @abstractmethod
    def decide_npc_goal(self, context):
        """Return a dict (or None) describing what the NPC should do."""
        raise NotImplementedError


class FakeNPCGoalProvider(NPCGoalProvider):
    """Deterministic offline provider used by tests and demos.

    decision may be a fixed dict/None, or a callable that receives the
    context and returns a dict/None (scripted per message for tests).
    """

    def __init__(self, decision=None, error=None):
        self.decision = decision
        self.error = error
        self.last_context = None
        self.calls = 0

    def decide_npc_goal(self, context):
        self.calls += 1
        self.last_context = context
        if self.error is not None:
            raise self.error
        if callable(self.decision):
            return copy.deepcopy(self.decision(context))
        return copy.deepcopy(self.decision)


LLM_NPC_GOAL_PROMPT = """You decide what ONE NPC should do next from the current dialogue and world state.

You do NOT change the world and you do NOT move the NPC yourself. You only
return a structured goal proposal, or decide that no action is needed.

Allowed goal_type values:
- follow_player : the player explicitly told this NPC to follow them.
- lead_player_to : the player wants this NPC to take them somewhere.
- go_to : this NPC should go somewhere on its own.
- investigate : this NPC should look into an object / person / event.
- talk_to : this NPC should go and talk to someone.
- wait : this NPC should wait.

Return ONLY one JSON object:
{"action_required": false, "reason": "..."}
when the player is only chatting or asking a question (weather small talk,
or a question about the ledger). NEVER invent an action for small talk, and
never return a wait goal just to fill space.

When an action is clearly requested or strongly implied, return:
{"action_required": true, "goal_type": "lead_player_to", "target": "mayor_house",
 "reason": "player asked me to take them there", "confidence": 0.9, "commitment": "immediate"}

Commitment (how the NPC actually honours what it says):
- "immediate" : this NPC really starts doing it now (default).
- "later"     : the NPC agrees but only intends to go later; it may forget. The goal
                is still written, and the world will show whether it ever moves.
- "deceive"   : the NPC answers politely but has no intention of going. Do NOT write
                a goal for this; the promise simply never happens.
- "decline"   : the NPC refuses; no goal is written.
Choose the commitment from this NPC's character_seed, beliefs, goals and the
conversation. A calculating or hostile NPC may deceive; a busy NPC may say later.

Rules:
- Asking the NPC to take the player somewhere -> lead_player_to with that place.
- If the NPC already planned to go somewhere and the player joins -> lead_player_to.
- An explicit order to follow the player -> follow_player (target player).
- Asking the NPC to follow the player with a question (ending in ma / ?) is NOT a
  command: return action_required=false. A request or offer to come along
  ("can I come with you") is NOT follow_player either.
- Telling the NPC to go look at something -> go_to or investigate.
- If the context is not enough to be sure, return action_required=false.
- Use target ids from the provided context when possible (places, npc ids, objects).
- If npc.goals already contains that goal, do not repeat it: small talk or a short
  acknowledgement must return action_required=false.
- Return at most one goal and no explanation outside the JSON."""


class LLMNPCGoalProvider(NPCGoalProvider):
    """Production provider backed by an OpenAI-compatible chat completion API."""

    def __init__(self, api_key=None, model=None, base_url=None, timeout=None):
        load_environment()
        self.api_key = os.getenv("LLM_API_KEY") if api_key is None else api_key
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL).rstrip("/")
        self.timeout = timeout or DEFAULT_LLM_TIMEOUT
        self.last_raw = None

    def decide_npc_goal(self, context):
        raw = self._request_decision(context)
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

    def _request_decision(self, context):
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is not set")
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": LLM_NPC_GOAL_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "temperature": 0.2,
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
