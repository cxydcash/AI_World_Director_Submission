"""Interactive autonomous simulation with safe player actions."""

from dialogue_system import DialogueSystem
from player_controller import PlayerController
from player_intent import PlayerIntentParser
from simulation_engine import SimulationEngine


class InteractiveSimulationEngine:
    """Combine autonomous ticks with player input and safe action execution."""

    def __init__(self, director=None, tick_seconds=1.0, intent_parser=None, dialogue_system=None):
        self.simulation = SimulationEngine(
            director=director,
            tick_seconds=tick_seconds,
        )
        self.controller = PlayerController()
        self.intent_parser = intent_parser or PlayerIntentParser()
        self.dialogue_system = dialogue_system or DialogueSystem()
        self.reset()

    @property
    def world(self):
        """Expose the world for read-only adapters and tests."""
        return self.simulation.world

    def reset(self):
        """Reset the world and return to a paused interactive state."""
        self.simulation.reset()
        self.input_paused = False
        self.auto_running = False
        self._resume_after_input = False
        self.last_candidates = []
        self.last_dialogue_reply = None
        self.dialogue_log = []
        self.last_player_action = None
        self.last_error = None
        self._last_event = dict(self.simulation.current()["event"])
        return self.current()

    def start(self, interval=None):
        """Start automatic world evolution."""
        self.auto_running = True
        self.input_paused = False
        self._resume_after_input = False
        self.simulation.start(interval=interval)
        return self.current()

    def pause(self):
        """Pause autonomous evolution explicitly."""
        self.auto_running = False
        self.input_paused = False
        self._resume_after_input = False
        self.simulation.pause()
        return self.current()

    def pause_for_input(self):
        """Pause the world while the player is thinking or typing."""
        if not self.input_paused:
            running = self.simulation.snapshot()["simulation"]["running"]
            self._resume_after_input = self.auto_running or running
            self.simulation.pause()
            self.input_paused = True
        return self.current()

    def resume_after_input(self):
        """Resume auto ticks if the world was running before input."""
        should_resume = self._resume_after_input
        self.input_paused = False
        self._resume_after_input = False
        if should_resume:
            self.auto_running = True
            self.simulation.start()
        return self.current()

    def tick(self):
        """Advance one autonomous tick and expose the resulting state."""
        step = self.simulation.tick()
        self._last_event = dict(step["event"])
        return self.current()

    def move(self, direction):
        """Move the player relative to their current position."""
        self.pause_for_input()
        success, message, parsed = self.controller.move(self.world, direction)
        return self._finish_action(success, message, parsed, f"move {direction}")

    def execute_action(self, action, raw_text=None):
        """Validate and execute one structured player Action."""
        self.pause_for_input()
        success, message, parsed = self.controller.execute_action(
            self.world, action, raw_text
        )
        return self._finish_action(success, message, parsed, raw_text or "action")

    def analyze_thought(self, text):
        """Convert player thoughts into candidate Actions without executing them."""
        self.pause_for_input()
        candidates = self.intent_parser.parse_candidates(text, self.world)
        self.last_candidates = candidates
        self.last_error = None if candidates else "No safe Action candidates found."
        self._last_event = {
            "time": self.world.time,
            "type": "intent_analysis",
            "description": (
                f"AI proposed {len(candidates)} possible Actions."
                if candidates
                else "AI found no safe Action candidates."
            ),
            "input": text,
        }
        return {
            "success": bool(candidates),
            "message": self.last_error or "Candidate Actions generated.",
            "candidates": list(candidates),
            "step": self.current(),
        }

    def execute_candidate(self, candidate_id):
        """Execute one previously proposed candidate after player confirmation."""
        candidate = next(
            (item for item in self.last_candidates if item["id"] == candidate_id),
            None,
        )
        if candidate is None:
            self.last_error = f"Unknown candidate: {candidate_id}."
            return {
                "success": False,
                "error": self.last_error,
                "step": self.current(),
            }
        result = self.execute_action(
            candidate["action"],
            raw_text=candidate["label"],
        )
        result["candidate"] = candidate
        return result

    def talk(self, npc_id, message):
        """Execute the existing talk Action and generate a state-grounded reply."""
        self.pause_for_input()
        success, action_message, parsed = self.controller.talk(
            self.world, npc_id, message
        )
        reply = None
        if success:
            try:
                reply = self.dialogue_system.generate_reply(
                    self.world, npc_id, message
                )
            except ValueError as exc:
                success = False
                action_message = str(exc)
        self._finish_action(success, action_message, parsed, f"talk {npc_id}")
        if success:
            self.last_dialogue_reply = reply
            npc = self.world.get_npc(npc_id)
            self.dialogue_log.append(
                {
                    "time": self.world.time,
                    "player": message,
                    "npc_id": npc_id,
                    "npc": npc.name if npc is not None else npc_id,
                    "reply": reply,
                }
            )
            self._last_event["dialogue_reply"] = reply
        result = {
            "success": success,
            "message": action_message,
            "reply": reply,
            "step": self.current(),
        }
        return result

    def snapshot(self):
        """Return one shared visualization snapshot plus player-local knowledge."""
        snapshot = self.simulation.snapshot()
        snapshot["observation"] = self.controller.observe(self.world)
        snapshot["interactive"] = {
            "input_paused": self.input_paused,
            "auto_running": self.auto_running,
            "resume_pending": self._resume_after_input,
            "candidates": [dict(item) for item in self.last_candidates],
            "last_dialogue_reply": self.last_dialogue_reply,
            "dialogue_log": [dict(item) for item in self.dialogue_log[-8:]],
            "last_player_action": (
                dict(self.last_player_action)
                if self.last_player_action is not None
                else None
            ),
            "error": self.last_error,
        }
        return snapshot

    def current(self):
        """Return the current event and a visual snapshot."""
        return {
            "event": dict(self._last_event),
            "snapshot": self.snapshot(),
        }

    def _finish_action(self, success, message, parsed, raw_text):
        self.last_error = None if success else message
        if success:
            self.last_player_action = {
                "time": self.world.time,
                "action": parsed,
                "raw_text": raw_text,
                "message": message,
            }
            self._last_event = {
                "time": self.world.time,
                "type": "player_action",
                "description": message,
                "action": parsed,
            }
            self.resume_after_input()
        return {
            "success": success,
            "message": message,
            "action": parsed,
            "error": None if success else message,
            "step": self.current(),
        }
