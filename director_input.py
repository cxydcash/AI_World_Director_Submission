"""Unified Director Input / GoalChange contract (Step 12.1).

The AI Director is the single decider of NPC goals. Every world signal that may
influence NPC behaviour is expressed as a ``DirectorInput``. The Director answers
with zero or more ``GoalChange`` objects, which GoalManager is the only code
allowed to translate into the Event DSL.

Only ``player_dialogue`` is wired in Step 12.1. The other sources are declared so
future inputs (PlayerAction / NPCObservation / NPCDiscovery / WorldEvent) can be
added without rewriting the Director or GoalManager.
"""

from dataclasses import dataclass, field


# -- Input sources -----------------------------------------------------------
SOURCE_PLAYER_DIALOGUE = "player_dialogue"
SOURCE_PLAYER_ACTION = "player_action"
SOURCE_NPC_OBSERVATION = "npc_observation"
SOURCE_NPC_DISCOVERY = "npc_discovery"
SOURCE_WORLD_EVENT = "world_event"

DIRECTOR_INPUT_SOURCES = frozenset({
    SOURCE_PLAYER_DIALOGUE,
    SOURCE_PLAYER_ACTION,
    SOURCE_NPC_OBSERVATION,
    SOURCE_NPC_DISCOVERY,
    SOURCE_WORLD_EVENT,
})

# -- Goal change kinds -------------------------------------------------------
GOAL_CHANGE_NONE = "none"
GOAL_CHANGE_ADD = "add"
GOAL_CHANGE_REMOVE = "remove"
GOAL_CHANGE_MODIFY = "modify"

GOAL_CHANGE_TYPES = frozenset({
    GOAL_CHANGE_NONE,
    GOAL_CHANGE_ADD,
    GOAL_CHANGE_REMOVE,
    GOAL_CHANGE_MODIFY,
})

FOLLOW_PLAYER_GOAL = "follow_player"

# -- Goal urgency (Step 12.3) ------------------------------------------------
# Only an explicitly urgent/critical goal may interrupt the current active
# goal. Priority is a scheduling hint; urgency is a preemption decision.
URGENCY_NORMAL = "normal"
URGENCY_URGENT = "urgent"
URGENCY_CRITICAL = "critical"
URGENCY_LEVELS = (URGENCY_NORMAL, URGENCY_URGENT, URGENCY_CRITICAL)
URGENCY_RANK = {URGENCY_NORMAL: 0, URGENCY_URGENT: 1, URGENCY_CRITICAL: 2}


def normalize_urgency(value):
    text = str(value or "").strip().lower()
    return text if text in URGENCY_RANK else URGENCY_NORMAL


def split_goal_string(text):
    """Inverse of encode_goal: 'lead_player_to:home' -> ('lead_player_to', 'home')."""
    text = str(text or "").strip()
    if not text:
        return "", None
    if text == "follow player":
        return FOLLOW_PLAYER_GOAL, "player"
    if text == "wait":
        return "wait", None
    if ":" in text:
        prefix, _, target = text.partition(":")
        return prefix.strip(), (target.strip() or None)
    return text, None


def encode_goal(goal_type, target=None):
    """Canonical string form stored in ``npc.goals``.

    This is the only place the goal string shape is decided, so the Director,
    GoalManager and the legacy decision module all agree.
    """
    goal_type = str(goal_type or "").strip()
    if goal_type == FOLLOW_PLAYER_GOAL:
        return "follow player"
    if goal_type == "wait":
        return "wait"
    target = "" if target is None else str(target).strip()
    if not target:
        return goal_type
    return f"{goal_type}:{target}"


@dataclass
class DirectorInput:
    """One world signal offered to the AI Director.

    ``source`` selects how the Director interprets the rest of the fields.
    ``context`` carries the world snapshot the Director may use to decide; it
    must never be mutated by the Director.
    """

    source: str
    actor_id: str = "player"
    target_id: str | None = None
    text: str = ""
    payload: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    time: float = 0.0

    def to_dict(self):
        return {
            "source": self.source,
            "actor_id": self.actor_id,
            "target_id": self.target_id,
            "text": self.text,
            "payload": dict(self.payload),
            "context": dict(self.context),
            "time": self.time,
        }

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        return cls(
            source=str(data.get("source", "")),
            actor_id=str(data.get("actor_id", "player")),
            target_id=data.get("target_id"),
            text=str(data.get("text", "")),
            payload=dict(data.get("payload", {})),
            context=dict(data.get("context", {})),
            time=float(data.get("time", 0.0) or 0.0),
        )


@dataclass
class GoalChange:
    """The AI Director decision about one NPC goal.

    ``change_type`` is one of none/add/remove/modify. A ``none`` change is a
    real decision ("do not change the world") and may still carry commitment
    metadata so observation layers can report a promise that was not acted on.
    """

    change_type: str = GOAL_CHANGE_NONE
    npc_id: str | None = None
    goal_type: str | None = None
    target: str | None = None
    goal_string: str | None = None
    priority: float | None = None
    urgency: str = URGENCY_NORMAL
    commitment: str = "immediate"
    confidence: float = 1.0
    reason: str = ""
    source: str = "ai_director"
    payload: dict = field(default_factory=dict)

    @property
    def is_change(self):
        return self.change_type in {GOAL_CHANGE_ADD, GOAL_CHANGE_REMOVE, GOAL_CHANGE_MODIFY}

    def resolved_goal_string(self):
        if self.goal_string:
            return str(self.goal_string)
        return encode_goal(self.goal_type, self.target)

    def to_dict(self):
        return {
            "change_type": self.change_type,
            "npc_id": self.npc_id,
            "goal_type": self.goal_type,
            "target": self.target,
            "goal_string": self.resolved_goal_string(),
            "priority": self.priority,
            "urgency": normalize_urgency(self.urgency),
            "commitment": self.commitment,
            "confidence": self.confidence,
            "reason": self.reason,
            "source": self.source,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        return cls(
            change_type=str(data.get("change_type", GOAL_CHANGE_NONE)),
            npc_id=data.get("npc_id"),
            goal_type=data.get("goal_type"),
            target=data.get("target"),
            goal_string=data.get("goal_string"),
            priority=data.get("priority"),
            urgency=normalize_urgency(data.get("urgency")),
            commitment=str(data.get("commitment", "immediate")),
            confidence=float(data.get("confidence", 1.0) or 0.0),
            reason=str(data.get("reason", "")),
            source=str(data.get("source", "ai_director")),
            payload=dict(data.get("payload", {})),
        )
