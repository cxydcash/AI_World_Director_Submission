"""Player movement, interaction, and safe action execution."""

from action import execute_parsed_action
from player_action_parser import PlayerIntentError, validate_parsed_action


class PlayerController:
    """Translate player intents into the existing safe Action pipeline."""

    def __init__(self, move_step=2.0, observation_radius=10.0):
        self.move_step = float(move_step)
        self.observation_radius = float(observation_radius)

    def execute_action(self, world, action, raw_text=None):
        """Validate and execute one structured Action."""
        try:
            parsed = validate_parsed_action(world, action)
        except (PlayerIntentError, ValueError) as exc:
            return False, str(exc), None

        success, message = execute_parsed_action(world, parsed, raw_text)
        return success, message, parsed if success else None

    def move(self, world, direction):
        """Move the player one step in a cardinal direction or WASD key."""
        vectors = {
            "up": (0.0, self.move_step),
            "w": (0.0, self.move_step),
            "down": (0.0, -self.move_step),
            "s": (0.0, -self.move_step),
            "left": (-self.move_step, 0.0),
            "a": (-self.move_step, 0.0),
            "right": (self.move_step, 0.0),
            "d": (self.move_step, 0.0),
        }
        key = str(direction).strip().lower()
        vector = vectors.get(key)
        if vector is None:
            return False, f"Unknown movement direction: {direction}.", None

        action = {
            "type": "move",
            "x": world.player.x + vector[0],
            "y": world.player.y + vector[1],
        }
        return self.execute_action(world, action, raw_text=f"move {key}")

    def talk(self, world, npc_id, message):
        """Talk to one nearby NPC through the existing talk Action."""
        if not isinstance(message, str) or not message.strip():
            return False, "Dialogue message must be non-empty.", None
        return self.execute_action(
            world,
            {"type": "talk", "npc_id": npc_id, "text": message.strip()},
            raw_text=f"talk {npc_id} {message.strip()}",
        )

    def observe(self, world):
        """Return only what the player can perceive near their position."""
        position = (world.player.x, world.player.y)
        location = world.current_explore_location()
        terrain = None
        if location and location in world.environment_state.get("terrain", {}):
            terrain = dict(world.environment_state["terrain"][location])

        nearby_npcs = []
        for npc in world.npcs:
            npc_position = world.npc_position(npc.id)
            distance = world.distance_between(position, npc_position)
            if distance <= self.observation_radius:
                nearby_npcs.append(
                    {
                        "id": npc.id,
                        "name": npc.name,
                        "distance": round(distance, 2),
                        "position": list(npc_position),
                        "goals": list(npc.goals),
                    }
                )

        nearby_evidence = []
        for evidence in world.evidence:
            distance = world.distance_between(position, evidence.get("location", position))
            if distance <= self.observation_radius:
                nearby_evidence.append(
                    {
                        "type": evidence.get("type", "unknown"),
                        "description": evidence.get("description", ""),
                        "distance": round(distance, 2),
                        "location": list(evidence.get("location", position)),
                    }
                )

        nearby_items = []
        for item in world.items:
            if item.holder is not None:
                continue
            distance = world.distance_between(position, item)
            if distance <= self.observation_radius:
                nearby_items.append(
                    {
                        "id": item.id,
                        "type": item.type,
                        "distance": round(distance, 2),
                        "position": [item.x, item.y],
                    }
                )

        return {
            "position": [world.player.x, world.player.y],
            "location": location,
            "terrain": terrain,
            "weather": dict(world.environment_state.get("weather", {})),
            "npcs": sorted(nearby_npcs, key=lambda item: item["distance"]),
            "evidence": sorted(nearby_evidence, key=lambda item: item["distance"]),
            "items": sorted(nearby_items, key=lambda item: item["distance"]),
        }

