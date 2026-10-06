"""Deterministic replay data for the lightweight visualization demo."""

import json
from pathlib import Path

from action import create_evidence, execute_parsed_action
from director import MockDirector
from player_action_parser import PlayerIntentError, parse_player_action_input
from event import execute_event
from main import create_demo_world
from story_engine import timeline_summary


EVENTS_FILE = Path(__file__).with_name("demo_events.json")


def _snapshot(world):
    return {
        "time": world.time,
        "player": {
            "position": [world.player.x, world.player.y],
            "inventory": list(world.player.inventory),
            "money": world.player.money,
            "energy": world.player.energy,
        },
        "npcs": [
            {
                "id": npc.id,
                "name": npc.name,
                "position": list(world.npc_position(npc.id)),
                "beliefs": dict(npc.beliefs),
                "relationships": dict(npc.relationships),
                "goals": list(npc.goals),
                "memory": [dict(entry) for entry in npc.memory[-5:]],
                "personality": list(npc.personality),
                "character_seed": dict(npc.character_seed),
            }
            for npc in world.npcs
        ],
        "items": [
            {
                "id": item.id,
                "type": item.type,
                "position": [item.x, item.y],
                "holder": item.holder,
            }
            for item in world.items
        ],
        "evidence": [dict(entry) for entry in world.evidence],
        "environment": json.loads(json.dumps(world.environment_state)),
        "timeline": timeline_summary(world),
        "history": [dict(entry) for entry in world.history[-10:]],
        "event_history": [dict(entry) for entry in world.event_history],
    }


class ReplayEngine:
    """Hold a deterministic sequence of visualization snapshots."""

    def __init__(self, director=None):
        self.steps = []
        self.index = 0
        self.director = director or MockDirector()
        self.reset()

    def reset(self):
        world = create_demo_world()
        self.world = world
        self.steps = []
        self.index = 0
        self._add_step(
            world,
            {
                "time": world.time,
                "type": "reset",
                "description": "Demo reset to the initial world.",
            },
        )

        old_x, old_y = world.player.x, world.player.y
        world.move(40.0, 40.0)
        self._add_step(
            world,
            {
                "time": world.time,
                "type": "player_action",
                "description": f"Player moved from ({old_x:g},{old_y:g}) to forest.",
            },
        )

        create_evidence(
            world,
            "footprint",
            "forest",
            "footprints heading north",
            target_belief="Ren fled north",
        )
        self._add_step(
            world,
            {
                "time": world.time,
                "type": "evidence_created",
                "description": "Player created fake footprint evidence.",
            },
        )

        ai_event = {
            "event_id": "replay_ai_footprint",
            "time": world.time,
            "effects": [
                {
                    "type": "add_goal",
                    "npc_id": "npc_2",
                    "goal": "investigate_suspicious_footprint",
                }
            ],
        }
        execute_event(world, ai_event)
        self._add_step(
            world,
            {
                "time": world.time,
                "type": "ai_director",
                "description": "AI Director generated: Ren investigates the suspicious footprint.",
            },
        )

        world.wait(20000)
        self._add_step(
            world,
            {
                "time": world.time,
                "type": "timeline",
                "description": "World time advanced and story timeline was checked.",
            },
        )
        self._write_events()

    def _add_step(self, world, event):
        self.steps.append({"event": event, "snapshot": _snapshot(world)})

    def current(self):
        return self.steps[self.index]

    def apply_input(self, text):
        """Parse, validate, execute, and visualize one interactive action."""
        before = _snapshot(self.world)
        try:
            parsed = parse_player_action_input(text, self.world)
        except (PlayerIntentError, ValueError) as exc:
            return {"success": False, "error": str(exc), "snapshot": before}

        success, message = execute_parsed_action(self.world, parsed, text)
        if not success:
            return {
                "success": False,
                "error": message,
                "parsed_action": parsed,
                "snapshot": before,
            }

        director_event = None
        if hasattr(self.director, "generate_event"):
            director_event = self.director.generate_event(
                self.world, context="interactive_action"
            )
            execute_event(self.world, director_event)

        self.steps.append(
            {
                "event": {
                    "time": self.world.time,
                    "type": "player_action",
                    "description": message,
                    "parsed_action": parsed,
                    "director_event": director_event,
                },
                "snapshot": _snapshot(self.world),
            }
        )
        self.index = len(self.steps) - 1
        self._write_events()
        return {
            "success": True,
            "message": message,
            "parsed_action": parsed,
            "director_event": director_event,
            "step": self.current(),
        }

    def next_event(self):
        if self.index < len(self.steps) - 1:
            self.index += 1
        return self.current()

    def run_all(self):
        self.index = len(self.steps) - 1
        return self.steps

    def _write_events(self):
        EVENTS_FILE.write_text(
            json.dumps([step["event"] for step in self.steps], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )