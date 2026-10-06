"""World environment and physical-action evaluation."""

from world import EXPLORE_LOCATIONS


DEFAULT_TERRAIN = {
    "village": {"type": "stone", "moisture": 0.1},
    "forest": {"type": "soil", "moisture": 0.6},
    "stone_road": {"type": "stone", "moisture": 0.0},
    "town": {"type": "stone", "moisture": 0.1},
}

DEFAULT_WEATHER = {"rain": False, "temperature": 20.0}


def default_environment_state():
    """Return a fresh environment state."""
    return {
        "terrain": {name: dict(data) for name, data in DEFAULT_TERRAIN.items()},
        "weather": dict(DEFAULT_WEATHER),
        "temporary_changes": [],
    }


def location_name(location):
    """Resolve a location name or coordinate pair to a terrain key."""
    if isinstance(location, str):
        if location not in DEFAULT_TERRAIN:
            raise ValueError(f"Unknown terrain location: {location}.")
        return location
    if isinstance(location, (list, tuple)) and len(location) == 2:
        best = None
        best_distance = None
        for name, coordinates in EXPLORE_LOCATIONS.items():
            distance = (float(location[0]) - coordinates[0]) ** 2 + (
                float(location[1]) - coordinates[1]
            ) ** 2
            if best_distance is None or distance < best_distance:
                best = name
                best_distance = distance
        return best
    raise ValueError("Location must be a known name or coordinate pair.")


def terrain_at(environment_state, location):
    """Return terrain data for a location."""
    name = location_name(location)
    return environment_state["terrain"][name]


def active_temporary_changes(environment_state, current_time):
    """Return temporary changes that are still active."""
    active = []
    for change in environment_state.get("temporary_changes", []):
        expires_at = change.get("created_at", 0) + change.get("change", {}).get("duration", 0)
        if current_time < expires_at:
            active.append(change)
    return active


def add_temporary_change(environment_state, location, change, created_at):
    """Add a temporary environmental change such as added moisture."""
    environment_state.setdefault("temporary_changes", []).append(
        {
            "location": location_name(location),
            "change": dict(change),
            "created_at": created_at,
        }
    )


def moisture_at(environment_state, location, current_time):
    """Return effective moisture including weather and active changes."""
    moisture = terrain_at(environment_state, location)["moisture"]
    if environment_state.get("weather", {}).get("rain", False):
        moisture += 0.3
    for change in active_temporary_changes(environment_state, current_time):
        if change["location"] == location_name(location):
            moisture += change["change"].get("moisture", 0.0)
    return moisture


def environment_changes(environment_state, current_time):
    """Return the observable temporary environment changes."""
    return [
        {
            "location": change["location"],
            "change": change["change"],
            "created_at": change["created_at"],
            "expires_at": change["created_at"] + change.get("change", {}).get("duration", 0),
        }
        for change in active_temporary_changes(environment_state, current_time)
    ]


def evaluate_player_action(world, action):
    """Evaluate whether a parsed action is physically plausible."""
    if action.get("type") != "create_evidence":
        return {"success": True, "effect": None, "side_effects": []}

    evidence_type = action.get("evidence_type", "").lower()
    if evidence_type not in {"footprint", "footprints", "track"}:
        return {"success": True, "effect": None, "side_effects": []}

    location = action.get("location")
    terrain = terrain_at(world.environment_state, location)
    moisture = moisture_at(world.environment_state, location, world.time)

    if action.get("water_applied"):
        add_temporary_change(
            world.environment_state,
            location,
            {"moisture": 0.5, "duration": 3600},
            world.time,
        )
        moisture += 0.5
        world.evidence.append(
            {
                "type": "water_trace",
                "owner": "player",
                "location": _location_coordinates(location),
                "description": "water traces remain",
                "created_at": world.time,
                "investigated": False,
            }
        )

    if terrain["type"] == "stone":
        if moisture >= 0.4:
            return {
                "success": True,
                "effect": "wet footprints created",
                "side_effects": [
                    "water traces remain",
                    "NPC may notice unusual moisture",
                ],
            }
        return {
            "success": False,
            "reason": "stone surface cannot preserve footprints",
        }

    if moisture >= 0.5:
        effect = "clear footprints created"
    else:
        effect = "faint footprints created"
    return {"success": True, "effect": effect, "side_effects": []}


def _location_coordinates(location):
    if isinstance(location, str):
        return [float(EXPLORE_LOCATIONS[location][0]), float(EXPLORE_LOCATIONS[location][1])]
    return [float(location[0]), float(location[1])]