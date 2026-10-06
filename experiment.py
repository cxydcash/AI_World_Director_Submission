"""Repeatable A/B/C scenarios and experiment-result recording."""

import json
from dataclasses import dataclass
from pathlib import Path

from entities import Item, NPC, Player
from event import execute_event
from persistence import save_world
from world import WorldState


DEFAULT_RESULTS_FILE = Path(__file__).with_name("experiment_results.json")


@dataclass
class Scenario:
    name: str
    time: float
    player_position: tuple
    actions: tuple
    description: str = ""


def build_scenario_world(scenario):
    """Build one isolated world for a fixed player-behavior scenario."""
    player_x, player_y = scenario.player_position
    items = [
        Item(id="item_1", type="key", x=player_x, y=player_y),
        Item(id="item_2", type="food", x=20.0, y=30.0),
        Item(id="item_3", type="tool", x=50.0, y=50.0),
    ]
    return WorldState(
        time=scenario.time,
        player=Player(player_x, player_y, inventory=[]),
        npcs=[
            NPC(
                id="npc_1",
                name="Mira",
                x=20.0,
                y=20.0,
                vision_range=5.0,
                relationships={"player": 0.5, "npc_2": 0.7},
                goals=["watch_items"],
            ),
            NPC(
                id="npc_2",
                name="Ren",
                x=80.0,
                y=70.0,
                vision_range=8.0,
                relationships={"player": 0.3},
                goals=["talk_to_mira"],
            ),
            NPC(
                id="npc_3",
                name="Ivy",
                x=50.0,
                y=60.0,
                vision_range=6.0,
                relationships={"player": 0.4},
                goals=["rest"],
            ),
        ],
        items=items,
    )


def default_scenarios():
    actions_a = (("pick", ("item_1",)), ("observe", ("npc_1",)), ("wait", (10,)), ("move", (30.0, 30.0)))
    actions_b = (("pick", ("item_1",)), ("observe", ("npc_1",)), ("wait", (10,)), ("move", (75.0, 75.0)))
    actions_c = (("pick", ("item_1",)), ("observe", ("npc_1",)), ("wait", (10,)), ("move", (30.0, 30.0)))
    return [
        Scenario(
            name="scenario_a_near_same_time",
            time=0.0,
            player_position=(20.0, 20.0),
            actions=actions_a,
            description="Player takes item near NPC at home, waits, leaves.",
        ),
        Scenario(
            name="scenario_b_far_same_time",
            time=0.0,
            player_position=(90.0, 90.0),
            actions=actions_b,
            description="Player takes the same item far from NPC, waits, leaves.",
        ),
        Scenario(
            name="scenario_c_near_different_time",
            time=9 * 3600,
            player_position=(20.0, 20.0),
            actions=actions_c,
            description="Same as scenario A but during workplace hours.",
        ),
    ]


def apply_actions(world, actions):
    """Apply a fixed action sequence and return human-readable messages."""
    messages = []
    for action, arguments in actions:
        if action == "move":
            success, message = world.move(*arguments)
        elif action == "pick":
            success, message = world.pick(arguments[0])
        elif action == "drop":
            success, message = world.drop(*arguments)
        elif action == "wait":
            success, message = world.wait(arguments[0])
        elif action == "observe":
            message = world.observe(arguments[0])
            success = True
        else:
            continue
        messages.append({"action": action, "arguments": list(arguments), "result": message, "success": success})
    return messages


def summarize_world(world):
    """Return a compact, JSON-friendly snapshot of relevant state."""
    return {
        "time": world.time,
        "player": {
            "position": [world.player.x, world.player.y],
            "inventory": list(world.player.inventory),
        },
        "npcs": [
            {
                "id": npc.id,
                "name": npc.name,
                "position": list(world.npc_position(npc.id)),
                "location": world.npc_location(npc.id),
                "memory_count": len(npc.memory),
                "beliefs": dict(npc.beliefs),
                "relationships": dict(npc.relationships),
                "goals": list(npc.goals),
            }
            for npc in world.npcs
        ],
        "items": [
            {"id": item.id, "type": item.type, "position": [item.x, item.y], "holder": item.holder}
            for item in world.items
        ],
        "event_history_count": len(world.event_history),
        "dialogue_count": len(world.dialogues),
    }


def run_scenario(scenario, director):
    """Run one fixed scenario through the full Director pipeline."""
    world = build_scenario_world(scenario)
    action_log = apply_actions(world, scenario.actions)

    observations = [world.observe(npc.id) for npc in world.npcs]
    before = summarize_world(world)

    event = director.generate_event(world, context=scenario.name)
    log = dict(director.last_log)
    execute_event(world, event)
    after = summarize_world(world)

    return {
        "scenario": scenario.name,
        "description": scenario.description,
        "player_actions": action_log,
        "time": before["time"],
        "player_position": before["player"]["position"],
        "observations": observations,
        "generated_event": event,
        "director_log": log,
        "world_before": before,
        "world_after": after,
    }


def run_experiment(director, scenarios=None):
    """Run all default scenarios for one director."""
    scenarios = scenarios or default_scenarios()
    return [run_scenario(scenario, director) for scenario in scenarios]


def run_comparison(ai_director, scenarios=None):
    """Run baseline and AI over identical scenarios."""
    from baseline import BaselineDirector

    scenarios = scenarios or default_scenarios()
    return {
        "scenarios": [scenario.name for scenario in scenarios],
        "baseline": run_experiment(BaselineDirector(), scenarios),
        "ai": run_experiment(ai_director, scenarios),
    }


def save_experiment_results(results, path=DEFAULT_RESULTS_FILE):
    path = Path(path)
    path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_experiment_results(path=DEFAULT_RESULTS_FILE):
    return json.loads(Path(path).read_text(encoding="utf-8"))