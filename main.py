"""Terminal demo for the v2 living-simulation loop."""

import json
import os
import shlex
import shutil
from datetime import datetime
from pathlib import Path

from action import attack, escape, execute_parsed_action, fake_trace, investigate_evidence, knock_out, rescue, talk as action_talk, train
from director import LLMDirector, MockDirector, load_environment
from entities import Item, NPC, Player
from event import EventValidationError, execute_event
from persistence import STATE_FILE, load_world, save_world
from player_action_parser import PlayerIntentError, parse_player_intent
from quest import Quest
from scenario import apply_timeline_change
from story_engine import timeline_summary
from schedule import format_time_of_day
from story import STORY_SEED_TYPES
from world import MAP_HEIGHT, MAP_WIDTH, WorldState


COMMANDS = (
    "move <x> <y>",
    "pick <item_id>",
    "drop <item_id> <x> <y>",
    "wait <seconds>",
    "work",
    "sleep",
    "buy <item_id>",
    "explore",
    "accept <quest_id>",
    "quests",
    "history",
    "evidence",
    "environment",
    "npc <npc_id>",
    "observe <npc_id>",
    "talk <npc_id> <text>",
    "help <npc_id>",
    "ignore <npc_id>",
    "rescue <npc_id>",
    "attack <npc_id>",
    "knock_out <npc_id>",
    "escape <npc_id>",
    "train",
    "fake_trace [x y]",
    "investigate <npc_id>",
    "timeline",
    "do <natural language>",
    "claim <type> <text>",
    "time",
    "schedule <npc_id>",
    "director",
    "status",
    "save",
    "reset",
    "quit",
)


def create_director():
    """Choose the configured director with an offline mock fallback."""
    load_environment()
    mode = os.getenv("DIRECTOR_MODE", "llm").strip().lower()
    if mode == "mock":
        return MockDirector()
    return LLMDirector()


DIRECTOR = create_director()


def create_demo_world():
    """Create the initial world with three NPCs, items, and one quest."""
    return WorldState(
        time=0.0,
        player=Player(x=50.0, y=50.0, inventory=[], money=100.0, energy=100.0),
        npcs=[
            NPC(
                id="npc_1",
                name="Mira",
                x=20.0,
                y=30.0,
                vision_range=5.0,
                relationships={"player": 0.5, "npc_2": 0.7},
                goals=["watch_items"],
                character_seed={
                    "personality": ["careful", "kind"],
                    "long_term_goal": "protect Ren",
                    "secret": "She knows more about Ren's disappearance than she tells the player.",
                    "fear": "The player discovers the truth.",
                    "conflict": "She wants the player to find Ren, but fears the truth being revealed.",
                },
            ),
            NPC(
                id="npc_2",
                name="Ren",
                x=40.0,
                y=40.0,
                vision_range=8.0,
                relationships={"player": 0.3},
                goals=["talk_to_mira"],
                character_seed={
                    "personality": ["independent", "restless"],
                    "long_term_goal": "return home safely",
                    "secret": "He left voluntarily but became lost.",
                    "fear": "Being forced back before finishing his journey.",
                    "conflict": "He wants to be found, but also wants freedom.",
                },
                position_override=[40.0, 40.0],
            ),
            NPC(
                id="npc_3",
                name="Ivy",
                x=70.0,
                y=70.0,
                vision_range=6.0,
                relationships={"player": 0.4},
                goals=["rest"],
                character_seed={
                    "personality": ["observant", "calm"],
                    "long_term_goal": "learn what happened in the forest",
                    "secret": "She saw Ren leave town.",
                    "fear": "Being blamed for Ren's disappearance.",
                    "conflict": "She wants to help Mira, but does not want to become involved.",
                },
            ),
        ],
        items=[
            Item(id="item_1", type="key", x=52.0, y=50.0),
            Item(id="item_2", type="food", x=20.0, y=30.0),
            Item(id="item_3", type="tool", x=70.0, y=70.0),
        ],
        characters_state={
            "npc_1": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "village", "rescued": False},
            "npc_2": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "forest", "rescued": False},
            "npc_3": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "village", "rescued": False},
        },
        quests=[
            Quest(
                quest_id="quest_001",
                title="Find Ren",
                giver_id="npc_1",
                description="Ren went to the forest and never returned. Mira wants you to find Ren.",
                target="find Ren",
                reward=50,
            )
        ],
    )


def reset_game_state(state_file=STATE_FILE):
    """Back up the current save and restore the initial demo world."""
    state_file = Path(state_file)
    backup_path = None

    if state_file.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = state_file.with_name(
            f"{state_file.stem}.backup.{timestamp}{state_file.suffix}"
        )
        counter = 1
        while backup_path.exists():
            backup_path = state_file.with_name(
                f"{state_file.stem}.backup.{timestamp}-{counter}{state_file.suffix}"
            )
            counter += 1
        shutil.copy2(state_file, backup_path)

    reset_world = create_demo_world()
    save_world(reset_world, state_file)
    return reset_world, backup_path

def format_number(value):
    """Keep decimal input readable while preserving fractional positions."""
    return f"{value:g}"


def format_day_time(seconds):
    """Return Day N HH:MM for the current world time."""
    day = int(seconds // 86400) + 1
    return f"Day {day} {format_time_of_day(seconds)}"


def print_world_status(world):
    """Print time, player, NPCs, items, and active quests."""
    player = world.player
    print(f"Time: {format_number(world.time)} seconds ({format_day_time(world.time)})")
    print(
        "Player: "
        f"({format_number(player.x)}, {format_number(player.y)}) | "
        f"money={format_number(player.money)} | energy={format_number(player.energy)} | "
        f"inventory={player.inventory}"
    )
    if world.npcs:
        descriptions = []
        for npc in world.npcs:
            x, y = world.npc_position(npc.id)
            location = world.npc_location(npc.id)
            descriptions.append(
                f"{npc.id}:{npc.name}@{location}"
                f"({format_number(x)}, {format_number(y)})"
                f" vision={format_number(npc.vision_range)}"
            )
        print(f"NPCs: {', '.join(descriptions)}")
    for item in world.items:
        holder = item.holder if item.holder is not None else "ground"
        print(
            f"Item {item.id} ({item.type}): "
            f"({format_number(item.x)}, {format_number(item.y)}) "
            f"holder={holder}"
        )
    active_quests = [quest for quest in world.quests if quest.status == "active"]
    if active_quests:
        print("Active quests:")
        for quest in active_quests:
            print(f"- {quest.quest_id}: {quest.title} -> {quest.target}")


def print_help():
    print("Commands:")
    for command in COMMANDS:
        print(f"- {command}")


def format_evidence(world):
    """Return a readable evidence summary."""
    if not world.evidence:
        return "Evidence: none"
    lines = ["Evidence:"]
    for entry in world.evidence:
        lines.extend(
            [
                f"- type: {entry.get('type')}",
                f"  location: {entry.get('location')}",
                f"  description: {entry.get('description', '')}",
                f"  target belief: {entry.get('target_belief', 'none')}",
                f"  created time: {entry.get('created_at')}",
            ]
        )
    return "\n".join(lines)


def format_environment(world):
    """Return a readable environment summary."""
    environment = world.environment_state
    lines = ["Terrain:"]
    for name, terrain in environment.get("terrain", {}).items():
        lines.append(
            f"- {name}: {terrain.get('type')} moisture={terrain.get('moisture')}"
        )
    weather = environment.get("weather", {})
    lines.append(
        f"Weather: rain={weather.get('rain', False)} temperature={weather.get('temperature')}"
    )
    changes = environment.get("temporary_changes", [])
    lines.append("Temporary changes:")
    if not changes:
        lines.append("- none")
    for change in changes:
        expires = change.get("created_at", 0) + change.get("change", {}).get("duration", 0)
        lines.append(
            f"- {change.get('location')} {change.get('change')} expires at {expires}"
        )
    return "\n".join(lines)


def format_npc(world, npc_id):
    """Return a readable NPC cognition summary."""
    npc = world.get_npc(npc_id)
    if npc is None:
        return f"NPC not found: {npc_id}."
    lines = [
        npc.name,
        "beliefs:",
    ]
    lines.extend(
        [f"- {key}: {value}" for key, value in npc.beliefs.items()] or ["- none"]
    )
    lines.append("goals:")
    lines.extend([f"- {goal}" for goal in npc.goals] or ["- none"])
    lines.append("relationship:")
    lines.extend(
        [f"- {key}: {value}" for key, value in npc.relationships.items()] or ["- none"]
    )
    lines.append("memory:")
    lines.extend(
        [
            f"- {memory.get('content', memory)}"
            for memory in npc.memory[-5:]
        ]
        or ["- none"]
    )
    return "\n".join(lines)

def print_quests(world):
    if not world.quests:
        print("No quests.")
        return
    for quest in world.quests:
        print(
            f"{quest.quest_id} [{quest.status}] {quest.title}: {quest.description} "
            f"(reward {format_number(quest.reward)})"
        )


def print_history(world):
    if not world.history:
        print("No history yet.")
        return
    for entry in world.history[-10:]:
        print(
            f"t={format_number(entry['time'])} "
            f"{entry.get('actor', '?')} {entry.get('action', '?')} "
            f"target={entry.get('target', '')} | {entry.get('content', '')}"
        )
        details = entry.get("details")
        if details:
            print(
                f"  details: {json.dumps(details, ensure_ascii=False)}"
            )


def print_schedule(world, npc_id):
    npc = world.get_npc(npc_id)
    if npc is None:
        print(f"NPC not found: {npc_id}.")
        return

    x, y = world.npc_position(npc_id)
    location = world.npc_location(npc_id)
    change = world.next_npc_schedule_change(npc_id)
    print(f"Time: {format_number(world.time)} seconds ({format_day_time(world.time)})")
    print(f"{npc_id} is at {location} ({format_number(x)}, {format_number(y)}).")
    if change is not None:
        next_time, next_location = change
        print(
            f"Next location change at {format_number(next_time)} seconds "
            f"({format_time_of_day(next_time)}) -> {next_location}."
        )


def run_director_pipeline(world):
    print("=== Director Opportunity Check ===")
    print_world_status(world)

    observations = [world.observe(npc.id) for npc in world.npcs]
    print("NPC observations updated.")

    if hasattr(DIRECTOR, "check_timeline_change"):
        timeline_proposal = DIRECTOR.check_timeline_change(world)
        if timeline_proposal is not None:
            apply_timeline_change(world, timeline_proposal)
            print("Timeline changed:")
            print(json.dumps(timeline_proposal, ensure_ascii=False, indent=2))

    before_ids = {npc.id for npc in world.npcs}
    decision = DIRECTOR.check_opportunity(world)

    if not decision.get("should_trigger"):
        print(f"No event triggered. Reason: {decision.get('reason')}")
        return

    event = decision.get("event")
    print("Director generated Event DSL:")
    print(json.dumps(event, ensure_ascii=False, indent=2))

    try:
        execute_event(world, event)
        print("Validator: valid")
        print("Executor: executed")
    except EventValidationError as exc:
        print(f"Validator rejected event: {exc}")
        return

    for npc in world.npcs:
        if npc.id not in before_ids:
            print("Generated NPC:")
            print(f"ID: {npc.id}")
            print(f"Name: {npc.name}")
            print(f"Role: {npc.role}")

    save_world(world)
    print(f"Event history entries: {len(world.event_history)}")
    print_world_status(world)


def command_succeeded(world, result):
    success, message = result
    print(message)
    if success:
        save_world(world)
        print(f"Saved to {STATE_FILE}")
    print()
    print_world_status(world)
    print()


if __name__ == "__main__":
    world = load_world()
    if world is None:
        world = create_demo_world()
        save_world(world)
        print("Created a new v2 world.")
    else:
        print(f"Loaded world from {STATE_FILE}.")

    world.story_director = DIRECTOR

    print(
        f"World map: {format_number(MAP_WIDTH)} x {format_number(MAP_HEIGHT)} "
        "continuous coordinates."
    )
    print_help()
    print()
    print_world_status(world)
    print()

    while True:
        command = input("> ").strip()
        if not command:
            continue

        try:
            parts = shlex.split(command)
        except ValueError as exc:
            print(str(exc))
            continue
        action = parts[0].lower()

        if action == "quit":
            save_world(world)
            print("World saved. Goodbye.")
            break

        if action == "status":
            print_world_status(world)
            print()
            continue

        if action == "director":
            run_director_pipeline(world)
            continue

        if action == "quests":
            print_quests(world)
            continue

        if action == "history":
            print_history(world)
            continue

        if action == "evidence":
            print(format_evidence(world))
            continue

        if action == "environment":
            print(format_environment(world))
            continue

        if action == "npc" and len(parts) == 2:
            print(format_npc(world, parts[1]))
            continue

        if action == "timeline":
            print(json.dumps(timeline_summary(world), ensure_ascii=False, indent=2))
            continue

        if action == "do" and len(parts) >= 2:
            raw_text = command[3:].strip()
            try:
                parsed_action = parse_player_intent(raw_text, world)
            except PlayerIntentError as exc:
                print(f"Intent rejected: {exc}")
                continue
            print("Parsed Action:")
            print(json.dumps(parsed_action, ensure_ascii=False, indent=2))
            result = execute_parsed_action(world, parsed_action, raw_text)
            command_succeeded(world, result)
            continue

        if action == "time":
            print(
                f"Time: {format_number(world.time)} seconds "
                f"({format_day_time(world.time)})"
            )
            continue

        if action == "schedule" and len(parts) == 2:
            print_schedule(world, parts[1])
            continue

        if action == "reset":
            world, backup_path = reset_game_state()
            world.story_director = DIRECTOR
            if backup_path is not None:
                print(f"Backup created: {backup_path}")
            print("Game state reset to the initial demo state.")
            print()
            print_world_status(world)
            print()
            continue

        if action == "save":
            save_world(world)
            print(f"Saved to {STATE_FILE}")
            continue

        if action == "observe" and len(parts) == 2:
            npc = world.get_npc(parts[1])
            if npc is None:
                print(f"NPC not found: {parts[1]}.")
                continue
            print(json.dumps(world.observe(parts[1]), ensure_ascii=False, indent=2))
            world.apply_action_time("observe")
            print(f"Memory entries for {parts[1]}: {len(npc.memory)}")
            save_world(world)
            print(f"Saved to {STATE_FILE}")
            print()
            print_world_status(world)
            print()
            continue

        if action == "claim" and len(parts) >= 3:
            seed_type = parts[1].lower()
            content = " ".join(parts[2:])
            if seed_type not in STORY_SEED_TYPES:
                print(f"Unknown seed type: {seed_type}. Use {sorted(STORY_SEED_TYPES)}")
                continue
            seed = world.add_story_seed(seed_type, content)
            save_world(world)
            print(json.dumps(seed, ensure_ascii=False, indent=2))
            print(f"Saved to {STATE_FILE}")
            continue

        try:
            if action == "move" and len(parts) == 3:
                result = world.move(float(parts[1]), float(parts[2]))
            elif action == "pick" and len(parts) == 2:
                result = world.pick(parts[1])
            elif action == "drop" and len(parts) == 4:
                result = world.drop(parts[1], float(parts[2]), float(parts[3]))
            elif action == "wait" and len(parts) == 2:
                result = world.wait(float(parts[1]))
            elif action == "work":
                result = world.work()
            elif action == "sleep":
                result = world.sleep()
            elif action == "buy" and len(parts) == 2:
                result = world.buy(parts[1])
            elif action == "explore":
                result = world.explore()
            elif action == "accept" and len(parts) == 2:
                result = world.accept_quest(parts[1])
            elif action == "talk" and len(parts) == 3:
                result = action_talk(world, parts[1], parts[2])
            elif action == "help" and len(parts) == 2:
                result = world.help(parts[1])
            elif action == "ignore" and len(parts) == 2:
                result = world.ignore(parts[1])
            elif action == "rescue" and len(parts) == 2:
                result = rescue(world, parts[1])
            elif action == "attack" and len(parts) == 2:
                result = attack(world, parts[1])
            elif action == "knock_out" and len(parts) == 2:
                result = knock_out(world, parts[1])
            elif action == "escape" and len(parts) == 2:
                result = escape(world, parts[1])
            elif action == "train" and len(parts) == 1:
                result = train(world)
            elif action == "fake_trace" and len(parts) in (1, 3):
                if len(parts) == 3:
                    result = fake_trace(world, float(parts[1]), float(parts[2]))
                else:
                    result = fake_trace(world)
            elif action == "investigate" and len(parts) == 2:
                result = investigate_evidence(world, parts[1])
            else:
                print("Unknown or incomplete command.")
                print_help()
                print()
                continue
        except ValueError as exc:
            print(str(exc))
            print()
            continue

        command_succeeded(world, result)