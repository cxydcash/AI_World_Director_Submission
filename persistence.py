"""JSON persistence for the v2 world state."""

import json
import os
from pathlib import Path

from world import WorldState


STATE_FILE = Path(__file__).with_name("game_state.json")


def save_world(world, state_file=STATE_FILE):
    """Write the world atomically so interrupted saves do not corrupt JSON."""
    state_file = Path(state_file)
    temporary_file = state_file.with_name(f".{state_file.name}.tmp")
    try:
        temporary_file.write_text(
            json.dumps(world.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_file, state_file)
    finally:
        if temporary_file.exists():
            temporary_file.unlink()


def load_world(state_file=STATE_FILE):
    """Load and validate a saved world state."""
    state_file = Path(state_file)
    if not state_file.exists():
        return None

    with state_file.open("r", encoding="utf-8") as file:
        data = json.load(file)
    return WorldState.from_dict(data)