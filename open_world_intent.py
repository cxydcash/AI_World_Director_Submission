"""Intent-to-Action planning for the open world, with optional LLM."""

import json
import os
import urllib.error
import urllib.request

from director import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, DEFAULT_LLM_TIMEOUT, load_environment


ALLOWED_ACTION_TYPES = {
    "search_object",
    "inspect_object",
    "take_object",
    "follow_object",
    "follow_npc",
    "talk",
    "wait",
    "move",
    "break_object",
    "open_drawer",
    "protect_ren",
    "take_ledger",
    "ask_for_hammer",
    "npc_follow_player",
    "free_action",
}


class OpenWorldIntentParser:
    """Propose contextual candidates from nearby objects and NPC state."""

    def __init__(self, api_key=None, model=None, base_url=None, timeout=None):
        load_environment()
        self.api_key = os.getenv("LLM_API_KEY") if api_key is None else api_key
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL).rstrip("/")
        self.timeout = timeout or DEFAULT_LLM_TIMEOUT
        self.last_source = "rule"
        self.last_llm_error = None
        self.last_llm_raw = None
        self.last_llm_reason = None

    def parse_candidates(self, text, world, nearby_objects=None, nearby_npcs=None):
        nearby_objects = nearby_objects or []
        nearby_npcs = nearby_npcs or []
        if self.api_key:
            llm_candidates = self._llm_candidates(text, world, nearby_objects, nearby_npcs)
            if llm_candidates:
                self.last_source = "LLM"
                return llm_candidates
        self.last_source = "rule"
        self.last_llm_error = None
        self.last_llm_raw = None
        self.last_llm_reason = None
        return self._rule_candidates(text, world, nearby_objects, nearby_npcs)

    def _llm_candidates(self, text, world, nearby_objects, nearby_npcs):
        allowed_object_ids = [obj["id"] for obj in nearby_objects]
        allowed_npc_ids = [npc["id"] for npc in nearby_npcs]
        system = (
            "你是开放世界中的 AI 行动规划器。玩家用自然语言表达想法，"
            "你根据玩家当前位置、附近对象和附近 NPC，给出少数几个最合理的候选动作。"
            "不要给与当前位置无关的动作。必须返回 JSON，格式严格如下："
            '{"actions":[{"label":"动作名","action":{"type":"break_object","object_id":"mayor_desk_lock"},'
            '"reason":"为什么","risk":"low","possible_outcome":"可能的结果"}],"reason":"如果没有可行动作，用中文说明原因"}'
            "。type 可以是 break_object、search_object、inspect_object、take_object、follow_object、"
            "follow_npc、talk、wait、open_drawer、take_ledger、protect_ren、ask_for_hammer、npc_follow_player，"
            "也可以用 free_action 表达任意玩家想做的事，例如放风筝、唱歌、躲藏、布置陷阱。"
            "free_action 必须包含 verb 和 description 字段。"
            "对象和 NPC 必须来自输入。"
        )
        context = {
            "player_thought": text,
            "location": world.current_explore_location() or "open_ground",
            "nearby_objects": [
                {"id": obj["id"], "description": obj["description"]} for obj in nearby_objects
            ],
            "nearby_npcs": [
                {"id": npc["id"], "name": npc["name"], "goals": npc["goals"]} for npc in nearby_npcs
            ],
            "player_memory": [str(m.get("content", "")) for m in world.player.memory[-5:]],
        }
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "temperature": 0.4,
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
                body = json.loads(response.read().decode("utf-8"))
            raw = body["choices"][0]["message"]["content"]
            if isinstance(raw, str):
                raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                parsed = {"actions": parsed}
            self.last_llm_raw = parsed
            if isinstance(parsed, dict):
                self.last_llm_reason = parsed.get("reason")
            return self._validate_llm_candidates(parsed, allowed_object_ids, allowed_npc_ids)
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            self.last_llm_error = str(exc)
            return []

    def _validate_llm_candidates(self, parsed, allowed_object_ids, allowed_npc_ids):
        if not isinstance(parsed, dict):
            return []
        actions = parsed.get("actions")
        if not isinstance(actions, list):
            return []
        candidates = []
        seen = set()
        for index, item in enumerate(actions[:6]):
            if not isinstance(item, dict):
                continue
            action = dict(item.get("action", {}))
            action_type = action.get("type")
            if action_type not in ALLOWED_ACTION_TYPES:
                continue
            if action_type == "free_action":
                if not isinstance(action.get("verb"), str) or not action.get("verb", "").strip():
                    continue
                if not isinstance(action.get("description"), str) or not action.get("description", "").strip():
                    continue
            object_id = action.get("object_id")
            npc_id = action.get("npc_id")
            if object_id and object_id not in allowed_object_ids:
                continue
            if npc_id and npc_id not in allowed_npc_ids:
                continue
            signature = json.dumps(action, sort_keys=True)
            if signature in seen:
                continue
            seen.add(signature)
            candidates.append(
                {
                    "id": f"llm_{index + 1}",
                    "label": str(item.get("label") or action_type),
                    "action": action,
                    "reason": str(item.get("reason") or ""),
                    "risk": str(item.get("risk") or "medium"),
                    "possible_outcome": str(item.get("possible_outcome") or ""),
                    "signature": signature,
                }
            )
        return candidates

    def _rule_candidates(self, text, world, nearby_objects, nearby_npcs):
        lowered = text.lower()
        candidates = []

        def add(label, action, reason, risk="medium", possible_outcome=""):
            signature = json.dumps(action, sort_keys=True)
            if any(item["signature"] == signature for item in candidates):
                return
            candidates.append(
                {
                    "id": f"candidate_{len(candidates) + 1}",
                    "label": label,
                    "action": action,
                    "reason": reason,
                    "risk": risk,
                    "possible_outcome": possible_outcome,
                    "signature": signature,
                }
            )

        investigate = any(
            token in lowered
            for token in ("调查", "查看", "看看", "搜索", "检查", "investigate", "search", "inspect", "这里", "here")
        )

        for obj in nearby_objects:
            oid = obj["id"]
            if "desk" in oid:
                add("Search the desk", {"type": "search_object", "object_id": "mayor_desk"}, "The desk may hide documents or a drawer.", "medium", "Mayor suspicion +0.2; may reveal hidden drawer.")
                add("Inspect the bookshelf", {"type": "inspect_object", "object_id": "mayor_bookshelf"}, "Old ledgers may contain altered records.", "low", "Find altered records evidence.")
                add("Look for hidden documents", {"type": "search_object", "object_id": "mayor_hidden_drawer"}, "The desk may have a hidden drawer.", "high", "May reveal the hidden ledger.")
            if "bookshelf" in oid:
                add("Inspect the bookshelf", {"type": "inspect_object", "object_id": "mayor_bookshelf"}, "Old documents may show changed accounts.", "low", "Find altered records evidence.")
            if "lock" in oid:
                add("Break the desk lock", {"type": "break_object", "object_id": "mayor_desk_lock"}, "Breaking the lock may open the hidden drawer.", "high", "Success may open the drawer; failure alerts the mayor.")
            if oid == "ledger":
                add("Take the hidden ledger", {"type": "take_object", "object_id": "ledger"}, "The ledger is the key evidence.", "high", "Mayor suspicion +0.3; mayor searches player.")
            if oid == "footprints":
                add("Follow the footprints", {"type": "follow_object", "object_id": "footprints"}, "Footprints may lead to Ren.", "medium", "Discover Ren's route.")
            if oid == "dropped_note":
                add("Pick up the dropped note", {"type": "take_object", "object_id": "dropped_note"}, "The note may explain what Ren found.", "low", "Reveal Ren's note.")

        for npc in nearby_npcs:
            add(f"Talk to {npc['name']}", {"type": "talk", "npc_id": npc["id"], "text": text}, "Gather information from this NPC.", "low", "Learn NPC beliefs and goals.")
            add(f"Follow {npc['name']}", {"type": "follow_npc", "npc_id": npc["id"]}, "Observe the NPC without acting.", "medium", "May reveal the NPC's next destination.")
            if any(token in lowered for token in ("跟着我", "跟随我", "跟我走", "follow me", "come with me")):
                add(f"让 {npc['name']} 跟着你", {"type": "npc_follow_player", "npc_id": npc["id"]}, "你邀请 NPC 和你一起行动。", "medium", "NPC 会开始跟随并移动。")

        if investigate and not candidates:
            add("Search the current location", {"type": "search_object", "object_id": "__location__"}, "Look for anything hidden here.", "low", "May reveal hidden objects or evidence.")

        if not candidates and nearby_objects:
            first = nearby_objects[0]
            add(f"Observe {first['id'].replace('_', ' ')}", {"type": "inspect_object", "object_id": first["id"]}, "Start with the nearest visible object.", "low", "Gain local information.")

        if not candidates and nearby_npcs:
            npc = nearby_npcs[0]
            add(f"Talk to {npc['name']}", {"type": "talk", "npc_id": npc["id"], "text": text}, "Talking is a safe first action.", "low", "Learn what the NPC knows.")

        if not candidates and text.strip():
            add(
                f"尝试：{text.strip()}",
                {"type": "free_action", "verb": "custom", "description": text.strip()},
                "这是玩家想做的自由动作，由 AI 判断是否成功。",
                "medium",
                "成功或失败由 AI 根据环境、物品和 NPC 状态判断。",
            )

        return candidates
