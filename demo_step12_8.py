# -*- coding: utf-8 -*-
"""Step 12.8 — minimal two-route playable demo (offline, deterministic).

Route A: explore -> discover a clue -> talk / help Ren -> Night Director re-plans.
Route B: ignore Ren and the clue -> keep exploring -> Night Director re-plans
         from a different world state.

Both routes use the real OpenWorldEngine. The Night provider is a deterministic
stand-in for the LLM so the demo runs without a network.
"""

import os

os.environ.pop("LLM_API_KEY", None)

from open_world import OpenWorldEngine


class RouteNightProvider:
    """Deterministic stand-in that reacts to the world state in the context."""

    def __init__(self):
        self.context = None
        self.route = None

    def plan_next_day(self, context):
        self.context = context
        evidence = context["world"]["evidence"]
        if evidence:
            self.route = "cooperative"
            return {
                "goal_changes": [{"npc_id": "npc_4", "action": "add",
                                  "goal": "investigate:mayor", "urgency": "normal",
                                  "reason": "a clue surfaced today"}],
                "story_events": [{
                    "event_id": "ledger_recovery", "title": "Ledger recovery",
                    "description": "the village follows the new clue",
                    "trigger_at": context["time"]["night_start"] + 3 * 3600,
                    "effects": [{"type": "set_belief", "npc_id": "npc_4",
                                 "key": "clue_found", "value": 0.7}],
                }],
                "story_updates": [],
                "new_mysteries": [],
                "reasoning_summary": "evidence exists -> cooperative follow-up",
            }
        self.route = "manhunt"
        return {
            "goal_changes": [{"npc_id": "npc_1", "action": "add",
                              "goal": "frame Ren", "urgency": "normal",
                              "reason": "no one found the clue"}],
            "story_events": [{
                "event_id": "quiet_manhunt", "title": "Quiet manhunt",
                "description": "with no clue the mayor moves first",
                "trigger_at": context["time"]["night_start"] + 3 * 3600,
                "effects": [{"type": "set_belief", "npc_id": "npc_1",
                             "key": "ren_isolated", "value": 0.7}],
            }],
            "story_updates": [],
            "new_mysteries": [],
            "reasoning_summary": "no evidence -> the mayor acts first",
        }


def run_route(route):
    provider = RouteNightProvider()
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = provider
    engine.npc_goal_provider = None
    engine.tick_seconds = 3600.0
    engine._last_director_day = 0
    world = engine.world

    if route == "A":
        world.player.x, world.player.y = 20.0, 20.0
        engine.inspect_object("mayor_bookshelf")          # discover a clue
        world.player.x, world.player.y = 70.0, 70.0
        world.get_npc("npc_2").position_override = [70.0, 70.0]
        engine.talk("npc_2", "你好，Ren。")               # interact
        engine.protect_ren()                              # help Ren
    else:
        for _ in range(6):
            engine.move("w")                              # ignore everything

    world.time = 22 * 3600
    world.phase = "night_director"
    world.night_director_day = None
    engine.tick()                                          # Night Director
    return engine, provider


def summarize(route, engine, provider):
    world = engine.world
    report = world.night_director_report or {}
    return {
        "route": route,
        "evidence": len(world.evidence),
        "protected": bool(world.characters_state.get("npc_2", {}).get("protected")),
        "deviations": [d["kind"] for d in (provider.context or {}).get("story", {}).get("deviations", [])],
        "director_choice": provider.route,
        "applied_goals": [g.get("goal_string") for g in report.get("applied", {}).get("goals", []) if g.get("applied")],
        "new_events": [e.get("event_id") for e in report.get("applied", {}).get("story_events", []) if e.get("added")],
    }


if __name__ == "__main__":
    a_engine, a_provider = run_route("A")
    b_engine, b_provider = run_route("B")
    a = summarize("A", a_engine, a_provider)
    b = summarize("B", b_engine, b_provider)
    for row in (a, b):
        print(row)
    print("diverged:", a != b and a["director_choice"] != b["director_choice"])
