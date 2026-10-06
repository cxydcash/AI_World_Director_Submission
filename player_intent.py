"""Natural-language player intent to safe Action candidates."""

import json

from player_action_parser import (
    PlayerIntentError,
    parse_player_action_input,
    validate_parsed_action,
)
from world import EXPLORE_LOCATIONS


LOCATION_ALIASES = {
    "forest": ("forest", "森林", "树林", "林地"),
    "home": ("home", "家", "家里", "住宅"),
    "town": ("town", "镇", "镇上", "城镇"),
    "stone_road": ("stone_road", "石路", "石头路", "石板路"),
}

NPC_ALIASES = {
    "npc_1": ("mira", "npc_1", "米拉"),
    "npc_2": ("ren", "npc_2", "雷恩", "仁"),
    "npc_3": ("ivy", "npc_3", "艾薇"),
}


class PlayerIntentParser:
    """Produce candidate Actions without modifying the world."""

    def parse_candidates(self, text, world):
        if not isinstance(text, str) or not text.strip():
            return []
        original = text.strip()
        lowered = original.lower()
        candidates = []

        def add(label, action, reason):
            normalized = self._normalize_candidate(world, action)
            if normalized is None:
                return
            signature = json.dumps(normalized, sort_keys=True)
            if any(item["signature"] == signature for item in candidates):
                return
            candidates.append(
                {
                    "id": f"candidate_{len(candidates) + 1}",
                    "label": label,
                    "action": normalized,
                    "reason": reason,
                    "signature": signature,
                }
            )

        # Safe command forms remain available for debugging inside the thought box.
        if lowered.startswith(("move ", "talk ", "create evidence ")):
            try:
                parsed = parse_player_action_input(original, world)
                add("Use parsed command", parsed, "The thought is already a valid Action command.")
            except (PlayerIntentError, ValueError):
                pass

        matched_locations = self._matched_locations(lowered)
        matched_npcs = self._matched_npcs(lowered, world)
        mentions_movement = any(
            token in lowered
            for token in ("去", "前往", "到", "寻找", "找", "调查", "搜索", "move", "go", "find", "search", "investigate")
        )
        mentions_talk = any(
            token in lowered
            for token in ("说", "问", "告诉", "对话", "talk", "ask", "tell", "speak")
        )
        mentions_help = any(token in lowered for token in ("帮助", "救", " help", "rescue", "save"))

        for location in matched_locations:
            coordinates = EXPLORE_LOCATIONS[location]
            add(
                f"Move to {location}",
                {"type": "move", "x": coordinates[0], "y": coordinates[1]},
                f"The thought references {location}.",
            )

        for npc in matched_npcs:
            coordinates = world.npc_position(npc.id)
            add(
                f"Move toward {npc.name}",
                {"type": "move", "x": coordinates[0], "y": coordinates[1]},
                f"{npc.name} is currently near that location.",
            )

        if mentions_talk:
            for npc in matched_npcs:
                add(
                    f"Talk to {npc.name}",
                    {"type": "talk", "npc_id": npc.id, "text": original},
                    "The thought contains a dialogue intent.",
                )

        if mentions_help and matched_npcs:
            npc = matched_npcs[0]
            add(
                f"Talk to {npc.name} about helping",
                {"type": "talk", "npc_id": npc.id, "text": original},
                "The thought expresses a helping intent.",
            )

        if mentions_movement and matched_npcs and not mentions_talk:
            npc = matched_npcs[0]
            add(
                f"Ask {npc.name} about the search",
                {"type": "talk", "npc_id": npc.id, "text": original},
                "The thought asks to find this NPC; asking them is a possible Action.",
            )

        if mentions_movement and not matched_locations and not matched_npcs:
            evidence = self._nearest_evidence(world)
            if evidence is not None:
                add(
                    f"Move to nearby {evidence.get('type', 'evidence')}",
                    {
                        "type": "move",
                        "x": evidence["location"][0],
                        "y": evidence["location"][1],
                    },
                    "The thought asks to investigate and nearby evidence exists.",
                )

        give_npc = self._mentioned_or_nearest_npc(lowered, matched_npcs, world)
        give_item = self._mentioned_inventory_item(lowered, world)
        if any(token in lowered for token in ("给", "give", "hand")) and give_npc and give_item:
            add(
                f"Give {give_item.id} to {give_npc.name}",
                {"type": "give_item", "item_id": give_item.id, "target": give_npc.id},
                "The thought asks to give an inventory item to an NPC.",
            )

        if any(token in lowered for token in ("藏", "hide")) and give_item:
            location = matched_locations[0] if matched_locations else None
            coordinates = EXPLORE_LOCATIONS.get(location, [world.player.x, world.player.y])
            add(
                f"Hide {give_item.id}",
                {"type": "hide_item", "item_id": give_item.id, "location": [coordinates[0], coordinates[1]]},
                "The thought asks to hide an inventory item.",
            )

        # A thought that merely names a NPC can still offer a proximity-based talk.
        if not candidates and matched_npcs:
            npc = matched_npcs[0]
            add(
                f"Talk to {npc.name}",
                {"type": "talk", "npc_id": npc.id, "text": original},
                "The thought references this NPC.",
            )

        return candidates

    def _normalize_candidate(self, world, action):
        try:
            return validate_parsed_action(world, action)
        except (PlayerIntentError, ValueError):
            return None

    def _matched_locations(self, text):
        matches = []
        for location, aliases in LOCATION_ALIASES.items():
            if any(alias.lower() in text for alias in aliases):
                matches.append(location)
        return matches

    def _matched_npcs(self, text, world):
        matches = []
        for npc in world.npcs:
            aliases = list(NPC_ALIASES.get(npc.id, (npc.id,)))
            aliases.append(npc.name.lower())
            aliases.append(npc.role.lower())
            if "mayor" in npc.name.lower() or npc.role.lower() == "mayor":
                aliases.append("村长")
                aliases.append("镇长")
            if "auditor" in npc.role.lower() or npc.name.lower() == "ivy":
                aliases.append("审计员")
            if npc.name.lower() == "ren":
                aliases.append("会计")
                aliases.append("账目")
            if any(alias in text for alias in aliases):
                matches.append(npc)
        return matches

    def _mentioned_or_nearest_npc(self, text, matched_npcs, world):
        if matched_npcs:
            return matched_npcs[0]
        if any(token in text for token in ("npc", "人", "村民")):
            if not world.npcs:
                return None
            return min(
                world.npcs,
                key=lambda npc: world.distance_between(
                    world.player, world.npc_position(npc.id)
                ),
            )
        return None

    def _mentioned_inventory_item(self, text, world):
        for item_id in world.player.inventory:
            item = world.get_item(item_id)
            if item is None:
                continue
            if item.id.lower() in text or item.type.lower() in text:
                return item
        return None

    def _nearest_evidence(self, world):
        if not world.evidence:
            return None
        return min(
            world.evidence,
            key=lambda evidence: world.distance_between(
                world.player, evidence.get("location", [world.player.x, world.player.y])
            ),
        )
