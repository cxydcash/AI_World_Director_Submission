"""Interactable world objects for the open-world demo."""

OBJECT_CATALOG = {
    "home": [
        {
            "id": "mayor_desk",
            "location": "home",
            "description": "Mayor's heavy oak desk with locked drawers.",
            "visibility": "visible",
            "discover_condition": "always",
            "related_evidence": ["hidden_ledger"],
        },
        {
            "id": "mayor_bookshelf",
            "location": "home",
            "description": "A bookshelf of account ledgers and old documents.",
            "visibility": "visible",
            "discover_condition": "always",
            "related_evidence": ["altered_records"],
        },
        {
            "id": "mayor_hidden_drawer",
            "location": "home",
            "description": "A hidden drawer behind the desk.",
            "visibility": "hidden",
            "discover_condition": "inspect_mayor_desk",
            "related_evidence": ["hidden_ledger"],
        },
        {
            "id": "mayor_coat",
            "location": "home",
            "description": "A heavy coat hanging by the door.",
            "visibility": "visible",
            "discover_condition": "always",
            "related_evidence": [],
        },
        {
            "id": "mayor_desk_lock",
            "location": "home",
            "description": "A sturdy lock on the desk drawer.",
            "visibility": "visible",
            "discover_condition": "always",
            "related_evidence": ["broken_lock"],
        },
        {
            "id": "ledger",
            "location": "home",
            "description": "The altered village ledger hidden in the drawer.",
            "visibility": "requires_discovery",
            "discover_condition": "drawer_open",
            "related_evidence": ["hidden_ledger"],
        },
    ],
    "town": [
        {
            "id": "footprints",
            "location": "town",
            "description": "Muddy footprints leading from the office.",
            "visibility": "visible",
            "discover_condition": "always",
            "related_evidence": ["footprints"],
        },
        {
            "id": "broken_branch",
            "location": "town",
            "description": "A freshly broken branch near the road.",
            "visibility": "visible",
            "discover_condition": "always",
            "related_evidence": ["broken_branch"],
        },
        {
            "id": "dropped_note",
            "location": "town",
            "description": "A crumpled note dropped beside the footprints.",
            "visibility": "hidden",
            "discover_condition": "search_footprints",
            "related_evidence": ["ren_note"],
        },
    ],
    "forest": [
        {
            "id": "ren_camp",
            "location": "forest",
            "description": "Signs of someone hiding among the trees.",
            "visibility": "visible",
            "discover_condition": "always",
            "related_evidence": ["ren_hiding"],
        },
        {
            "id": "water_trace",
            "location": "forest",
            "description": "Unusual water traces on the soil.",
            "visibility": "visible",
            "discover_condition": "always",
            "related_evidence": ["water_trace"],
        },
    ],
}


class WorldObjectRegistry:
    """Resolve nearby world objects using only location and world state."""

    def __init__(self, catalog=None):
        self.catalog = catalog or OBJECT_CATALOG

    def objects_for_location(self, location):
        return [dict(obj) for obj in self.catalog.get(location, [])]

    def visible_objects(self, world, radius=12.0):
        location = world.current_explore_location()
        if not location:
            return []
        visible = []
        for obj in self.objects_for_location(location):
            if self._is_visible(obj, world):
                visible.append({**obj, "location": location})
        return visible

    def get(self, object_id):
        for objects in self.catalog.values():
            for obj in objects:
                if obj["id"] == object_id:
                    return dict(obj)
        return None

    def _is_visible(self, obj, world):
        condition = obj.get("discover_condition", "always")
        visibility = obj.get("visibility", "visible")
        if visibility == "hidden":
            return False
        if condition == "always":
            return True
        if condition == "inspect_mayor_desk":
            return "mayor_desk" in (world.object_state or {})
        if condition == "drawer_open":
            return "mayor_hidden_drawer" in (world.object_state or {})
        if condition == "search_footprints":
            return "footprints" in (world.object_state or {})
        return False
