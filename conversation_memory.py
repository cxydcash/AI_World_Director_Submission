"""Per-NPC continuous conversation memory for V3 Step 7.

This module owns one small, deterministic memory layer per NPC. It only reads
and writes ``npc.conversation_memory``; it never touches World Truth (items,
timeline, characters_state, evidence) and never calls an LLM.
"""

import re


DEFAULT_PARTNER = "player"
MAX_MESSAGES = 20
MAX_FACTS = 12
MAX_UNRESOLVED = 8
MAX_TOPICS = 8

FACT_LEDGER_MISSING_PAGES = "ledger has missing pages"
QUESTION_WHO_TOOK_PAGES = "who took the missing ledger pages"


TOPIC_KEYWORDS = {
    "ledger": ("账本", "账目", "ledger", "account"),
    "missing_pages": ("少了几页", "缺页", "缺少", "missing pages", "torn pages"),
    "who_took": ("谁拿", "谁偷", "谁取", "who took", "who stole"),
    "mayor": ("村长", "市长", "mayor"),
    "forest": ("森林", "forest"),
    "ren": ("ren", "雷恩"),
    "missing": ("失踪", "不见", "消失", "missing", "disappear"),
    "weather": ("天气", "weather"),
}

TOPIC_LABELS = {
    "ledger": "账本",
    "missing_pages": "账本缺页",
    "who_took": "谁拿走了账本",
    "mayor": "村长",
    "forest": "森林",
    "ren": "Ren",
    "missing": "失踪",
    "weather": "天气",
}


def _clean(value):
    return str(value or "").strip()


def extract_topics(*texts):
    """Deterministic keyword topics for a turn (no NLP model)."""
    lowered = " ".join(_clean(text).lower() for text in texts)
    topics = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword.lower() in lowered for keyword in keywords):
            topics.append(topic)
    return topics


def detect_player_claims(text):
    """Return player *claims* (never world truth) found in the message."""
    text = _clean(text)
    lowered = text.lower()
    claims = []
    if ("村长" in text or "mayor" in lowered) and any(
        token in text or token in lowered for token in ("偷", "拿走", "拿了", "贪污", "stole", "took")
    ):
        claims.append("player_claim: mayor took the ledger")
    return claims


def conversation_phrase(topics):
    """Human-readable Chinese phrase for the most recent conversation topics."""
    topics = list(topics or [])
    if "ledger" in topics and "missing_pages" in topics:
        return "账本失踪"
    if "who_took" in topics and "ledger" in topics:
        return "谁拿走了账本"
    for topic in topics:
        if topic in TOPIC_LABELS:
            return TOPIC_LABELS[topic]
    return ""


class ConversationMemoryManager:
    """Read/write the per-NPC, per-partner continuous conversation memory."""

    def ensure(self, npc, partner=DEFAULT_PARTNER):
        memory = getattr(npc, "conversation_memory", None)
        if not isinstance(memory, dict):
            memory = {}
            npc.conversation_memory = memory
        entry = memory.get(partner)
        if not isinstance(entry, dict):
            entry = {}
            memory[partner] = entry
        for key in ("messages", "known_facts", "unresolved_questions", "recent_topics", "player_claims"):
            if not isinstance(entry.get(key), list):
                entry[key] = []
        if not isinstance(entry.get("last_turn"), int):
            entry["last_turn"] = 0
        return entry

    def partner_entry(self, npc, partner=DEFAULT_PARTNER):
        return self.ensure(npc, partner)

    def record_turn(self, npc, speaker, text, world_time, partner=DEFAULT_PARTNER, intent_type=None):
        entry = self.ensure(npc, partner)
        text = _clean(text)
        topics = extract_topics(text)
        message = {
            "speaker": str(speaker),
            "text": text,
            "world_time": float(world_time),
            "intent_type": intent_type,
            "topic": topics[0] if topics else None,
        }
        entry["messages"].append(message)
        if len(entry["messages"]) > MAX_MESSAGES:
            del entry["messages"][:-MAX_MESSAGES]
        for topic in topics:
            self._push_unique(entry["recent_topics"], topic, MAX_TOPICS)
        entry["last_turn"] = int(entry.get("last_turn", 0)) + 1
        return message

    def history(self, npc, partner=DEFAULT_PARTNER, limit=10):
        entry = self.ensure(npc, partner)
        return [dict(message) for message in entry["messages"][-max(1, int(limit)):]]

    def peek_history(self, npc, partner=DEFAULT_PARTNER, limit=8):
        """Read recent messages without creating or mutating any structure."""
        memory = getattr(npc, "conversation_memory", None)
        if not isinstance(memory, dict):
            return []
        entry = memory.get(partner)
        if not isinstance(entry, dict):
            return []
        messages = entry.get("messages")
        if not isinstance(messages, list):
            return []
        recent = messages[-max(1, int(limit)):]
        return [dict(message) for message in recent if isinstance(message, dict)]

    def last_message(self, npc, speaker=None, partner=DEFAULT_PARTNER):
        entry = self.ensure(npc, partner)
        for message in reversed(entry["messages"]):
            if speaker is None or message.get("speaker") == speaker:
                return dict(message)
        return None

    def last_npc_message(self, npc, partner=DEFAULT_PARTNER):
        message = self.last_message(npc, speaker="npc", partner=partner)
        return message["text"] if message else None

    def known_facts(self, npc, partner=DEFAULT_PARTNER):
        return list(self.ensure(npc, partner)["known_facts"])

    def unresolved_questions(self, npc, partner=DEFAULT_PARTNER):
        return list(self.ensure(npc, partner)["unresolved_questions"])

    def player_claims(self, npc, partner=DEFAULT_PARTNER):
        return list(self.ensure(npc, partner)["player_claims"])

    def recent_topics(self, npc, partner=DEFAULT_PARTNER):
        return list(self.ensure(npc, partner)["recent_topics"])

    def remember_fact(self, npc, fact, partner=DEFAULT_PARTNER):
        fact = _clean(fact)
        if not fact:
            return None
        facts = self.ensure(npc, partner)["known_facts"]
        self._push_unique(facts, fact, MAX_FACTS)
        return fact

    def add_unresolved(self, npc, question, partner=DEFAULT_PARTNER):
        question = _clean(question)
        if not question:
            return None
        questions = self.ensure(npc, partner)["unresolved_questions"]
        self._push_unique(questions, question, MAX_UNRESOLVED)
        return question

    def resolve_question(self, npc, question, partner=DEFAULT_PARTNER):
        questions = self.ensure(npc, partner)["unresolved_questions"]
        if question in questions:
            questions.remove(question)
            return True
        return False

    def remember_claim(self, npc, claim, partner=DEFAULT_PARTNER):
        claim = _clean(claim)
        if not claim:
            return None
        claims = self.ensure(npc, partner)["player_claims"]
        self._push_unique(claims, claim, MAX_FACTS)
        return claim

    def set_last_player_intent(self, npc, intent_type, partner=DEFAULT_PARTNER):
        entry = self.ensure(npc, partner)
        for message in reversed(entry["messages"]):
            if message.get("speaker") == "player":
                message["intent_type"] = intent_type
                return True
        return False

    def ingest_player_message(self, npc, text, partner=DEFAULT_PARTNER):
        claims = detect_player_claims(text)
        for claim in claims:
            self.remember_claim(npc, claim, partner)
        return claims

    def ingest_reply(self, npc, reply, partner=DEFAULT_PARTNER):
        """Extract durable facts from what the NPC just said (not from truth)."""
        reply = _clean(reply)
        lowered = reply.lower()
        facts = []
        if "少了几页" in reply or "缺页" in reply or "missing pages" in lowered:
            facts.append(FACT_LEDGER_MISSING_PAGES)
        for fact in facts:
            self.remember_fact(npc, fact, partner)
        return facts

    def summarize_for_prompt(self, npc, partner=DEFAULT_PARTNER, limit=10):
        """Bounded context for LLM prompts (history + durable memory)."""
        return {
            "recent_messages": [
                {
                    "speaker": message.get("speaker"),
                    "text": message.get("text"),
                    "world_time": message.get("world_time"),
                    "intent_type": message.get("intent_type"),
                }
                for message in self.history(npc, partner, limit=limit)
            ],
            "known_facts": self.known_facts(npc, partner),
            "unresolved_questions": self.unresolved_questions(npc, partner),
            "recent_topics": self.recent_topics(npc, partner),
            "player_claims": self.player_claims(npc, partner),
        }

    @staticmethod
    def _push_unique(items, value, limit):
        if value in items:
            items.remove(value)
        items.insert(0, value)
        if len(items) > limit:
            del items[limit:]
