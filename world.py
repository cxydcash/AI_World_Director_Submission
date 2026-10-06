"""Continuous 100x100 world state and basic player operations."""

import math
import random

from action_time import action_time_cost

from entities import Item, NPC, Player
from quest import Quest
from scenario import default_timeline, process_timeline
from story_engine import check_story_events as _check_story_events
from story_timeline import StoryTimeline
from schedule import location_at_time, location_coordinates, next_schedule_change
from story import add_story_seed as _add_story_seed


MAP_WIDTH = 100.0
MAP_HEIGHT = 100.0
PICKUP_RADIUS = 5.0
INTERACTION_RADIUS = 5.0
DISCOVERY_RADIUS = 8.0
PLAYER_HOLDER = "player"

# Explicit world phases (Step 12.5). The night-director window is 22:00-24:00.
PHASE_DAY = "day"
PHASE_NIGHT_DIRECTOR = "night_director"
WORLD_PHASES = frozenset({PHASE_DAY, PHASE_NIGHT_DIRECTOR})
EXPLORE_DURATION = 30 * 60

EXPLORE_LOCATIONS = {
    "home": (20.0, 20.0),
    "forest": (40.0, 40.0),
    "town": (70.0, 70.0),
    "stone_road": (60.0, 60.0),
}

#: Extra place names that resolve onto the existing locations (no new map system).
LOCATION_ALIASES = {
    "mayor_house": "home",
    "home_office": "home",
    "mayor_office": "town",
    "town_office": "town",
    "village": "town",
}

SHOP_ITEMS = {
    "shop_bread": {"type": "bread", "price": 5.0},
    "shop_medicine": {"type": "medicine", "price": 20.0},
}


class WorldState:
    """Own time, entities, items, and the saved world state."""

    def __init__(self, time=0.0, player=None, npcs=None, items=None, event_history=None, dialogues=None, quests=None, history=None, story_seeds=None, characters_state=None, evidence=None, actions=None, story_timeline=None, environment_state=None, story_event_history=None, object_state=None, dialogue_session=None, phase=PHASE_DAY, night_director_day=None, night_director_report=None, daily_orchestration_day=None):
        self.time = _validate_time(time)
        self.player = player if player is not None else Player(50.0, 50.0)
        self.npcs = list(npcs or [])
        self.items = list(items or [])
        self.event_history = list(event_history or [])
        self.dialogues = list(dialogues or [])
        self.quests = list(quests or [])
        self.history = list(history or [])
        self.story_seeds = list(story_seeds or [])
        self.environment_state = dict(environment_state or _default_environment_state())
        self.characters_state = dict(characters_state or {})
        if not self.characters_state:
            self.characters_state = {
                npc.id: {
                    "alive": True,
                    "injured": False,
                    "unconscious": False,
                    "health": 100,
                    "location": "village",
                    "rescued": False,
                }
                for npc in self.npcs
            }
        self.evidence = list(evidence or [])
        self.actions = list(actions or [])
        self.story_timeline = StoryTimeline(story_timeline or default_timeline())
        self.story_event_history = list(story_event_history or [])
        self.object_state = dict(object_state or {})
        self.dialogue_session = _normalize_dialogue_session(dialogue_session)
        # Step 12.5 phase state (persisted).
        self.phase = phase if phase in WORLD_PHASES else PHASE_DAY
        self.night_director_day = (
            int(night_director_day) if night_director_day is not None else None
        )
        self.night_director_report = (
            dict(night_director_report) if isinstance(night_director_report, dict) else None
        )
        # The day whose daily orchestration has completed. The Night Director of
        # day N plans day N+1 and records it here, so the legacy daily director
        # never runs again for that day (Step 12.5 fix).
        self.daily_orchestration_day = (
            int(daily_orchestration_day) if daily_orchestration_day is not None else None
        )
        self._sync_npc_positions()
        self._validate_state()

    def move(self, x, y):
        """Move the player to a coordinate, consuming distance-based time."""
        try:
            new_x = _validate_coordinate(x, "x", MAP_WIDTH)
            new_y = _validate_coordinate(y, "y", MAP_HEIGHT)
        except ValueError as exc:
            return False, str(exc)

        from_x, from_y = self.player.x, self.player.y
        distance = math.hypot(new_x - from_x, new_y - from_y)
        self.player.x = new_x
        self.player.y = new_y
        time_cost = self.apply_action_time("move", distance=distance)

        location = self.current_explore_location()
        if location:
            self.add_history(
                f"player_entered_{location}",
                location,
                f"Entered {location}.",
            )
        self.add_history(
            "player_moved",
            "move",
            f"Moved from ({from_x:g}, {from_y:g}) to ({new_x:g}, {new_y:g}).",
            details={
                "from": [from_x, from_y],
                "to": [new_x, new_y],
                "distance": distance,
                "time_cost": time_cost,
            },
        )
        return True, f"Player moved to ({new_x:g}, {new_y:g})."

    def pick(self, item_id):
        """Pick up a nearby unheld item."""
        item = self.get_item(item_id)
        if item is None:
            return False, f"Item not found: {item_id}."
        if item.holder is not None:
            return False, f"Item is already held: {item_id}."

        distance = self.distance_between(self.player, item)
        if distance > PICKUP_RADIUS:
            return False, (
                f"Item is too far away: {item_id} "
                f"(distance {distance:.2f}, max {PICKUP_RADIUS:g})."
            )

        item.holder = PLAYER_HOLDER
        item.x = self.player.x
        item.y = self.player.y
        if item.id not in self.player.inventory:
            self.player.inventory.append(item.id)
        self.add_history("player_took_item", item_id, f"Picked up {item_id}.")
        self.apply_action_time("pick")
        return True, f"Picked up {item_id}."

    def drop(self, item_id, x, y):
        """Drop a held item at any valid coordinate."""
        try:
            drop_x = _validate_coordinate(x, "x", MAP_WIDTH)
            drop_y = _validate_coordinate(y, "y", MAP_HEIGHT)
        except ValueError as exc:
            return False, str(exc)

        item = self.get_item(item_id)
        if item is None:
            return False, f"Item not found: {item_id}."
        if item_id not in self.player.inventory or item.holder != PLAYER_HOLDER:
            return False, f"Item is not in the player's inventory: {item_id}."

        self.player.inventory.remove(item_id)
        item.holder = None
        item.x = drop_x
        item.y = drop_y
        self.add_history("player_dropped_item", item_id, f"Dropped {item_id} at ({drop_x:g}, {drop_y:g}).")
        self.apply_action_time("drop")
        return True, f"Dropped {item_id} at ({drop_x:g}, {drop_y:g})."

    def wait(self, seconds):
        """Advance game time by a non-negative number of seconds."""
        try:
            duration = _validate_duration(seconds)
        except ValueError as exc:
            return False, str(exc)

        self.advance_time(duration)
        return True, f"Waited {duration:g} seconds."

    def advance_time(self, seconds, run_story_events=True):
        """Advance world time and trigger schedule/environment/story updates.

        Night Phase advances time without running ordinary StoryEvents; already
        due events stay pending and run after the world resumes at 00:00.
        """
        self.time += seconds
        self._sync_npc_positions()
        self.update_environment()
        if run_story_events:
            self.check_story_events()

    def _advance_time(self, seconds):
        """Backward-compatible alias for the unified time entry point."""
        self.advance_time(seconds)

    def apply_action_time(self, action, distance=0.0, water_applied=False):
        """Calculate and apply the centralized time cost for one action."""
        cost = action_time_cost(action, distance=distance, water_applied=water_applied)
        if cost:
            self.advance_time(cost)
        return cost

    def update_environment(self):
        """Remove expired temporary environment changes."""
        active = []
        for change in self.environment_state.get("temporary_changes", []):
            expires_at = change.get("created_at", 0) + change.get("change", {}).get("duration", 0)
            if self.time < expires_at:
                active.append(change)
        self.environment_state["temporary_changes"] = active

    def check_story_events(self):
        """Check and trigger due story timeline events."""
        return _check_story_events(
            self,
            evaluator=getattr(self, "story_director", None),
        )

    def process_story_timeline(self):
        """Trigger due predefined story events."""
        return process_timeline(self)

    def add_action(self, action, target, text):
        """Persist one structured player action for Director context."""
        record = {
            "time": self.time,
            "actor": "player",
            "action": action,
            "target": target,
            "text": text,
        }
        self.actions.append(record)
        return record
    def distance_between(self, first, second):
        """Return Euclidean distance between entities or coordinate pairs."""
        first_x, first_y = _extract_coordinates(first)
        second_x, second_y = _extract_coordinates(second)
        return math.hypot(first_x - second_x, first_y - second_y)

    def get_item(self, item_id):
        """Return an item by ID, or None when it does not exist."""
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def add_history(self, action, target, content, actor="player", details=None):
        """Append one player/world history record."""
        record = {
            "time": self.time,
            "actor": actor,
            "action": action,
            "target": target,
            "content": content,
        }
        if details is not None:
            record["details"] = details
        self.history.append(record)
        return self.history[-1]

    def get_quest(self, quest_id):
        for quest in self.quests:
            if quest.quest_id == quest_id:
                return quest
        return None

    def accept_quest(self, quest_id):
        quest = self.get_quest(quest_id)
        if quest is None:
            return False, f"Quest not found: {quest_id}."
        if not quest.accept():
            return False, f"Quest cannot be accepted: {quest_id}."
        self.add_history("player_started_quest", quest_id, quest.title)
        return True, f"Accepted quest: {quest.title}."

    def complete_quest(self, quest_id):
        quest = self.get_quest(quest_id)
        if quest is None:
            return False, f"Quest not found: {quest_id}."
        if not quest.complete():
            return False, f"Quest cannot be completed: {quest_id}."
        self.player.money = float(self.player.money) + float(quest.reward)
        self.add_history(
            "player_completed_quest",
            quest_id,
            f"Completed {quest.title}; reward {quest.reward}.",
        )
        return True, f"Completed quest: {quest.title}. Reward: {quest.reward}."

    def fail_quest(self, quest_id):
        quest = self.get_quest(quest_id)
        if quest is None:
            return False, f"Quest not found: {quest_id}."
        if not quest.fail():
            return False, f"Quest cannot be failed: {quest_id}."
        self.add_history("player_failed_quest", quest_id, quest.title)
        return True, f"Failed quest: {quest.title}."

    def work(self):
        self.player.work()
        self.apply_action_time("work")
        self.add_history("player_worked", "work", "Worked for 8 hours.")
        return True, "Worked 8 hours. Money +80, energy -30."

    def sleep(self):
        self.player.sleep()
        self.apply_action_time("sleep")
        self.add_history("player_slept", "sleep", "Slept for 8 hours.")
        return True, "Slept 8 hours. Energy restored."

    def buy(self, item_id):
        entry = SHOP_ITEMS.get(item_id)
        if entry is None:
            return False, f"Shop item not found: {item_id}."
        if self.player.money < entry["price"]:
            return False, f"Not enough money for {item_id}."

        item = self.get_item(item_id)
        if item is None:
            item = Item(
                id=item_id,
                type=entry["type"],
                x=self.player.x,
                y=self.player.y,
                holder=PLAYER_HOLDER,
            )
            self.items.append(item)
            self.player.inventory.append(item_id)
        elif item.holder is None:
            item.holder = PLAYER_HOLDER
            item.x = self.player.x
            item.y = self.player.y
            self.player.inventory.append(item_id)
        else:
            return False, f"Item is already held: {item_id}."

        self.player.money = float(self.player.money) - float(entry["price"])
        self.add_history("player_bought_item", item_id, f"Bought {item_id}.")
        self.apply_action_time("buy")
        return True, f"Bought {item_id} for {entry['price']} money."

    def current_explore_location(self):
        player_position = (self.player.x, self.player.y)
        for name, coordinates in EXPLORE_LOCATIONS.items():
            if self.distance_between(player_position, coordinates) <= 5.0:
                return name
        return None

    def nearby_npcs(self, radius=INTERACTION_RADIUS):
        return [
            npc
            for npc in self.npcs
            if self.distance_between(self.player, self.npc_position(npc.id)) <= radius
        ]

    def explore(self):
        location = self.current_explore_location()
        if location is None:
            return False, "You are not at an explorable location."

        self.apply_action_time("explore")
        result = self._generate_exploration_result(location)
        self.add_history("player_explored", location, result["content"])
        return True, result["message"]

    def _generate_exploration_result(self, location):
        nearby_npcs = self.nearby_npcs(DISCOVERY_RADIUS)
        if nearby_npcs:
            npc = nearby_npcs[0]
            self.record_met_npc(npc.id)
            return {
                "message": f"You found {npc.name} ({npc.id}).",
                "content": f"Found NPC {npc.id} while exploring {location}.",
            }

        nearby_items = [
            item
            for item in self.items
            if item.holder is None
            and self.distance_between(self.player, item) <= DISCOVERY_RADIUS
        ]
        if nearby_items:
            item = nearby_items[0]
            self.add_history("player_found_item", item.id, item.type)
            return {
                "message": f"You found a {item.type} ({item.id}).",
                "content": f"Found item {item.id} while exploring {location}.",
            }

        seed = random.Random(
            f"{location}:{self.time}:{self.player.x}:{self.player.y}"
        )
        if seed.random() < 0.5:
            return {
                "message": f"You learn more about {location}.",
                "content": f"Gained environmental information about {location}.",
            }
        return {
            "message": "You explored but found nothing notable.",
            "content": f"Nothing found while exploring {location}.",
        }

    def record_met_npc(self, npc_id):
        npc = self.get_npc(npc_id)
        if npc is None:
            return
        self.add_history("player_met_npc", npc_id, f"Met {npc.name}.")
        for quest in self.quests:
            if quest.status != "active":
                continue
            if quest.target in {f"find {npc.name}", f"meet {npc.name}"}:
                self.complete_quest(quest.quest_id)

    def talk(self, npc_id):
        npc = self.get_npc(npc_id)
        if npc is None or self.distance_between(self.player, self.npc_position(npc_id)) > INTERACTION_RADIUS:
            return False, f"{npc_id} is not nearby."
        npc.add_memory(
            {"time": self.time, "source": "interaction", "content": "player_talked_to_me"}
        )
        self.add_history("player_talked_to_npc", npc_id, f"Talked to {npc.name}.")
        return True, f"You talked to {npc.name}."

    def help(self, npc_id):
        npc = self.get_npc(npc_id)
        if npc is None or self.distance_between(self.player, self.npc_position(npc_id)) > INTERACTION_RADIUS:
            return False, f"{npc_id} is not nearby."
        npc.add_memory(
            {"time": self.time, "source": "interaction", "content": "player_helped_me"}
        )
        npc.change_relationship("player", 0.2)
        self.add_history("player_helped_npc", npc_id, f"Helped {npc.name}.")
        return True, f"You helped {npc.name}. Relationship +0.2."

    def ignore(self, npc_id):
        npc = self.get_npc(npc_id)
        if npc is None or self.distance_between(self.player, self.npc_position(npc_id)) > INTERACTION_RADIUS:
            return False, f"{npc_id} is not nearby."
        npc.add_memory(
            {"time": self.time, "source": "interaction", "content": "player_ignored_me"}
        )
        npc.change_relationship("player", -0.1)
        self.add_history("player_ignored_npc", npc_id, f"Ignored {npc.name}.")
        return True, f"You ignored {npc.name}. Relationship -0.1."

    def add_story_seed(self, seed_type, content):
        seed = _add_story_seed(self, seed_type, content, actor="player")
        self.add_history("player_made_claim", seed_type, content)
        return seed

    def unresolved_story_seeds(self):
        return [seed for seed in self.story_seeds if seed.get("status") == "unresolved"]
    def add_memory(self, npc_id, record):
        """Append a memory record to an NPC."""
        npc = self._require_npc(npc_id)
        return npc.add_memory(record)

    def set_belief(self, npc_id, key, value):
        """Set a validated belief for an NPC."""
        npc = self._require_npc(npc_id)
        return npc.set_belief(key, value)

    def change_relationship(self, npc_id, name, delta):
        """Change a relationship value and return the clamped result."""
        npc = self._require_npc(npc_id)
        return npc.change_relationship(name, delta)

    def add_goal(self, npc_id, goal):
        """Add a goal to an NPC and report whether it was new."""
        npc = self._require_npc(npc_id)
        return npc.add_goal(goal)

    def remove_goal(self, npc_id, goal):
        """Remove a goal from an NPC and report whether it existed."""
        npc = self._require_npc(npc_id)
        return npc.remove_goal(goal)

    def _require_npc(self, npc_id):
        npc = self.get_npc(npc_id)
        if npc is None:
            raise ValueError(f"NPC not found: {npc_id}.")
        return npc
    def next_npc_id(self):
        """Return the next unused NPC ID such as NPC_001."""
        existing_ids = {npc.id for npc in self.npcs}
        index = 1
        while f"NPC_{index:03d}" in existing_ids:
            index += 1
        return f"NPC_{index:03d}"
    def npc_location(self, npc_id):
        """Return the schedule location for an NPC at the current time."""
        npc = self.get_npc(npc_id)
        if npc is None:
            raise ValueError(f"NPC not found: {npc_id}.")
        return location_at_time(npc.schedule, self.time)

    def npc_position(self, npc_id):
        """Return the NPC coordinates, respecting a manual override."""
        npc = self.get_npc(npc_id)
        if npc is None:
            raise ValueError(f"NPC not found: {npc_id}.")
        if npc.position_override is not None:
            return (
                float(npc.position_override[0]),
                float(npc.position_override[1]),
            )
        location = location_at_time(npc.schedule, self.time)
        return location_coordinates(location)

    def next_npc_schedule_change(self, npc_id):
        """Return the next absolute time and location an NPC changes to."""
        npc = self.get_npc(npc_id)
        if npc is None:
            raise ValueError(f"NPC not found: {npc_id}.")
        return next_schedule_change(npc.schedule, self.time)

    def _sync_npc_positions(self):
        """Refresh stored NPC positions from their schedules."""
        for npc in self.npcs:
            npc.x, npc.y = self.npc_position(npc.id)
    def get_npc(self, npc_id):
        """Return an NPC by ID, or None when it does not exist."""
        for npc in self.npcs:
            if npc.id == npc_id:
                return npc
        return None

    def observe(self, npc_id, remember=True):
        """Return what one NPC can currently see inside its vision range."""
        npc = self.get_npc(npc_id)
        if npc is None:
            raise ValueError(f"NPC not found: {npc_id}.")

        observation = {
            "time": self.time,
            "observer": npc_id,
            "observed": [],
        }

        observer_position = self.npc_position(npc_id)
        player_distance = self.distance_between(observer_position, self.player)
        if player_distance <= npc.vision_range:
            observation["observed"].append(
                {
                    "type": "player",
                    "id": "player",
                    "position": [self.player.x, self.player.y],
                    "inventory": list(self.player.inventory),
                }
            )

        for item in self.items:
            if self.distance_between(observer_position, item) <= npc.vision_range:
                observation["observed"].append(
                    {
                        "type": "item",
                        "id": item.id,
                        "position": [item.x, item.y],
                    }
                )

        for other in self.npcs:
            if other.id == npc.id:
                continue
            other_position = self.npc_position(other.id)
            if self.distance_between(observer_position, other_position) <= npc.vision_range:
                observation["observed"].append(
                    {
                        "type": "npc",
                        "id": other.id,
                        "name": other.name,
                        "position": [other_position[0], other_position[1]],
                    }
                )

        self._apply_observation_beliefs(npc, observation)

        if remember and observation["observed"]:
            npc.memory.append(observation)

        return observation

    def _apply_observation_beliefs(self, npc, observation):
        """Apply hardcoded cognitive rules from one local observation."""
        for entry in observation["observed"]:
            if entry["type"] != "player":
                continue
            for item_id in entry.get("inventory", []):
                npc.set_belief(f"player_has_{item_id}", 1.0)

    def to_dict(self):
        """Return a complete JSON-friendly world state."""
        self._sync_npc_positions()
        return {
            "map": {
                "width": MAP_WIDTH,
                "height": MAP_HEIGHT,
            },
            "time": self.time,
            "player": self.player.to_dict(),
            "npcs": [npc.to_dict() for npc in self.npcs],
            "items": [item.to_dict() for item in self.items],
            "event_history": [dict(entry) for entry in self.event_history],
            "dialogues": [dict(entry) for entry in self.dialogues],
            "quests": [quest.to_dict() for quest in self.quests],
            "history": [dict(entry) for entry in self.history],
            "story_seeds": [dict(seed) for seed in self.story_seeds],
            "characters_state": {
                npc_id: dict(state)
                for npc_id, state in self.characters_state.items()
            },
            "evidence": [dict(entry) for entry in self.evidence],
            "actions": [dict(entry) for entry in self.actions],
            "story_timeline": [dict(entry) for entry in self.story_timeline],
            "story_event_history": [dict(entry) for entry in self.story_event_history],
            "object_state": dict(self.object_state),
            "dialogue_session": _normalize_dialogue_session(self.dialogue_session),
            "phase": self.phase if self.phase in WORLD_PHASES else PHASE_DAY,
            "night_director_day": self.night_director_day,
            "night_director_report": (
                dict(self.night_director_report)
                if isinstance(self.night_director_report, dict)
                else None
            ),
            "daily_orchestration_day": self.daily_orchestration_day,
            "environment_state": {
                "terrain": {
                    name: dict(data)
                    for name, data in self.environment_state.get("terrain", {}).items()
                },
                "weather": dict(self.environment_state.get("weather", {})),
                "temporary_changes": [
                    dict(entry)
                    for entry in self.environment_state.get("temporary_changes", [])
                ],
            },
        }

    @classmethod
    def from_dict(cls, data):
        """Build a validated world state from saved JSON data."""
        map_data = data.get("map", {})
        if float(map_data.get("width", MAP_WIDTH)) != MAP_WIDTH:
            raise ValueError("Saved map width must be 100.")
        if float(map_data.get("height", MAP_HEIGHT)) != MAP_HEIGHT:
            raise ValueError("Saved map height must be 100.")

        return cls(
            time=data.get("time", 0.0),
            player=Player.from_dict(data["player"]),
            npcs=[NPC.from_dict(npc) for npc in data.get("npcs", [])],
            items=[Item.from_dict(item) for item in data.get("items", [])],
            event_history=data.get("event_history", []),
            dialogues=data.get("dialogues", []),
            quests=[Quest.from_dict(quest) for quest in data.get("quests", [])],
            history=data.get("history", []),
            story_seeds=data.get("story_seeds", []),
            characters_state=data.get("characters_state", {}),
            evidence=data.get("evidence", []),
            actions=data.get("actions", []),
            story_timeline=data.get("story_timeline", []),
            story_event_history=data.get("story_event_history", []),
            environment_state=data.get("environment_state", {}),
            object_state=data.get("object_state", {}),
            dialogue_session=data.get("dialogue_session", {}),
            phase=data.get("phase", PHASE_DAY),
            night_director_day=data.get("night_director_day"),
            night_director_report=data.get("night_director_report"),
            daily_orchestration_day=data.get("daily_orchestration_day"),
        )

    def _validate_state(self):
        npc_ids = [npc.id for npc in self.npcs]
        item_ids = [item.id for item in self.items]
        if len(npc_ids) != len(set(npc_ids)):
            raise ValueError("NPC IDs must be unique.")
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Item IDs must be unique.")

        if not isinstance(self.event_history, list):
            raise ValueError("World event_history must be a list.")
        if not isinstance(self.dialogues, list):
            raise ValueError("World dialogues must be a list.")
        if not isinstance(self.quests, list):
            raise ValueError("World quests must be a list.")
        if not isinstance(self.history, list):
            raise ValueError("World history must be a list.")
        if not isinstance(self.story_seeds, list):
            raise ValueError("World story_seeds must be a list.")
        if not isinstance(self.characters_state, dict):
            raise ValueError("World characters_state must be an object.")
        if not isinstance(self.evidence, list):
            raise ValueError("World evidence must be a list.")
        if not isinstance(self.actions, list):
            raise ValueError("World actions must be a list.")
        if not isinstance(self.story_timeline, list):
            raise ValueError("World story_timeline must be a list.")
        if not isinstance(self.story_event_history, list):
            raise ValueError("World story_event_history must be a list.")
        if not isinstance(self.environment_state, dict):
            raise ValueError("World environment_state must be an object.")
        if self.player.money < 0:
            raise ValueError("Player money must be non-negative.")
        if self.player.energy < 0:
            raise ValueError("Player energy must be non-negative.")

        _extract_coordinates(self.player)
        for npc in self.npcs:
            _extract_coordinates(npc)
            npc.vision_range = _validate_vision_range(npc.vision_range)
            if not isinstance(npc.memory, list):
                raise ValueError("NPC memory must be a list.")
            if npc.position_override is not None:
                override_x, override_y = _extract_coordinates(npc.position_override)
                npc.position_override = [override_x, override_y]
        for item in self.items:
            _extract_coordinates(item)

        for item_id in self.player.inventory:
            item = self.get_item(item_id)
            if item is None:
                raise ValueError(f"Player inventory references unknown item: {item_id}.")
            if item.holder != PLAYER_HOLDER:
                raise ValueError(f"Inventory item holder is inconsistent: {item_id}.")


def _normalize_dialogue_session(value):
    """Return {"active","npc_id","started_at"} for the active dialogue session."""
    default = {"active": False, "npc_id": None, "started_at": None}
    if not isinstance(value, dict):
        return default
    npc_id = value.get("npc_id")
    active = bool(value.get("active", False)) and isinstance(npc_id, str) and bool(npc_id)
    started_at = value.get("started_at")
    if started_at is not None:
        try:
            started_at = float(started_at)
        except (TypeError, ValueError):
            started_at = None
    if not active:
        return default
    return {"active": True, "npc_id": npc_id, "started_at": started_at}


def _validate_coordinate(value, axis, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{axis} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{axis} must be finite.")
    if not 0.0 <= number <= maximum:
        raise ValueError(f"{axis} must be between 0 and {maximum:g}.")
    return number


def _validate_time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("World time must be a number.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError("World time must be a non-negative finite number.")
    return number


def _validate_vision_range(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("NPC vision_range must be a number.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError("NPC vision_range must be a non-negative finite number.")
    return number

def _validate_duration(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("wait seconds must be a number.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError("wait seconds must be a non-negative finite number.")
    return number


def _extract_coordinates(value):
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise ValueError("Coordinate pairs must contain x and y.")
        x_value, y_value = value
    elif hasattr(value, "x") and hasattr(value, "y"):
        x_value, y_value = value.x, value.y
    else:
        raise ValueError("Expected an entity or coordinate pair.")

    return (
        _validate_coordinate(x_value, "x", MAP_WIDTH),
        _validate_coordinate(y_value, "y", MAP_HEIGHT),
    )
def _default_environment_state():
    return {
        "terrain": {
            "village": {"type": "stone", "moisture": 0.1},
            "forest": {"type": "soil", "moisture": 0.6},
            "stone_road": {"type": "stone", "moisture": 0.0},
            "town": {"type": "stone", "moisture": 0.1},
        },
        "weather": {"rain": False, "temperature": 20.0},
        "temporary_changes": [],
    }