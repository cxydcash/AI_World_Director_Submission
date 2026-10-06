"""Unified intent layer for Dialogue, Thought, and free-action input.

This module resolves *what the player wants*. It never validates, executes, or
mutates WorldState.
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from conversation_memory import ConversationMemoryManager
from director import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, DEFAULT_LLM_TIMEOUT, load_environment


INTENT_TYPES = {
    "move",
    "inspect",
    "search",
    "take",
    "drop",
    "open",
    "talk",
    "follow",
    "stop_follow",
    "ask",
    "investigate",
    "give",
    "buy",
    "work",
    "explore",
    "wait",
    "attack",
    "rescue",
    "hide",
    "create_rumor",
    "conversation_only",
    "unknown",
}

INTENT_SOURCES = {"dialogue", "thought", "free_action"}


FOLLOW_COMMAND_PATTERNS = (
    "跟着我",
    "跟我走",
    "跟我来",
    "跟我一起走",
    "你跟我走",
    "你跟我来",
    "从现在开始跟着我",
    "follow me",
    "come with me",
)

ACCOMPANY_REQUEST_MARKERS = (
    "我能跟",
    "我能和",
    "我能陪",
    "我可以跟",
    "我可以和",
    "我可以陪",
    "能让我跟",
    "让我跟",
    "我想跟你",
    "我想和你",
    "我想陪",
    "我跟你一起",
    "我和你一起",
    "陪你",
    "一起去",
    "跟你去",
    "和你去",
    "跟你一起",
    "和你一起",
    "愿意",
    "愿不愿意",
    "方便吗",
    "方便不",
    "can i come",
    "can i join",
    "may i join",
    "could i",
    "can we",
    "would you",
)


def is_accompany_request(text):
    """True when the player asks/offers to join the NPC (not an order)."""
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in ACCOMPANY_REQUEST_MARKERS)


def is_explicit_follow_command(text):
    """True only for an explicit "NPC, follow me" order.

    "跟着我" is a follow order (the NPC follows the player). "我能跟你一起去吗"
    is a request to join the NPC, so it must NOT become follow.
    """
    lowered = str(text or "").strip().lower()
    if not lowered or is_accompany_request(lowered):
        return False
    if any(pattern in lowered for pattern in FOLLOW_COMMAND_PATTERNS):
        return True
    if "跟我去" in lowered and not lowered.endswith(("吗", "?", "？")):
        return True
    return False


@dataclass
class Intent:
    """Canonical, non-executing representation of player intention."""

    source: str
    actor_id: str
    target_id: str | None
    intent_type: str
    parameters: dict = field(default_factory=dict)
    raw_text: str = ""
    confidence: float = 0.0
    context: dict = field(default_factory=dict)
    reason: str = ""

    def to_dict(self):
        return {
            "source": self.source,
            "actor_id": self.actor_id,
            "target_id": self.target_id,
            "intent_type": self.intent_type,
            "parameters": dict(self.parameters),
            "raw_text": self.raw_text,
            "confidence": self.confidence,
            "context": dict(self.context),
            "reason": self.reason,
        }


class IntentResolver:
    """Resolve natural language into a structured Intent.

    LLM resolution is preferred when available. The deterministic rules are a
    fallback only and must not mutate WorldState.
    """

    def __init__(self, api_key=None, model=None, base_url=None, timeout=None):
        load_environment()
        self.api_key = os.getenv("LLM_API_KEY") if api_key is None else api_key
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL).rstrip("/")
        self.timeout = timeout or DEFAULT_LLM_TIMEOUT
        self.last_source = "rule"
        self.last_error = None

    def resolve(self, text, source, actor_id="player", target_id=None, world=None, context=None):
        """Return a canonical Intent without changing the world."""
        if source not in INTENT_SOURCES:
            raise ValueError(f"Unknown intent source: {source}.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Intent text must be non-empty.")

        context = self._build_context(world, target_id, context)
        if self.api_key:
            resolved = self._request_llm_intent(text, source, actor_id, target_id, context)
            if resolved is not None:
                self.last_source = "LLM"
                return resolved

        self.last_source = "rule"
        return self._rule_intent(text, source, actor_id, target_id, context)

    def _build_context(self, world, target_id, supplied):
        context = dict(supplied or {})
        context.setdefault("target_id", target_id)
        if world is None:
            return context
        context.setdefault("location", world.current_explore_location() or "open_ground")
        context.setdefault("time", world.time)
        context.setdefault(
            "nearby_npcs",
            [
                {"id": npc.id, "name": npc.name, "role": npc.role, "goals": list(npc.goals)}
                for npc in world.npcs
                if world.distance_between(world.player, world.npc_position(npc.id)) <= 12.0
            ],
        )
        context.setdefault("nearby_objects", [])
        if target_id and "conversation_history" not in context:
            target_npc = world.get_npc(target_id) if hasattr(world, "get_npc") else None
            if target_npc is not None:
                context["conversation_history"] = ConversationMemoryManager().peek_history(
                    target_npc, limit=8
                )
        return context

    def _request_llm_intent(self, text, source, actor_id, target_id, context):
        available_types = sorted(INTENT_TYPES)
        system = (
            "你是游戏 Intent Resolver，只判断玩家想做什么，不判断成败，不执行动作。"
            "只返回一个 JSON 对象，不要输出 Python、Markdown 或解释。"
            "格式："
            '{"intent_type":"follow","actor_id":"player","target_id":"npc_2",'
            '"parameters":{},"confidence":0.9,"reason":"简短中文原因"}。'
            "intent_type 只能是：" + ", ".join(available_types) + "。"
            "如果只是闲聊，使用 conversation_only；无法确定使用 unknown。"
            "target_id 只能使用输入上下文中已经存在的 NPC、对象或地点；没有目标则为 null。"
            "重要语义规则（必须遵守）："
            "1) 询问 ≠ 执行，请求 ≠ 已经执行。"
            "2) 出现“吗 / 能不能 / 可以吗 / 可不可以 / 愿不愿意 / 方便吗 / 要不要 / ?”通常是询问或请求，不是动作指令。"
            "3) 玩家想加入 NPC 的行动（例如“我能跟你一起去吗”“我可以陪你去看看吗”“那我能一起去看看吗”“你愿意跟我去村长家吗”）必须使用 conversation_only，禁止使用 follow（follow 是 NPC 跟随玩家）。"
            "4) follow 会让 NPC 真正开始跟随玩家并改变世界状态，只有玩家明确命令 NPC 跟随自己时才使用：跟我走 / 跟着我 / 跟我来 / 从现在开始跟着我 / 跟我一起走。"
            "5) 不允许仅凭句子中出现“跟 / 一起 / 去”等单个词就判断 follow。"
            "6) 不确定时优先选择不改变世界状态的 intent（conversation_only 或 unknown）。"
            "7) 判断时可结合 context.conversation_history 里上一轮 NPC 说过的话；如果玩家是在回应 NPC 的计划并要求同行，用 conversation_only。"
        )
        payload = {
            "text": text,
            "source": source,
            "actor_id": actor_id,
            "target_id": target_id,
            "context": context,
        }
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.1,
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
                body = json.loads(response.read().decode("utf-8"))
            raw = body["choices"][0]["message"]["content"]
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            return self._normalize(parsed, text, source, actor_id, target_id, context)
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            return None

    def _normalize(self, data, text, source, actor_id, target_id, context):
        if not isinstance(data, dict):
            return None
        intent_type = data.get("intent_type") or data.get("intent")
        if intent_type not in INTENT_TYPES:
            return None
        resolved_target = data.get("target_id", target_id)
        if resolved_target is not None and not isinstance(resolved_target, str):
            resolved_target = str(resolved_target)
        parameters = data.get("parameters", {})
        if not isinstance(parameters, dict):
            parameters = {}
        confidence = data.get("confidence", 0.7)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.5
        return Intent(
            source=source,
            actor_id=str(data.get("actor_id") or actor_id),
            target_id=resolved_target,
            intent_type=intent_type,
            parameters=parameters,
            raw_text=text,
            confidence=confidence,
            context=dict(context),
            reason=str(data.get("reason") or ""),
        )

    def _rule_intent(self, text, source, actor_id, target_id, context):
        lowered = text.lower().strip()
        target = target_id
        if target is None and context.get("target_id"):
            target = context["target_id"]
        nearby_npcs = context.get("nearby_npcs") or []
        nearby_objects = context.get("nearby_objects") or []

        if any(token in lowered for token in ("不用跟着", "别跟着", "不要跟着", "停止跟", "stop following", "don't follow")):
            intent_type = "stop_follow"
        elif is_accompany_request(text):
            # "Can I come with you?" is a question/offer, not an order to follow.
            intent_type = "conversation_only"
        elif is_explicit_follow_command(text):
            intent_type = "follow"
            if target is None and nearby_npcs:
                target = nearby_npcs[0]["id"]
        elif any(token in lowered for token in ("调查", "investigate", "消失的地方")):
            intent_type = "investigate"
            target = target or self._match_object(nearby_objects, ("footprint", "note", "camp", "forest"))
        elif any(token in lowered for token in ("拿起来", "拿走", "拾取", "捡起", "take ")):
            intent_type = "take"
            target = target or self._match_object(nearby_objects, ("ledger", "note", "desk"))
        elif any(token in lowered for token in ("看看", "查看", "检查", "inspect", "look at")):
            intent_type = "inspect"
            target = target or self._match_object(nearby_objects, ("desk", "bookshelf", "drawer", "lock"))
        elif any(token in lowered for token in ("搜索", "搜寻", "search")):
            intent_type = "search"
            target = target or self._match_object(nearby_objects, ("desk", "bookshelf", "footprint"))
        elif any(token in lowered for token in ("打开", "open ")):
            intent_type = "open"
            target = target or self._match_object(nearby_objects, ("drawer", "door", "box"))
        elif any(token in lowered for token in ("交给", "给 ", "give ", "hand ")):
            intent_type = "give"
            target = target or (nearby_npcs[0]["id"] if nearby_npcs else None)
        elif any(token in lowered for token in ("去森林", "前往森林", "去村庄", "去镇", "go to", "move to")):
            intent_type = "move"
            target = target or self._match_object(nearby_objects, ("forest", "town", "village", "home"))
        elif any(token in lowered for token in ("问", "打听", "ask ")):
            intent_type = "ask"
            target = target or (nearby_npcs[0]["id"] if nearby_npcs else None)
        elif target and any(token in lowered for token in ("聊天", "说话", "talk to", "和", "跟")):
            intent_type = "talk"
        elif any(token in lowered for token in ("打", "攻击", "attack")):
            intent_type = "attack"
            target = target or (nearby_npcs[0]["id"] if nearby_npcs else None)
        elif any(token in lowered for token in ("救", "rescue", "save")):
            intent_type = "rescue"
            target = target or (nearby_npcs[0]["id"] if nearby_npcs else None)
        elif any(token in lowered for token in ("藏", "hide")):
            intent_type = "hide"
        elif any(token in lowered for token in ("等", "wait")):
            intent_type = "wait"
        elif any(token in lowered for token in ("工作", "work")):
            intent_type = "work"
        elif any(token in lowered for token in ("买", "buy")):
            intent_type = "buy"
        elif any(token in lowered for token in ("探索", "explore")):
            intent_type = "explore"
        else:
            intent_type = "conversation_only"

        confidence = 0.75 if intent_type not in {"unknown", "conversation_only"} else 0.35
        return Intent(
            source=source,
            actor_id=actor_id,
            target_id=target,
            intent_type=intent_type,
            parameters={},
            raw_text=text,
            confidence=confidence,
            context=dict(context),
            reason="rule fallback",
        )

    @staticmethod
    def _match_object(objects, keywords):
        for obj in objects:
            obj_id = str(obj.get("id", "")).lower()
            if any(keyword in obj_id for keyword in keywords):
                return obj.get("id")
        return None
