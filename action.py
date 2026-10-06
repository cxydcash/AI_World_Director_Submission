"""Programmatic player actions for the village simulation."""

import random

from environment import evaluate_player_action
from scenario import cancel_timeline_events
from world import EXPLORE_LOCATIONS


def talk(world, npc_id, text):
    """Record player dialogue and let the NPC remember the interaction."""
    npc = world.get_npc(npc_id)
    if npc is None or not _near(world, npc_id):
        return False, f"{npc_id} is not nearby."
    if not isinstance(text, str) or not text.strip():
        return False, "Talk text must be non-empty."

    world.dialogues.append(
        {"time": world.time, "actor": "player", "target": npc_id, "text": text}
    )
    npc.add_memory({"time": world.time, "source": "dialogue", "content": text})
    world.add_history("player_talked_to_npc", npc_id, text)
    world.apply_action_time("talk")
    world.add_action("talk", npc_id, text)
    return True, f"You said to {npc.name}: {text}"


def rescue(world, npc_id):
    """Rescue a nearby living NPC and resolve related timeline events."""
    npc = world.get_npc(npc_id)
    state = _state(world, npc_id)
    if npc is None or not _near(world, npc_id):
        return False, f"{npc_id} is not nearby."
    if not state.get("alive", True):
        return False, f"{npc.name} cannot be rescued."

    state["rescued"] = True
    state["injured"] = False
    state["unconscious"] = False
    state["location"] = "village"
    cancel_timeline_events(world, {"villagers_find_ren", "ren_worsens", "ren_dies"})
    npc.add_memory(
        {"time": world.time, "source": "action", "content": "player_rescued_me"}
    )
    npc.change_relationship("player", 0.3)
    message = f"Rescued {npc.name}. Relationship +0.3."
    world.add_history("player_rescued_npc", npc_id, message)
    world.apply_action_time("rescue")
    world.add_action("rescue", npc_id, message)

    for quest in world.quests:
        if quest.status == "active" and quest.target in {
            f"find {npc.name}",
            f"meet {npc.name}",
        }:
            world.complete_quest(quest.quest_id)
    return True, message


def attack(world, npc_id):
    """Apply a minimal attack result without a combat subsystem."""
    npc = world.get_npc(npc_id)
    state = _state(world, npc_id)
    if npc is None or not _near(world, npc_id):
        return False, f"{npc_id} is not nearby."

    state["health"] = max(0, state.get("health", 100) - 20)
    state["injured"] = True
    npc.change_relationship("player", -0.2)
    message = f"Attacked {npc.name}. Health -20, relationship -0.2."
    world.add_history("player_attacked_npc", npc_id, message)
    world.apply_action_time("attack")
    world.add_action("attack", npc_id, message)
    return True, message


def knock_out(world, npc_id):
    """Set an NPC unconscious without adding a combat system."""
    npc = world.get_npc(npc_id)
    state = _state(world, npc_id)
    if npc is None or not _near(world, npc_id):
        return False, f"{npc_id} is not nearby."

    state["unconscious"] = True
    npc.change_relationship("player", -0.1)
    message = f"Knocked out {npc.name}."
    world.add_history("player_knocked_out_npc", npc_id, message)
    world.apply_action_time("knock_out")
    world.add_action("knock_out", npc_id, message)
    return True, message


def escape(world, npc_id, rng=None):
    """Attempt to escape with a rescued NPC using player training."""
    npc = world.get_npc(npc_id)
    state = _state(world, npc_id)
    if npc is None or not _near(world, npc_id):
        return False, f"{npc_id} is not nearby."
    if not state.get("alive", True):
        return False, f"{npc.name} cannot escape."

    rng = rng or random.Random(f"{world.time}:{npc_id}:{world.player.strength}")
    if rng.random() > world.player.escape_probability:
        message = f"Escape with {npc.name} failed."
        world.add_history("player_escape_failed", npc_id, message)
        world.add_action("escape", npc_id, message)
        return False, message

    state["rescued"] = True
    state["unconscious"] = False
    state["location"] = "village"
    cancel_timeline_events(world, {"villagers_find_ren", "ren_worsens", "ren_dies"})
    message = f"Escaped with {npc.name}."
    world.add_history("player_escaped_with_npc", npc_id, message)
    world.apply_action_time("escape")
    world.add_action("escape", npc_id, message)
    return True, message


def train(world):
    """Increase player strength/fitness and update escape probability."""
    world.player.strength += 1
    world.player.fitness += 1
    world.player.escape_probability = 0.33 + world.player.strength * 0.05
    world.apply_action_time("train")
    message = (
        f"Trained. strength={world.player.strength:g}, "
        f"escape_probability={world.player.escape_probability:.2f}."
    )
    world.add_history("player_trained", "self", "Trained strength and fitness.")
    world.add_action("train", "self", message)
    return True, message


def fake_trace(world, x=None, y=None):
    """Create misleading footprint evidence at the player's location."""
    x = world.player.x if x is None else float(x)
    y = world.player.y if y is None else float(y)
    record = {
        "type": "footprint",
        "owner": "unknown",
        "real_owner": "player",
        "location": [x, y],
        "created_at": world.time,
        "investigated": False,
    }
    world.evidence.append(record)
    message = f"Created fake footprint evidence at ({x:g}, {y:g})."
    world.add_history("player_faked_trace", "evidence", message)
    _auto_investigate_new_evidence(world)
    world.apply_action_time("fake_trace")
    world.add_action("fake_trace", "evidence", message)
    return True, message


def investigate_evidence(world, npc_id):
    """Let an NPC form a belief from nearby evidence."""
    npc = world.get_npc(npc_id)
    if npc is None:
        return False, f"NPC not found: {npc_id}."

    for evidence in world.evidence:
        if evidence.get("investigated"):
            continue
        ex, ey = evidence["location"]
        if world.distance_between(world.npc_position(npc_id), (ex, ey)) <= npc.vision_range + 3:
            evidence["investigated"] = True
            evidence["investigated_by"] = npc_id
            npc.add_memory(
                {
                    "time": world.time,
                    "source": "evidence",
                    "content": f"Found {evidence['type']} leading away.",
                }
            )
            if evidence["type"] == "water_trace":
                npc.set_belief("someone_manipulated_evidence", 0.8)
            else:
                npc.set_belief("ren_went_wrong_direction", 0.8)
            message = f"{npc.name} investigated the false trace."
            world.add_history("npc_investigated_evidence", npc_id, message)
            world.apply_action_time("investigate")
            world.add_action("investigate_evidence", npc_id, message)
            return True, message
    return False, f"{npc.name} found no evidence."


def create_evidence(world, evidence_type, location, description, target_belief=None, water_applied=False):
    """Create structured evidence that NPCs may investigate later."""
    coordinates = _resolve_location(location)
    record = {
        "type": str(evidence_type),
        "owner": "player",
        "location": coordinates,
        "description": str(description),
        "created_at": world.time,
        "investigated": False,
    }
    if target_belief:
        record["target_belief"] = str(target_belief)
    world.evidence.append(record)
    message = f"Created {evidence_type} evidence: {description}"
    world.add_history("player_created_evidence", evidence_type, message)
    _auto_investigate_new_evidence(world)
    world.apply_action_time("create_evidence", water_applied=water_applied)
    world.add_action("create_evidence", evidence_type, message)
    return True, message


def give_item(world, item_id, target):
    """Give an inventory item to a nearby NPC."""
    if item_id not in world.player.inventory:
        return False, f"Item is not in player inventory: {item_id}."
    npc = world.get_npc(target)
    if npc is None or not _near(world, target):
        return False, f"{target} is not nearby."

    item = world.get_item(item_id)
    world.player.inventory.remove(item_id)
    item.holder = target
    item.x, item.y = world.npc_position(target)
    npc.add_memory(
        {"time": world.time, "source": "action", "content": f"player_gave_me_{item_id}"}
    )
    npc.change_relationship("player", 0.1)
    message = f"Gave {item_id} to {npc.name}."
    world.add_history("player_gave_item", target, message)
    world.apply_action_time("give_item")
    world.add_action("give_item", target, message)
    return True, message


def hide_item(world, item_id, location):
    """Hide a player-held item at a resolved world location."""
    if item_id not in world.player.inventory:
        return False, f"Item is not in player inventory: {item_id}."
    coordinates = _resolve_location(location)
    item = world.get_item(item_id)
    world.player.inventory.remove(item_id)
    item.holder = None
    item.x, item.y = coordinates
    message = f"Hid {item_id} at ({coordinates[0]:g}, {coordinates[1]:g})."
    world.add_history("player_hid_item", item_id, message)
    world.apply_action_time("hide_item")
    world.add_action("hide_item", item_id, message)
    return True, message


def create_rumor(world, target, content):
    """Record a rumor as NPC memory without forcing it to become true."""
    npc = world.get_npc(target)
    if npc is None or not _near(world, target):
        return False, f"{target} is not nearby."
    npc.add_memory({"time": world.time, "source": "rumor", "content": content})
    message = f"Told {npc.name}: {content}"
    world.add_history("player_created_rumor", target, content)
    world.apply_action_time("create_rumor")
    world.add_action("create_rumor", target, content)
    return True, message


def _auto_investigate_new_evidence(world):
    """Let nearby NPCs notice new evidence according to vision range."""
    reactions = []
    for evidence in world.evidence:
        already = set(evidence.get("auto_investigated_by", []))
        for npc in world.npcs:
            if npc.id in already:
                continue
            distance = world.distance_between(
                world.npc_position(npc.id), evidence["location"]
            )
            if distance > npc.vision_range:
                continue

            npc.add_memory(
                {
                    "time": world.time,
                    "source": "evidence",
                    "content": f"Observed {evidence['type']} evidence",
                }
            )
            if evidence.get("target_belief"):
                npc.set_belief(str(evidence["target_belief"]), 0.7)
            elif evidence["type"] == "water_trace":
                npc.set_belief("someone_manipulated_evidence", 0.6)
            else:
                npc.set_belief("ren_went_wrong_direction", 0.7)

            if {"cautious", "suspicious"} & set(npc.personality):
                npc.set_belief("suspect_false_evidence", 0.5)

            evidence.setdefault("auto_investigated_by", []).append(npc.id)
            reactions.append(
                {
                    "npc_id": npc.id,
                    "evidence_type": evidence["type"],
                    "distance": distance,
                }
            )
    world.last_evidence_reactions = reactions
    return reactions


def execute_parsed_action(world, parsed_action, raw_text=None):
    """Execute one validated parser action and attach raw natural language."""
    environment_result = evaluate_player_action(world, parsed_action)
    if not environment_result.get("success", True):
        return False, environment_result.get("reason", "Environment rejected action.")
    action_type = parsed_action.get("type")
    if action_type == "move":
        result = world.move(parsed_action["x"], parsed_action["y"])
    elif action_type == "talk":
        result = talk(world, parsed_action["npc_id"], parsed_action["text"])
    elif action_type == "create_evidence":
        result = create_evidence(
            world,
            parsed_action["evidence_type"],
            parsed_action["location"],
            parsed_action["description"],
            parsed_action.get("target_belief"),
            parsed_action.get("water_applied", False),
        )
    elif action_type == "give_item":
        result = give_item(world, parsed_action["item_id"], parsed_action["target"])
    elif action_type == "hide_item":
        result = hide_item(world, parsed_action["item_id"], parsed_action["location"])
    elif action_type == "create_rumor":
        result = create_rumor(
            world, parsed_action["target"], parsed_action["content"]
        )
    else:
        return False, f"Unsupported parsed action: {action_type}."

    success, message = result
    if success:
        _auto_investigate_new_evidence(world)
    if world.actions:
        world.actions[-1]["raw_text"] = raw_text
        world.actions[-1]["parsed_action"] = dict(parsed_action)
    return success, message


def _resolve_location(location):
    if isinstance(location, str):
        coordinates = EXPLORE_LOCATIONS.get(location)
        if coordinates is None:
            raise ValueError(f"Unknown location: {location}.")
        return [float(coordinates[0]), float(coordinates[1])]
    if isinstance(location, (list, tuple)) and len(location) == 2:
        x = float(location[0])
        y = float(location[1])
        if not 0.0 <= x <= 100.0 or not 0.0 <= y <= 100.0:
            raise ValueError("Evidence/item coordinates must be between 0 and 100.")
        return [x, y]
    raise ValueError("Location must be a known name or [x, y].")

def _state(world, npc_id):
    if npc_id not in world.characters_state:
        world.characters_state[npc_id] = {
            "alive": True,
            "injured": False,
            "unconscious": False,
            "health": 100,
            "location": "village",
            "rescued": False,
        }
    return world.characters_state[npc_id]


def _near(world, npc_id):
    return world.distance_between(world.player, world.npc_position(npc_id)) <= 5.0
