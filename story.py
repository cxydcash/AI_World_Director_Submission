"""Minimal unresolved story-seed storage."""

STORY_SEED_TYPES = {
    "character_claim",
    "location_claim",
    "rumor",
    "past_event_claim",
}


def add_story_seed(world, seed_type, content, actor="player"):
    """Save one unresolved seed without materializing it into the world."""
    if seed_type not in STORY_SEED_TYPES:
        raise ValueError(f"Unknown story seed type: {seed_type}.")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Story seed content must be a non-empty string.")

    seed = {
        "type": seed_type,
        "content": content,
        "actor": actor,
        "status": "unresolved",
        "created_at": world.time,
    }
    world.story_seeds.append(seed)
    return seed


def unresolved_seeds(world):
    """Return the current unresolved story seeds."""
    return [seed for seed in world.story_seeds if seed.get("status") == "unresolved"]