"""Minimal quest state with accept, complete, and fail transitions."""

from dataclasses import dataclass


QUEST_STATUSES = {"available", "active", "completed", "failed"}


@dataclass
class Quest:
    quest_id: str
    title: str
    giver_id: str
    description: str
    target: str
    reward: float
    status: str = "available"

    def __post_init__(self):
        if self.status not in QUEST_STATUSES:
            raise ValueError(f"Unknown quest status: {self.status}.")

    def accept(self):
        if self.status != "available":
            return False
        self.status = "active"
        return True

    def complete(self):
        if self.status != "active":
            return False
        self.status = "completed"
        return True

    def fail(self):
        if self.status != "active":
            return False
        self.status = "failed"
        return True

    def to_dict(self):
        return {
            "quest_id": self.quest_id,
            "title": self.title,
            "giver_id": self.giver_id,
            "description": self.description,
            "target": self.target,
            "reward": self.reward,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            quest_id=str(data["quest_id"]),
            title=str(data["title"]),
            giver_id=str(data["giver_id"]),
            description=str(data["description"]),
            target=str(data["target"]),
            reward=float(data["reward"]),
            status=str(data.get("status", "available")),
        )