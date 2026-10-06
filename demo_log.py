"""Unified terminal logging format for future visual demos."""


def format_world_event(
    player_action,
    environment,
    evidence,
    npc_reactions,
    director,
    timeline,
):
    """Format one important world event without changing game state."""
    lines = [
        "========== WORLD EVENT ==========",
        "Player Action:",
        str(player_action),
        "",
        "Environment:",
        str(environment),
        "",
        "Evidence:",
        str(evidence),
        "",
        "NPC Reaction:",
        str(npc_reactions),
        "",
        "AI Director:",
        str(director),
        "",
        "Timeline:",
        str(timeline),
        "=================================",
    ]
    return "\n".join(lines)