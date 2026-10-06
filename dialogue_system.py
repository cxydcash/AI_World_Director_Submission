"State-grounded Chinese dialogue responses for NPCs, with optional LLM."

import json
import os
import urllib.error
import urllib.request

from conversation_memory import (
    ConversationMemoryManager,
    FACT_LEDGER_MISSING_PAGES,
    QUESTION_WHO_TOOK_PAGES,
    conversation_phrase,
)
from director import DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, DEFAULT_LLM_TIMEOUT, load_environment


def _has_any(lowered, *tokens):
    return any(token in lowered for token in tokens)


def _asks_about_conversation(lowered):
    if "我们刚才" in lowered:
        return True
    meta = ("聊什么", "聊了", "说过什么", "说什么", "what did we", "talking about", "conversation")
    recent = ("刚才", "之前", "刚刚", "earlier", "before")
    return _has_any(lowered, *meta) and _has_any(lowered, *recent)


def _asks_about_pages(lowered):
    return _has_any(lowered, "哪几页", "哪一页", "哪几頁", "哪些页", "具体是哪", "which pages", "missing pages")


def _asks_who_took(lowered):
    if _has_any(lowered, "谁拿", "谁偷", "谁取", "who took", "who stole"):
        return True
    return "谁" in lowered and _has_any(lowered, "拿走", "偷", "拿的", "取走")


def _asks_resolution(lowered):
    return _has_any(lowered, "查到", "查出来", "找到谁", "揪出", "find out who", "figured out")


CLEAN_HISTORY_LIMIT = 10
TIME_WORDS = ("昨天", "前天", "今天", "今早", "早上", "上午", "中午", "下午", "晚上", "昨晚", "刚才", "刚刚", "上周", "上个月", "去年")


def _dialogue_view(history, limit=CLEAN_HISTORY_LIMIT):
    """Clean [{"speaker","text"}] view; metadata stays in persistence only."""
    view = []
    for message in (history or [])[-max(1, int(limit)):]:
        if not isinstance(message, dict):
            continue
        text = str(message.get("text", "")).strip()
        if not text:
            continue
        view.append({"speaker": str(message.get("speaker", "")).strip(), "text": text})
    return view


def _last_npc_text(history):
    for message in reversed(history or []):
        if message.get("speaker") == "npc" and str(message.get("text", "")).strip():
            return str(message["text"]).strip()
    return ""


def _world_memory(npc, dialogue_texts, limit=6):
    """NPC observation memory that is NOT already part of the dialogue."""
    seen = {str(text).strip().lower() for text in (dialogue_texts or [])}
    entries = []
    for record in npc.memory:
        if not isinstance(record, dict):
            continue
        content = str(record.get("content", record.get("observed", ""))).strip()
        if not content:
            continue
        if str(record.get("source", "")) == "dialogue" or content.lower() in seen:
            continue
        entries.append(content)
    return entries[-limit:]


def _asks_recap(lowered):
    if _has_any(lowered, "我们刚才聊", "刚才聊什么", "刚才在说什么", "刚才到底在说什么",
                 "我们在说什么", "说到哪里", "聊到哪", "what were we talking"):
        return True
    return "刚才" in lowered and _has_any(lowered, "聊什么", "说什么", "聊到", "说到")


def _asks_which(lowered):
    return _has_any(lowered, "哪几笔", "哪一笔", "哪几项", "哪几处", "哪些支出", "哪些账", "which entries")


def _asks_what_number(lowered):
    return _has_any(lowered, "什么数字", "哪个数字", "什么数", "什么支出", "哪些数字", "具体数字", "what number")


def _asks_who_changed(lowered):
    if _has_any(lowered, "谁改", "谁动", "谁做", "谁拿", "谁偷", "谁取", "谁发现", "who changed", "who altered"):
        return True
    return "谁" in lowered


def _asks_when(lowered):
    return _has_any(lowered, "什么时候", "何时", "哪一天", "哪天", "几点", "when")


def _asks_then(lowered):
    return _has_any(lowered, "然后呢", "然后", "接下来", "之后呢", "那后来", "后来呢", "what happened next", "then what")


def _asks_confirm(lowered):
    return _has_any(lowered, "是不是", "对吗", "是吗", "说的是", "指的是", "是否")


def _asks_repeat(lowered):
    return _has_any(lowered, "你刚才说", "你刚说", "刚才说什么", "刚才说啥", "再说一遍", "what did you say")


def _extract_time_word(text):
    for word in TIME_WORDS:
        if word in (text or ""):
            return word
    return ""


class DialogueSystem:
    """Generate NPC replies from cognition; falls back to deterministic Chinese."""

    def __init__(self, api_key=None, model=None, base_url=None, timeout=None):
        load_environment()
        self.api_key = os.getenv("LLM_API_KEY") if api_key is None else api_key
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL).rstrip("/")
        self.timeout = timeout or DEFAULT_LLM_TIMEOUT
        self.last_source = "rule"
        self.last_history_count = 0
        self.memory = ConversationMemoryManager()

    def detect_intent(self, world, npc_id, message):
        """Legacy keyword fallback. V3 main path uses IntentResolver instead."""
        lowered = str(message).lower()
        stop_tokens = ("不用跟着", "别跟着", "不要跟着", "不用跟", "停止跟", "stop following", "don't follow")
        follow_tokens = ("跟着我", "跟我走", "跟我来", "和我一起走", "你可以跟", "can you follow", "follow me")
        if any(token in lowered for token in stop_tokens):
            return {"intent": "stop_follow", "npc_id": npc_id}
        if any(token in lowered for token in follow_tokens):
            return {"intent": "request_follow", "npc_id": npc_id}
        return {"intent": "none", "npc_id": npc_id}

    def generate_reply(self, world, npc_id, message):
        npc = world.get_npc(npc_id)
        if npc is None:
            raise ValueError(f"NPC not found: {npc_id}.")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Dialogue message must be non-empty.")

        # Record the player turn first so the reply can read the full history.
        self.memory.record_turn(npc, "player", message, world.time)
        self.memory.ingest_player_message(npc, message)
        self.last_history_count = len(
            _dialogue_view(self.memory.peek_history(npc, limit=CLEAN_HISTORY_LIMIT))
        )

        if self.api_key:
            reply = self._request_reply(npc, message)
            if reply:
                self.last_source = "LLM"
                self._remember_reply(npc, reply, world.time)
                return reply
        self.last_source = "rule"
        reply = self._fallback_reply(npc, message)
        self._remember_reply(npc, reply, world.time)
        return reply

    def _remember_reply(self, npc, reply, world_time):
        """Persist the NPC half of the turn and extract durable facts."""
        self.memory.record_turn(npc, "npc", reply, world_time)
        self.memory.ingest_reply(npc, reply)

    def note_intent(self, world, npc_id, intent_type):
        """Attach the resolved intent type to the last player turn."""
        npc = world.get_npc(npc_id)
        if npc is None or not intent_type:
            return False
        return self.memory.set_last_player_intent(npc, intent_type)

    def _request_reply(self, npc, message):
        # Clean, metadata-free dialogue view: this is the live conversation,
        # not background data.
        history = _dialogue_view(self.memory.peek_history(npc, limit=CLEAN_HISTORY_LIMIT))
        dialogue_texts = [entry["text"] for entry in history]
        system = (
            f"你是村庄里的 {npc.name}，职业是{npc.role or '村民'}。用第一人称自然地说话，像真实村民一样。"
            "回答使用简体中文，语气自然、简短，不要逐条复述数据。"
            "context 字段的优先级从高到低：1) player_message（玩家此刻的话）；"
            "2) conversation_history（你们正在进行的对话，这是当前上下文，不是背景资料）；"
            "3) known_facts / unresolved_questions；4) your_goals / your_beliefs / your_world_memory；"
            "5) relationship_to_player 等其它世界信息。"
            "对话规则：A. 先判断 player_message 是新话题，还是对前文的追问或省略。"
            "B. 玩家经常省略主语或宾语，例如“哪几笔？”“什么数字？”“谁发现的？”“什么时候？”“然后呢？”“你刚才说什么？”“我们刚才聊什么？”。"
            "这些问题依赖上文：回答前必须先从 conversation_history 里找到它们具体指代的上一句内容，再直接回答这个具体问题。"
            "C. 如果是追问，不要重新介绍已经讨论过的背景，不要当成第一次对话，不要因为出现相同关键词就重讲一遍话题。"
            "D. 可以直接引用或改写 conversation_history 里已经出现过的话。"
            "事实约束（必须遵守）：只能使用 conversation_history、known_facts、your_beliefs、your_world_memory 里已经出现过的具体事实"
            "（人名、数量、金额、时间、地点、物品）。如果这些信息里没有，就必须承认不知道"
            "（例如“我还不知道是谁”“我还没查清楚是什么时候”），禁止为了让回答听起来完整而编造任何新的具体事实。"
            "不要编造玩家还没有告诉你的秘密。player_claims 只是玩家的说法，不等于事实。"
        )
        context = {
            "player_message": message,
            "conversation_history": history,
            "known_facts": self.memory.known_facts(npc),
            "unresolved_questions": self.memory.unresolved_questions(npc),
            "recent_topics": self.memory.recent_topics(npc),
            "player_claims": self.memory.player_claims(npc),
            "your_personality": list(npc.personality),
            "your_goals": list(npc.goals),
            "your_beliefs": dict(npc.beliefs),
            "your_world_memory": _world_memory(npc, dialogue_texts),
            "relationship_to_player": npc.relationships.get("player"),
        }
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "temperature": 0.7,
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
            return body["choices"][0]["message"]["content"].strip()
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError):
            return None

    def _fallback_reply(self, npc, message):
        """Deterministic reply that reads the raw conversation messages."""
        lowered = str(message).lower()
        history = _dialogue_view(self.memory.peek_history(npc, limit=CLEAN_HISTORY_LIMIT))
        last_npc = _last_npc_text(history)
        facts = self.memory.known_facts(npc)
        unresolved = self.memory.unresolved_questions(npc)
        topics = self.memory.recent_topics(npc)

        # (0) recap: "what were we talking about?"
        if _asks_recap(lowered):
            phrase = conversation_phrase(topics)
            if last_npc:
                lead = f"我们刚才在说{phrase}，" if phrase else "我们刚才在说之前那件事，"
                return f"{lead}我最后说的是：{last_npc}"
            if phrase:
                return f"我们刚才一直在聊{phrase}的事情。"
            return "我们刚才还没聊到具体的事情。"

        # (1) follow-up on something the NPC already told the player
        if _asks_about_pages(lowered):
            if FACT_LEDGER_MISSING_PAGES in facts:
                reply = "是账本中间那几页。"
                if last_npc:
                    reply += f"我之前已经告诉过你：{last_npc.rstrip('。.!！？')}。"
                return reply
            return "我还没确认具体是哪几页。"

        # (2) "which entries / what numbers?" -> continue the previous NPC line
        if _asks_which(lowered) or _asks_what_number(lowered):
            if last_npc:
                return f"就是刚才说的那几笔：{last_npc}"
            return "我还没查到具体是哪些。"

        # (3) "who ...?" -> never invent a name
        if _asks_who_changed(lowered) or _asks_resolution(lowered):
            text = last_npc or ""
            if _has_any(text, "改过", "改的", "改动", "被人改"):
                return "我还不知道是谁改的，我只是发现那几笔被人改过。"
            if _has_any(text, "拿走", "少了几页", "缺页", "失踪"):
                if QUESTION_WHO_TOOK_PAGES in unresolved:
                    return "还是那件事，我还没查到是谁拿走了账本的那几页。"
                self.memory.add_unresolved(npc, QUESTION_WHO_TOOK_PAGES)
                return "我不知道是谁拿走的，但我确实发现少了几页。"
            if QUESTION_WHO_TOOK_PAGES in unresolved:
                return "还是那件事，我还没查到是谁拿走了账本的那几页。"
            return "我还不知道是谁，我只是发现账目有异常。"

        # (4) "when?"
        if _asks_when(lowered):
            when = _extract_time_word(last_npc)
            if when:
                return f"就是{when}发现的。"
            return "这个我还没查清楚，刚才我没提到具体时间。"

        # (5) "then what?"
        if _asks_then(lowered):
            if last_npc:
                return f"我刚说到「{last_npc}」，后面的事我还没查清楚。"
            return "后来我还没查出什么。"

        # (6) confirmation question
        if _asks_confirm(lowered):
            if last_npc:
                return f"是的，我刚才说的就是这件事：{last_npc}"
            return "我刚才没说过这个。"

        # (7) "what did you just say?"
        if _asks_repeat(lowered):
            if last_npc:
                return f"我刚才说的是：{last_npc}"
            return "我刚才没有说什么特别的。"

        # (8) ledger topic fallback (kept, but no longer the primary rule)
        if "ledger" in topics or _has_any(lowered, "账本", "账目", "ledger", "account"):
            if FACT_LEDGER_MISSING_PAGES in facts:
                return "我知道，最近账本少了几页，我一直在查。"
            self.memory.remember_fact(npc, FACT_LEDGER_MISSING_PAGES)
            return "知道，最近少了几页。"

        # (e) existing state-grounded fallback (goals / memory / beliefs / trust)
        parts = []
        goals = list(npc.goals)
        if any(g in goals for g in ("find ledger", "expose the mayor")):
            parts.append("我最近发现账本里有几处被改动过，我想查清楚。")
        elif "protect_office" in goals:
            parts.append("村里的账目一直由我管理，应该没有异常。")
        elif "help Ren" in goals:
            parts.append("Ren最近很不安，我很担心他。")
        elif "observe village" in goals:
            parts.append("我只是在核对村里的记录。")
        elif goals:
            parts.append(f"我现在想{goals[0].replace('_', ' ')}。")

        relevant_memory = self._pick_relevant_memory(npc, message)
        if relevant_memory:
            parts.append(f"我记得：{relevant_memory}。")

        if npc.beliefs:
            key, value = list(npc.beliefs.items())[-1]
            parts.append(f"我有种感觉，{key.replace('_', ' ')}，把握大约{value:.0%}。")

        relationship = npc.relationships.get("player")
        if relationship is not None:
            if relationship >= 0.7:
                parts.append("我愿意相信你。")
            elif relationship <= 0.3:
                parts.append("我暂时还不能完全信任你。")

        if not parts:
            parts.append("我现在知道的还不多。")
        return " ".join(parts)

    def _pick_relevant_memory(self, npc, current_message):
        best = None
        best_score = -1
        for memory in reversed(npc.memory):
            raw = str(memory.get("content", memory.get("observed", "")))
            if raw.strip().lower() == current_message.strip().lower():
                continue
            score = len(raw)
            if score > best_score:
                best = raw
                best_score = score
        return best
