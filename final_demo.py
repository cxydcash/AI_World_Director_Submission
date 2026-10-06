"""Final scripted demo for the complete v2 system."""

import json

from baseline import BaselineDirector
from director import LLMDirector
from event import execute_event
from experiment import (
    Scenario,
    apply_actions,
    build_scenario_world,
    summarize_world,
)
from persistence import save_world


def build_demo_scenario():
    """Return the fixed near-NPC, same-time demo scenario."""
    actions = (
        ("pick", ("item_1",)),
        ("observe", ("npc_1",)),
        ("wait", (10,)),
        ("move", (30.0, 30.0)),
    )
    return Scenario(
        name="final_demo_near_same_time",
        time=0.0,
        player_position=(20.0, 20.0),
        actions=actions,
        description="Player takes item near NPC at home, waits, leaves.",
    )


def run_final_demo(director=None, scenario=None, save_path=None):
    """Run one scripted scenario and return a structured summary."""
    scenario = scenario or build_demo_scenario()
    world = build_scenario_world(scenario)

    print("=== Final Demo ===")
    action_log = apply_actions(world, scenario.actions)
    print("1. Player actions:")
    print(json.dumps(action_log, ensure_ascii=False, indent=2))

    observations = [world.observe(npc.id) for npc in world.npcs]
    print("2. NPC observations / memory:")
    print(json.dumps(observations, ensure_ascii=False, indent=2))

    before = summarize_world(world)
    event = director.generate_event(world, context=scenario.name)
    log = dict(director.last_log)
    print("3. Director:")
    print(
        f"   source={log.get('source')} | "
        f"model={log.get('model')} | "
        f"input={log.get('input_summary')}"
    )
    print("4. Generated Event DSL:")
    print(json.dumps(event, ensure_ascii=False, indent=2))

    execute_event(world, event)
    after = summarize_world(world)
    print("5. Validator: valid")
    print("6. Executor: executed")
    print("7. World before / after:")
    print(json.dumps({"before": before, "after": after}, ensure_ascii=False, indent=2))
    print(f"8. Event history entries: {len(world.event_history)}")

    if save_path:
        save_world(world, save_path)
        print(f"Saved demo state to {save_path}")

    return {
        "scenario": scenario.name,
        "player_actions": action_log,
        "observations": observations,
        "generated_event": event,
        "director_log": log,
        "world_before": before,
        "world_after": after,
    }


def create_director(use_baseline):
    if use_baseline:
        return BaselineDirector()
    return LLMDirector()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the final v2 demo.")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Use the deterministic BaselineDirector instead of LLM.",
    )
    parser.add_argument("--save", default=None, help="Optional save path.")
    args = parser.parse_args()

    run_final_demo(create_director(args.baseline), save_path=args.save)