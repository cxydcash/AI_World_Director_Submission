"""Entity definitions for the v2 world simulation."""

import math
from dataclasses import dataclass, field
from typing import Optional

from schedule import default_schedule, normalize_schedule
from director_input import URGENCY_RANK, normalize_urgency


@dataclass
class Player:
    """Player position and inventory of item IDs."""

    x: float = 0.0
    y: float = 0.0
    inventory: list[str] = field(default_factory=list)
    money: float = 100.0
    energy: float = 100.0
    strength: float = 0.0
    fitness: float = 0.0
    escape_probability: float = 0.33
    memory: list[dict] = field(default_factory=list)

    def work(self):
        """Apply the numeric effect of one work action."""
        self.money = float(self.money) + 80.0
        self.energy = max(0.0, float(self.energy) - 30.0)

    def sleep(self):
        """Restore energy up to 100."""
        self.energy = 100.0

    def to_dict(self):
        """Return a JSON-friendly player state."""
        return {
            "x": self.x,
            "y": self.y,
            "inventory": list(self.inventory),
            "money": self.money,
            "energy": self.energy,
            "strength": self.strength,
            "fitness": self.fitness,
            "escape_probability": self.escape_probability,
            "memory": [dict(entry) for entry in self.memory],
        }

    @classmethod
    def from_dict(cls, data):
        """Build a player from saved JSON data."""
        return cls(
            x=float(data["x"]),
            y=float(data["y"]),
            inventory=[str(item_id) for item_id in data.get("inventory", [])],
            money=float(data.get("money", 100.0)),
            energy=float(data.get("energy", 100.0)),
            strength=float(data.get("strength", 0.0)),
            fitness=float(data.get("fitness", 0.0)),
            escape_probability=float(data.get("escape_probability", 0.33)),
            memory=[dict(entry) for entry in data.get("memory", [])],
        )


@dataclass
class NPC:
    """NPC schedule, local vision, memory, and simple cognitive state."""

    id: str
    name: str
    x: float
    y: float
    role: str = ""
    personality: list[str] = field(default_factory=list)
    background: str = ""
    vision_range: float = 5.0
    memory: list[dict] = field(default_factory=list)
    schedule: list[dict] = field(default_factory=default_schedule)
    beliefs: dict = field(default_factory=dict)
    relationships: dict = field(default_factory=dict)
    goals: list[str] = field(default_factory=list)
    character_seed: dict = field(default_factory=dict)
    position_override: Optional[list[float]] = None
    current_action: dict = field(default_factory=dict)
    action_progress: dict = field(default_factory=dict)
    conversation_memory: dict = field(default_factory=dict)
    goal_states: dict = field(default_factory=dict)
    # Step 12.3: explicit currently-executing goal + suspended stack. These are
    # an extension of goal_states, not a second goal system.
    active_goal: Optional[str] = None
    suspended_goal_stack: list = field(default_factory=list)

    def __post_init__(self):
        self.schedule = normalize_schedule(self.schedule)
        self.memory = list(self.memory)
        self.role = _validate_optional_string(self.role, "role")
        self.personality = _normalize_personality(self.personality)
        self.background = _validate_optional_string(self.background, "background")
        self.beliefs = _normalize_probability_map(self.beliefs, "belief")
        self.relationships = _normalize_probability_map(
            self.relationships, "relationship"
        )
        self.goals = _normalize_goals(self.goals)
        self.character_seed = _normalize_character_seed(self.character_seed)
        self.position_override = _normalize_position_override(self.position_override)
        self.conversation_memory = _normalize_conversation_memory(self.conversation_memory)
        self.goal_states = _normalize_goal_states(self.goal_states)
        self.active_goal = _normalize_active_goal(self.active_goal, self.goals)
        self.suspended_goal_stack = _normalize_suspended_goal_stack(self.suspended_goal_stack)

    def to_dict(self):
        """Return a JSON-friendly NPC state."""
        return {
            "id": self.id,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "role": self.role,
            "personality": list(self.personality),
            "background": self.background,
            "vision_range": self.vision_range,
            "memory": [dict(entry) for entry in self.memory],
            "schedule": [dict(entry) for entry in self.schedule],
            "beliefs": dict(self.beliefs),
            "relationships": dict(self.relationships),
            "goals": list(self.goals),
            "character_seed": {
                "personality": list(self.character_seed["personality"]),
                "long_term_goal": self.character_seed["long_term_goal"],
                "secret": self.character_seed["secret"],
                "fear": self.character_seed["fear"],
                "conflict": self.character_seed["conflict"],
            },
            "position_override": (
                list(self.position_override)
                if self.position_override is not None
                else None
            ),
            "current_action": dict(self.current_action),
            "action_progress": dict(self.action_progress),
            "conversation_memory": _normalize_conversation_memory(self.conversation_memory),
            "goal_states": _normalize_goal_states(self.goal_states),
            "active_goal": self.active_goal,
            "suspended_goal_stack": [
                dict(entry) for entry in _normalize_suspended_goal_stack(self.suspended_goal_stack)
            ],
        }

    @classmethod
    def from_dict(cls, data):
        """Build an NPC from saved JSON data."""
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            x=float(data["x"]),
            y=float(data["y"]),
            role=str(data.get("role", "")),
            personality=[str(item) for item in data.get("personality", [])],
            background=str(data.get("background", "")),
            vision_range=float(data.get("vision_range", 5.0)),
            memory=[dict(entry) for entry in data.get("memory", [])],
            schedule=normalize_schedule(data.get("schedule")),
            beliefs=dict(data.get("beliefs", {})),
            relationships=dict(data.get("relationships", {})),
            goals=[str(goal) for goal in data.get("goals", [])],
            character_seed=data.get("character_seed", {}),
            position_override=data.get("position_override"),
            current_action=dict(data.get("current_action", {})),
            action_progress=dict(data.get("action_progress", {})),
            conversation_memory=data.get("conversation_memory", {}),
            goal_states=data.get("goal_states", {}),
            active_goal=data.get("active_goal"),
            suspended_goal_stack=list(data.get("suspended_goal_stack", []) or []),
        )

    def add_memory(self, record):
        """Append one validated observation record to memory."""
        if not isinstance(record, dict) or not record:
            raise ValueError("Memory record must be a non-empty object.")
        self.memory.append(dict(record))
        return self.memory[-1]

    def set_belief(self, key, value):
        """Set one belief to a probability in the 0..1 range."""
        key = _validate_key(key, "belief")
        value = _validate_probability(value, "belief")
        self.beliefs[key] = value
        return value

    def change_relationship(self, name, delta):
        """Adjust a relationship value and clamp it to the 0..1 range."""
        name = _validate_key(name, "relationship")
        delta = _validate_number(delta, "relationship delta")
        current = float(self.relationships.get(name, 0.0))
        updated = max(0.0, min(1.0, current + delta))
        self.relationships[name] = updated
        return updated

    def add_goal(self, goal):
        """Add a unique goal, returning True only when it was new."""
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Goal must be a non-empty string.")
        if goal in self.goals:
            return False
        self.goals.append(goal)
        return True

    def remove_goal(self, goal):
        """Remove a goal, returning True only when it existed."""
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Goal must be a non-empty string.")
        if goal not in self.goals:
            return False
        self.goals.remove(goal)
        return True


@dataclass
class Item:
    """World item position and optional holder."""

    id: str
    type: str
    x: float
    y: float
    holder: Optional[str] = None

    def to_dict(self):
        """Return a JSON-friendly item state."""
        return {
            "id": self.id,
            "type": self.type,
            "x": self.x,
            "y": self.y,
            "holder": self.holder,
        }

    @classmethod
    def from_dict(cls, data):
        """Build an item from saved JSON data."""
        holder = data.get("holder")
        return cls(
            id=str(data["id"]),
            type=str(data["type"]),
            x=float(data["x"]),
            y=float(data["y"]),
            holder=None if holder is None else str(holder),
        )


def _validate_optional_string(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string.")
    return value


# Goal lifecycle statuses (Step 12.3). "pending" is a queued goal waiting for
# the current active goal; "suspended" was interrupted and can be resumed.
GOAL_STATUSES = frozenset(
    {"active", "pending", "suspended", "completed", "failed", "cancelled"}
)


def _normalize_goal_states(value):
    """Return {goal: {status, attempts, reason, ...}} for the goal lifecycle."""
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for key, entry in value.items():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status", "active"))
        if status not in GOAL_STATUSES:
            status = "active"
        try:
            attempts = int(entry.get("attempts", 0) or 0)
        except (TypeError, ValueError):
            attempts = 0
        normalized_entry = {
            "status": status,
            "attempts": attempts,
            "reason": str(entry.get("reason", "")),
        }
        # Which layer queued this goal (e.g. "ai_director" for AI StoryEvents).
        # Kept optional so older saves and plain goals stay valid.
        for field in ("source", "interrupted_by", "interrupt_reason"):
            item = entry.get(field)
            if isinstance(item, str) and item:
                normalized_entry[field] = item
        urgency = entry.get("urgency")
        if isinstance(urgency, str) and urgency in URGENCY_RANK:
            normalized_entry["urgency"] = urgency
        for field in ("priority", "interrupted_at", "resumed_at", "activated_at"):
            item = entry.get(field)
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                normalized_entry[field] = float(item)
        normalized[str(key)] = normalized_entry
    return normalized


def _normalize_active_goal(value, goals):
    """The explicit active goal must reference a goal the NPC still has."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return value if value in (goals or []) else None


def _normalize_suspended_goal_stack(value):
    """Return a safe copy of the suspended-goal stack (old saves default [])."""
    if not isinstance(value, list):
        return []
    normalized = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        goal = str(entry.get("goal", "")).strip()
        if not goal:
            continue
        record = {
            "goal": goal,
            "reason": str(entry.get("reason", "")),
            "interrupted_by": str(entry.get("interrupted_by", "")),
        }
        urgency = entry.get("urgency")
        if isinstance(urgency, str) and urgency in URGENCY_RANK:
            record["urgency"] = urgency
        source = entry.get("source")
        if isinstance(source, str) and source:
            record["source"] = source
        for field in ("priority", "suspended_at"):
            item = entry.get(field)
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                record[field] = float(item)
        action = entry.get("action")
        if isinstance(action, dict):
            record["action"] = dict(action)
        progress = entry.get("progress")
        if isinstance(progress, dict):
            record["progress"] = dict(progress)
        normalized.append(record)
    return normalized


def _normalize_conversation_memory(value):
    """Return a safe copy of per-NPC conversation memory (old saves default empty)."""
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for partner, entry in value.items():
        if not isinstance(entry, dict):
            continue
        normalized[str(partner)] = {
            "messages": [
                dict(message) for message in entry.get("messages", []) if isinstance(message, dict)
            ],
            "known_facts": [str(item) for item in entry.get("known_facts", [])],
            "unresolved_questions": [str(item) for item in entry.get("unresolved_questions", [])],
            "recent_topics": [str(item) for item in entry.get("recent_topics", [])],
            "player_claims": [str(item) for item in entry.get("player_claims", [])],
            "last_turn": int(entry.get("last_turn", 0) or 0),
        }
    return normalized


def _normalize_personality(personality):
    if not isinstance(personality, list):
        raise ValueError("NPC personality must be a list of strings.")
    normalized = []
    for item in personality:
        normalized.append(_validate_key(item, "personality"))
    return normalized

def _validate_key(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} name must be a non-empty string.")
    return value


def _validate_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite.")
    return number


def _validate_probability(value, label):
    number = _validate_number(value, label)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{label} must be between 0 and 1.")
    return number


def _normalize_probability_map(mapping, label):
    if not isinstance(mapping, dict):
        raise ValueError(f"{label}s must be an object.")
    normalized = {}
    for key, value in mapping.items():
        validated_key = _validate_key(key, label)
        normalized[validated_key] = _validate_probability(value, label)
    return normalized


def _normalize_character_seed(seed):
    default = {
        "personality": [],
        "long_term_goal": "",
        "secret": "",
        "fear": "",
        "conflict": "",
    }
    if seed is None:
        return default
    if not isinstance(seed, dict):
        raise ValueError("NPC character_seed must be an object.")
    allowed = set(default)
    unknown = set(seed) - allowed
    if unknown:
        raise ValueError(f"Unknown character_seed fields: {sorted(unknown)}.")
    default["personality"] = _normalize_personality(
        seed.get("personality", [])
    )
    for field in ("long_term_goal", "secret", "fear", "conflict"):
        value = seed.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"character_seed.{field} must be a string.")
        default[field] = value
    return default

def _normalize_position_override(position_override):
    if position_override is None:
        return None
    if not isinstance(position_override, (list, tuple)) or len(position_override) != 2:
        raise ValueError("position_override must be [x, y] or null.")
    x = _validate_number(position_override[0], "position_override x")
    y = _validate_number(position_override[1], "position_override y")
    return [x, y]

def _normalize_goals(goals):
    if not isinstance(goals, list):
        raise ValueError("NPC goals must be a list.")
    normalized = []
    for goal in goals:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Each goal must be a non-empty string.")
        if goal not in normalized:
            normalized.append(goal)
    return normalized
