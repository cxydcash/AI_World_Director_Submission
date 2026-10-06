"""Autonomous tick-based simulation mode for the visualization demo."""

import json
import threading

from director import Director
from event import execute_event
from main import create_demo_world
from story_engine import timeline_summary


SIMULATION_START_TIME = 3 * 3600.0
TICK_SECONDS = 1.0
HOME_POSITION = (20.0, 20.0)
FOREST_POSITION = (40.0, 40.0)


class SimulationDirector(Director):
    """State-driven director used for the offline autonomous demo."""

    def __init__(self):
        self.last_log = {}

    def generate_event(self, world, context=None):
        """Return one Event only when the current world justifies it."""
        mira = world.get_npc("npc_1")
        ren_missing = any(
            entry.get("event_id", entry.get("id")) == "ren_missing"
            and float(entry.get("timestamp", world.time)) < world.time
            for entry in world.story_event_history
        )
        already_searching = mira is not None and "search Ren" in mira.goals

        if not ren_missing or already_searching or mira is None:
            self.last_log = {
                "source": "State-driven AI Director",
                "decision": "no_event",
                "reason": (
                    "Ren is not missing yet."
                    if not ren_missing
                    else "Mira is already searching for Ren."
                ),
            }
            return None

        event = {
            "event_id": f"simulation_mira_search_{int(world.time)}",
            "time": world.time,
            "effects": [
                {
                    "type": "add_goal",
                    "npc_id": "npc_1",
                    "goal": "search Ren",
                },
                {
                    "type": "set_belief",
                    "npc_id": "npc_1",
                    "key": "ren_may_be_in_danger",
                    "value": 0.85,
                },
                {
                    "type": "add_memory",
                    "npc_id": "npc_1",
                    "content": "Ren has lost contact in the forest.",
                },
                {
                    "type": "dialogue",
                    "speaker_id": "npc_1",
                    "content": "I need to find Ren before the weather gets worse.",
                },
            ],
        }
        self.last_log = {
            "source": "State-driven AI Director",
            "decision": "generated_event",
            "reason": "Ren is missing and Mira has no search goal.",
            "event_id": event["event_id"],
        }
        return event


class SimulationEngine:
    """Advance an autonomous world one state-driven tick at a time."""

    def __init__(self, director=None, tick_seconds=TICK_SECONDS, start_time=SIMULATION_START_TIME):
        self.director = director or SimulationDirector()
        self.tick_seconds = float(tick_seconds)
        self.start_time = float(start_time)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._running = False
        self.reset()

    def reset(self):
        """Restore the fixed autonomous demo starting state."""
        if getattr(self, '_initialized', False):
            self.pause()
        else:
            self._initialized = True
        with self._lock:
            world = create_demo_world()
            world.time = self.start_time
            world.environment_state["weather"] = {
                "rain": False,
                "temperature": 20.0,
            }
            world.environment_state["temporary_changes"] = []

            mira = world.get_npc("npc_1")
            if mira is not None:
                mira.goals = ["wait_home"]
                mira.position_override = list(HOME_POSITION)

            ren = world.get_npc("npc_2")
            if ren is not None:
                ren.goals = ["collect herbs"]
                ren.position_override = list(FOREST_POSITION)

            ivy = world.get_npc("npc_3")
            if ivy is not None:
                ivy.goals = ["rest"]
                ivy.position_override = list((70.0, 70.0))

            for entry in world.story_timeline:
                if entry["id"] == "ren_missing":
                    entry["trigger_time"] = self.start_time + 4.0
                    entry["status"] = "pending"

            world.story_event_history = []
            world.event_history = []
            world.history = []

            self.world = world
            self.tick_index = 0
            self.npc_actions = {
                "npc_1": "waiting at home",
                "npc_2": "preparing to collect herbs",
                "npc_3": "watching the village",
            }
            self.last_director_event = None
            self.last_director_log = {
                "source": "State-driven AI Director",
                "decision": "not_checked",
                "reason": "Simulation reset.",
            }
            self.last_story_events = []
            self.last_error = None
            self._last_event = {
                "time": world.time,
                "type": "simulation_reset",
                "description": "Autonomous simulation reset to its Day 1 starting state.",
            }
        return self.current()

    def tick(self):
        """Recalculate and apply one autonomous simulation tick."""
        with self._lock:
            before_story_history = len(self.world.story_event_history)
            self.world.advance_time(TICK_SECONDS)

            contact_changed = self._update_contact_state()
            weather_changed = self._update_weather()
            self._apply_goal_actions()

            self.tick_index += 1
            self.last_story_events = self._detect_events(before_story_history)
            director_event = self._call_director()
            self._apply_goal_actions()

            description = self._describe_tick(
                contact_changed=contact_changed,
                weather_changed=weather_changed,
                story_events=self.last_story_events,
                director_event=director_event,
            )
            self._last_event = {
                "time": self.world.time,
                "type": "simulation_tick",
                "description": description,
                "tick": self.tick_index,
                "story_events": [entry["event_id"] for entry in self.last_story_events],
                "director_event": director_event,
            }
        return self.current()

    def start(self, interval=None):
        """Start autonomous ticking in a background daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return self.current()

        interval = self.tick_seconds if interval is None else float(interval)
        self._stop_event.clear()
        with self._lock:
            self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="ai-world-simulation",
            daemon=True,
        )
        self._thread.start()
        return self.current()

    def pause(self):
        """Stop autonomous ticking without resetting the world."""
        self._stop_event.set()
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=max(2.0, self.tick_seconds * 2.0))
        with self._lock:
            self._running = False
            self._thread = None
        return self.current()

    def current(self):
        """Return the latest event and a fresh snapshot of current state."""
        with self._lock:
            return {
                "event": dict(self._last_event),
                "snapshot": self.snapshot(),
            }

    def snapshot(self):
        """Return a visualization-friendly snapshot without changing the world."""
        with self._lock:
            world = self.world
            return {
                "time": world.time,
                "player": {
                    "position": [world.player.x, world.player.y],
                    "inventory": list(world.player.inventory),
                    "money": world.player.money,
                    "energy": world.player.energy,
                },
                "npcs": [
                    {
                        "id": npc.id,
                        "name": npc.name,
                        "position": list(world.npc_position(npc.id)),
                        "beliefs": dict(npc.beliefs),
                        "relationships": dict(npc.relationships),
                        "goals": list(npc.goals),
                        "memory": [dict(entry) for entry in npc.memory[-5:]],
                        "personality": list(npc.personality),
                        "character_seed": dict(npc.character_seed),
                        "current_action": self.npc_actions.get(npc.id, "idle"),
                        "status": dict(world.characters_state.get(npc.id, {})),
                    }
                    for npc in world.npcs
                ],
                "items": [
                    {
                        "id": item.id,
                        "type": item.type,
                        "position": [item.x, item.y],
                        "holder": item.holder,
                    }
                    for item in world.items
                ],
                "evidence": [dict(entry) for entry in world.evidence],
                "environment": json.loads(json.dumps(world.environment_state)),
                "timeline": timeline_summary(world),
                "history": [dict(entry) for entry in world.history[-12:]],
                "event_history": [dict(entry) for entry in world.event_history],
                "story_event_history": [
                    dict(entry) for entry in world.story_event_history
                ],
                "simulation": {
                    "tick": self.tick_index,
                    "running": self._running,
                    "mode": "Simulation",
                    "npc_actions": dict(self.npc_actions),
                    "director": dict(self.last_director_log),
                    "last_director_event": (
                        dict(self.last_director_event)
                        if self.last_director_event is not None
                        else None
                    ),
                    "last_story_events": [dict(entry) for entry in self.last_story_events],
                    "error": self.last_error,
                },
            }

    def _run_loop(self, interval):
        while not self._stop_event.is_set():
            self.tick()
            if self._stop_event.wait(interval):
                break
        with self._lock:
            self._running = False

    def _update_contact_state(self):
        ren_state = self.world.characters_state.setdefault("npc_2", {})
        if self.world.time < self.start_time + 3.0:
            return False
        if ren_state.get("in_contact", True) is False:
            return False

        ren_state["in_contact"] = False
        mira = self.world.get_npc("npc_1")
        if mira is not None:
            mira.add_memory(
                {
                    "time": self.world.time,
                    "source": "simulation",
                    "content": "Ren stopped responding from the forest.",
                }
            )
        self.world.add_history(
            "ren_lost_contact",
            "npc_2",
            "Ren lost contact while collecting herbs in the forest.",
            actor="simulation",
        )
        return True

    def _update_weather(self):
        weather = self.world.environment_state.setdefault("weather", {})
        if self.world.time >= self.start_time + 2.0 and not weather.get("rain", False):
            weather["rain"] = True
            weather["temperature"] = 14.0
            self.world.add_history(
                "weather_changed",
                "forest",
                "Rain began over the forest.",
                actor="environment",
            )
            return True
        if self.world.time >= self.start_time + 30.0 and weather.get("rain", False):
            weather["rain"] = False
            weather["temperature"] = 18.0
            self.world.add_history(
                "weather_changed",
                "forest",
                "The rain stopped.",
                actor="environment",
            )
            return True
        return False

    def _apply_goal_actions(self):
        ren = self.world.get_npc("npc_2")
        if ren is not None:
            if "collect herbs" in ren.goals:
                action = "collect herbs"
            else:
                action = ren.goals[0] if ren.goals else "idle"
            self._set_npc_action("npc_2", action)

        mira = self.world.get_npc("npc_1")
        if mira is not None:
            if "search Ren" in mira.goals:
                self._move_toward(mira, FOREST_POSITION, step=2.5)
                distance = self.world.distance_between(
                    self.world.npc_position("npc_1"), FOREST_POSITION
                )
                action = (
                    "searching the forest for Ren"
                    if distance <= 2.5
                    else "searching for Ren"
                )
            elif "wait_home" in mira.goals:
                mira.position_override = list(HOME_POSITION)
                action = "waiting at home"
            else:
                action = mira.goals[0] if mira.goals else "idle"
            self._set_npc_action("npc_1", action)

        ivy = self.world.get_npc("npc_3")
        if ivy is not None:
            weather = self.world.environment_state.get("weather", {})
            action = "sheltering from rain" if weather.get("rain") else "watching the village"
            self._set_npc_action("npc_3", action)

    def _set_npc_action(self, npc_id, action):
        previous = self.npc_actions.get(npc_id)
        self.npc_actions[npc_id] = action
        if previous != action:
            self.world.add_history(
                "npc_action_changed",
                npc_id,
                f"{npc_id} is now {action}.",
                actor=npc_id,
            )

    def _move_toward(self, npc, target, step):
        current = self.world.npc_position(npc.id)
        dx = target[0] - current[0]
        dy = target[1] - current[1]
        distance = (dx * dx + dy * dy) ** 0.5
        if distance == 0:
            return
        scale = min(1.0, step / distance)
        npc.position_override = [
            current[0] + dx * scale,
            current[1] + dy * scale,
        ]
        npc.x, npc.y = npc.position_override

    def _detect_events(self, before_story_history):
        return [
            dict(entry)
            for entry in self.world.story_event_history[before_story_history:]
        ]

    def _call_director(self):
        self.last_error = None
        try:
            event = self.director.generate_event(
                self.world,
                context={
                    "mode": "autonomous_simulation",
                    "tick": self.tick_index,
                },
            )
        except Exception as exc:
            self.last_error = str(exc)
            self.last_director_log = {
                "source": "Director",
                "decision": "error",
                "reason": str(exc),
            }
            return None

        if event is None:
            fallback_log = getattr(self.director, "last_log", None)
            self.last_director_log = (
                dict(fallback_log)
                if isinstance(fallback_log, dict)
                else {
                    "source": "Director",
                    "decision": "no_event",
                    "reason": "No event was necessary.",
                }
            )
            return None

        try:
            executed = execute_event(self.world, event)
        except Exception as exc:
            self.last_error = str(exc)
            self.last_director_log = {
                "source": "Director",
                "decision": "rejected",
                "reason": str(exc),
            }
            return None

        self.last_director_event = executed
        fallback_log = getattr(self.director, "last_log", None)
        self.last_director_log = (
            dict(fallback_log)
            if isinstance(fallback_log, dict)
            else {
                "source": "Director",
                "decision": "generated_event",
                "reason": "Event validated and executed.",
            }
        )
        self.world.add_history(
            "ai_director_event",
            event["event_id"],
            "AI Director generated an autonomous world event.",
            actor="director",
        )
        return executed

    def _describe_tick(self, contact_changed, weather_changed, story_events, director_event):
        if story_events:
            event_id = story_events[0].get("event_id", "story event")
            return f"Event detector created: {event_id}."
        if director_event is not None:
            return "AI Director generated: Mira starts searching for Ren."
        if contact_changed:
            return "Ren loses contact with the village."
        if weather_changed:
            return "Weather changes: rain begins over the forest."
        if self.tick_index == 1:
            return "Ren enters the forest to collect herbs."
        return "World evolved automatically."



