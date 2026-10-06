"""LLM-backed natural-language to safe Action parser."""

import json
import os
import re
import urllib.error
import urllib.request

from director import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, DEFAULT_LLM_TIMEOUT, load_environment
from world import EXPLORE_LOCATIONS


ALLOWED_ACTION_TYPES = {
    "move",
    "talk",
    "create_evidence",
    "give_item",
    "hide_item",
    "create_rumor",
}

PARSER_SYSTEM_PROMPT = """You convert a player's natural-language action into one safe game Action.

Return only one JSON object. Do not output Python, markdown, or explanation.
Only use one of these action types:
- create_evidence: fields evidence_type, location, description, optional target_belief, optional water_applied boolean
- give_item: fields item_id, target
- hide_item: fields item_id, location
- create_rumor: fields target, content

Rules:
- Never invent item IDs or NPC IDs. Use only IDs present in the input.
- location must be a known location name from the input or a coordinate pair [x, y].
- Do not create locations, destroy the world, change code, or use unknown action types.
- Deception is allowed when represented as evidence or rumor. It must not alter
  world truth directly. For example, forging footprints pointing north should
  become create_evidence with evidence_type "footprint" and a description such
  as "footprints heading north".
- If the player intent cannot be represented safely, return:
  {"error": "short reason"}.
"""


class PlayerIntentError(ValueError):
    """Raised when natural language cannot be converted safely."""


class PlayerActionParser:
    """Call an OpenAI-compatible model and validate its structured action."""

    def __init__(self, api_key=None, model=None, base_url=None, timeout=None):
        load_environment()
        self.api_key = os.getenv("LLM_API_KEY") if api_key is None else api_key
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.base_url = (
            base_url or os.getenv("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL
        ).rstrip("/")
        self.timeout = timeout or DEFAULT_LLM_TIMEOUT

    def parse(self, text, world):
        if not isinstance(text, str) or not text.strip():
            raise PlayerIntentError("Player intent text must be non-empty.")

        payload = {
            "text": text,
            "time": world.time,
            "player": {
                "position": [world.player.x, world.player.y],
                "inventory": list(world.player.inventory),
            },
            "npcs": [
                {"id": npc.id, "name": npc.name, "position": list(world.npc_position(npc.id))}
                for npc in world.npcs
            ],
            "items": [
                {"id": item.id, "type": item.type, "holder": item.holder}
                for item in world.items
            ],
            "locations": {
                name: list(coordinates)
                for name, coordinates in EXPLORE_LOCATIONS.items()
            },
        }

        raw_text = self._request_json(payload)
        try:
            action = json.loads(raw_text)
        except json.JSONDecodeError:
            raise PlayerIntentError("LLM returned invalid JSON.") from None
        return validate_parsed_action(world, action)

    def _request_json(self, payload):
        if not self.api_key:
            raise PlayerIntentError("LLM_API_KEY is not set.")

        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": PARSER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise PlayerIntentError(
                f"LLM API request failed with HTTP {exc.code}."
            ) from None
        except urllib.error.URLError:
            raise PlayerIntentError("LLM API request failed.") from None

        try:
            content = response_body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise PlayerIntentError("LLM API returned an invalid response.") from None
        if isinstance(content, dict):
            return json.dumps(content)
        if not isinstance(content, str):
            raise PlayerIntentError("LLM API returned non-text content.")
        return content


def parse_player_intent(text, world):
    """Parse natural language into one validated safe Action."""
    return PlayerActionParser().parse(text, world)


def parse_player_action_input(text, world):
    """Parse a local command or fall back to the LLM intent parser."""
    text = text.strip()
    move_match = re.fullmatch(
        r"move\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    if move_match:
        return validate_parsed_action(
            world,
            {
                "type": "move",
                "x": float(move_match.group(1)),
                "y": float(move_match.group(2)),
            },
        )

    talk_match = re.fullmatch(
        r"talk\s+(\S+)(?:\s+(.*))?",
        text,
        flags=re.IGNORECASE,
    )
    if talk_match:
        return validate_parsed_action(
            world,
            {
                "type": "talk",
                "npc_id": talk_match.group(1),
                "text": (talk_match.group(2) or "Hello.").strip(),
            },
        )

    if text.lower().startswith("create evidence"):
        description = text[len("create evidence"):].strip() or "suspicious evidence"
        return validate_parsed_action(
            world,
            {
                "type": "create_evidence",
                "evidence_type": "footprint",
                "location": [world.player.x, world.player.y],
                "description": description,
                "target_belief": description,
                "water_applied": False,
            },
        )

    return parse_player_intent(text, world)

def validate_parsed_action(world, action):
    """Validate a parser-proposed Action against the current world."""
    if not isinstance(action, dict):
        raise PlayerIntentError("Parsed action must be an object.")
    if "error" in action:
        raise PlayerIntentError(str(action["error"]))
    action_type = action.get("type")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise PlayerIntentError(f"Unsupported action type: {action_type}.")

    if action_type == "move":
        _reject_unknown(action, {"type", "x", "y"})
        return {
            "type": "move",
            "x": _coordinate(action, "x"),
            "y": _coordinate(action, "y"),
        }

    if action_type == "talk":
        _reject_unknown(action, {"type", "npc_id", "text"})
        npc_id = _string(action, "npc_id")
        if world.get_npc(npc_id) is None:
            raise PlayerIntentError(f"NPC not found: {npc_id}.")
        return {
            "type": "talk",
            "npc_id": npc_id,
            "text": str(action.get("text", "Hello.")),
        }

    if action_type == "create_evidence":
        allowed = {"type", "evidence_type", "location", "description", "target_belief", "water_applied"}
        _reject_unknown(action, allowed)
        return {
            "type": "create_evidence",
            "evidence_type": _string(action, "evidence_type"),
            "location": _location(action, "location"),
            "description": _string(action, "description"),
            "target_belief": str(action.get("target_belief", "")),
            "water_applied": bool(action.get("water_applied", False)),
        }

    if action_type == "give_item":
        _reject_unknown(action, {"type", "item_id", "target"})
        item_id = _string(action, "item_id")
        target = _string(action, "target")
        if item_id not in world.player.inventory:
            raise PlayerIntentError(f"Item is not in player inventory: {item_id}.")
        if world.get_npc(target) is None:
            raise PlayerIntentError(f"NPC not found: {target}.")
        return {"type": "give_item", "item_id": item_id, "target": target}

    if action_type == "hide_item":
        _reject_unknown(action, {"type", "item_id", "location"})
        item_id = _string(action, "item_id")
        if item_id not in world.player.inventory:
            raise PlayerIntentError(f"Item is not in player inventory: {item_id}.")
        return {
            "type": "hide_item",
            "item_id": item_id,
            "location": _location(action, "location"),
        }

    _reject_unknown(action, {"type", "target", "content"})
    target = _string(action, "target")
    if world.get_npc(target) is None:
        raise PlayerIntentError(f"NPC not found: {target}.")
    return {
        "type": "create_rumor",
        "target": target,
        "content": _string(action, "content"),
    }


def _coordinate(action, field):
    value = action.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlayerIntentError(f"{field} must be a number.")
    number = float(value)
    if not 0.0 <= number <= 100.0:
        raise PlayerIntentError(f"{field} must be between 0 and 100.")
    return number

def _reject_unknown(action, allowed):
    unknown = set(action) - allowed
    if unknown:
        raise PlayerIntentError(f"Unknown action fields: {sorted(unknown)}.")


def _string(action, field):
    value = action.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PlayerIntentError(f"{field} must be a non-empty string.")
    return value


def _location(action, field):
    value = action.get(field)
    if isinstance(value, str):
        if value not in EXPLORE_LOCATIONS:
            raise PlayerIntentError(f"Unknown location: {value}.")
        return value
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            x = float(value[0])
            y = float(value[1])
        except (TypeError, ValueError):
            raise PlayerIntentError("Location coordinates must be numbers.") from None
        if not 0.0 <= x <= 100.0 or not 0.0 <= y <= 100.0:
            raise PlayerIntentError("Location coordinates must be between 0 and 100.")
        return [x, y]
    raise PlayerIntentError("Location must be a known name or coordinate pair.")
