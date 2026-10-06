"""每日叙事编写层（Daily Narrative Writer）。

职责：把"真实发生过的一天"交给 LLM，整理成小说 / 戏剧式的"一幕一幕"，
让玩家看清主要人物之间发生了什么、说了什么。

硬性约束：
* 输入只能是真实事件（含对话原文）；输出只是改写，不得新增人物 / 地点 /
  线索 / 情节 / 因果 / 对白。
* 没有 LLM 或调用失败时，退化为"逐幕复述真实事实"的中文分幕版，
  仍然不编造任何内容。
* 结果按天缓存；生成在后台线程完成，不阻塞界面轮询。
"""

import json
import os
import threading
import urllib.error
import urllib.request

try:
    from .zh_labels import PLAYER_ACTION_TEXTS
except ImportError:
    from zh_labels import PLAYER_ACTION_TEXTS

from director import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_TIMEOUT,
    load_environment,
)

DAY = 86400.0

NARRATIVE_SYSTEM_PROMPT = """你是这个世界的记录者。你会收到"某一天真实发生过的事件清单"（其中包含真实的对话原文）。

请把它们整理成小说 / 戏剧式的"一幕一幕"，让读者清楚看到：主要人物之间发生了什么、
说了什么、事情怎么推进。

硬性规则（必须遵守）：
1. 只能使用清单里给出的事实。不得新增人物、地点、线索、情节、因果或心理描写。
2. 对话只能逐字引用清单里给出的原文，绝对不要编写新对白。
3. 清单里没有的信息一律不写；不确定就不写。
4. 用中文写作，语气自然，但不要夸张。
5. 必须使用清单里出现的真实人物名字，不要写"有人""某人""一个村民"。
6. 不要罗列时间戳，要写成连贯的场景叙述；同一人物的行动串成一条线。
7. 若某条事实缺少人物名或不完整，就不要写它。
8. 只输出 JSON，形如：
{"title":"第N天 · 标题","scenes":[{"title":"第一幕 · 场景名","text":"这一幕发生了什么的叙述","dialogue":[{"speaker":"人物名","line":"逐字引用的一句话"}]}]}
9. scenes 数量 2~4 幕；每幕 dialogue 可以为空数组。
"""


def _time_label(seconds):
    seconds = float(seconds or 0.0)
    day = int(seconds // DAY) + 1
    rem = seconds % DAY
    return f"第{day}天 {int(rem // 3600):02d}:{int((rem % 3600) // 60):02d}"


def _period(seconds):
    hour = (float(seconds or 0.0) % DAY) / 3600.0
    if hour < 6:
        return "凌晨"
    if hour < 12:
        return "上午"
    if hour < 18:
        return "下午"
    return "晚上"


def collect_day_facts(snapshot, observer, day, names=None, locations=None):
    """收集某一天真实发生过的事实（含对话原文），供叙事层改写。"""
    names = names or {}
    locations = locations or {}
    start = (day - 1) * DAY
    end = day * DAY
    facts = []

    def name_of(npc_id):
        return names.get(npc_id) or "某个村民"

    def in_day(seconds):
        return start <= float(seconds or 0.0) < end

    # 真实对话（含原文）
    for entry in snapshot.get("dialogues") or []:
        if not in_day(entry.get("time")):
            continue
        speaker = entry.get("speaker_id") or "?"
        facts.append({
            "time": entry.get("time"), "time_label": _time_label(entry.get("time")),
            "type": "对话",
            "speaker": "玩家" if speaker == "player" else name_of(speaker),
            "line": str(entry.get("content") or ""),
            "text": f"{'玩家' if speaker == 'player' else name_of(speaker)}：{entry.get('content')}",
        })
    for entry in (snapshot.get("open_world") or {}).get("dialogue_log") or []:
        if not in_day(entry.get("time")):
            continue
        npc_name = entry.get("npc") or name_of(entry.get("npc_id"))
        facts.append({
            "time": entry.get("time"), "time_label": _time_label(entry.get("time")),
            "type": "对话", "speaker": "玩家", "line": str(entry.get("player") or ""),
            "text": f"玩家对{npc_name}说：{entry.get('player')}",
        })
        if entry.get("reply"):
            facts.append({
                "time": entry.get("time"), "time_label": _time_label(entry.get("time")),
                "type": "对话", "speaker": npc_name, "line": str(entry.get("reply")),
                "text": f"{npc_name}回答：{entry.get('reply')}",
            })

    # 玩家真实行为（Action 管线日志）
    for action in (snapshot.get("open_world") or {}).get("player_actions") or []:
        if not in_day(action.get("time")) or action.get("action") == "move":
            continue
        action_type = str(action.get("action") or "")
        label = PLAYER_ACTION_TEXTS.get(action_type) or "做了一件事"
        raw = str(action.get("raw_text") or "").strip()
        text = f"玩家{label}"
        if raw and raw not in {"action", ""} and any("\u4e00" <= ch <= "\u9fff" for ch in raw):
            text = f"{text}（{raw}）"
        facts.append({
            "time": action.get("time"), "time_label": _time_label(action.get("time")),
            "type": "玩家行为", "text": text,
        })

    # NPC 行动 / 线索 / 环境（来自只读观察记录）
    if observer is not None:
        for item in observer.activity_log(400):
            if not in_day(item.get("time")):
                continue
            facts.append({
                "time": item.get("time"), "time_label": item.get("time_label"),
                "type": str(item.get("kind") or "记录"), "text": str(item.get("text") or ""),
            })
        for item in observer.actions:
            if not in_day(item.get("time")):
                continue
            facts.append({
                "time": item.get("time"), "time_label": item.get("time_label"),
                "type": str(item.get("category") or "世界事件"), "text": str(item.get("text") or ""),
            })

    # AI 编导当天的安排（真实事件效果）
    for entry in snapshot.get("timeline") or []:
        if str(entry.get("source")) != "ai_director":
            continue
        moment = entry.get("trigger_at")
        if not in_day(moment):
            continue
        facts.append({
            "time": moment, "time_label": _time_label(moment), "type": "编导安排",
            "text": f"AI 编导为这一天安排了事件：{entry.get('title') or entry.get('event_id')}",
        })

    facts.sort(key=lambda item: float(item.get("time") or 0.0))
    return facts


def _fallback_scene(day, facts):
    """没有 LLM 时的确定性分幕：只复述真实事实，不编造。"""
    buckets = {}
    for fact in facts:
        buckets.setdefault(_period(fact.get("time")), []).append(fact)
    scenes = []
    for index, period in enumerate(("凌晨", "上午", "下午", "晚上")):
        items = buckets.get(period)
        if not items:
            continue
        dialogue = [
            {"speaker": item.get("speaker") or "", "line": item.get("line") or ""}
            for item in items if item.get("type") == "对话" and item.get("line")
        ]
        lines = []
        seen = set()
        for item in items:
            text = str(item.get("text") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            lines.append(text)
        if not lines and not dialogue:
            continue
        scenes.append({
            "title": f"第{len(scenes) + 1}幕 · {period}",
            "text": "；".join(lines),
            "dialogue": dialogue,
        })
    return {"title": f"第{day}天 · 世界记录", "scenes": scenes, "source": "record"}


class DailyNarrativeWriter:
    """按天缓存的分幕叙事。生成在后台线程完成，界面轮询拿缓存。"""

    def __init__(self, api_key=None, model=None, base_url=None, timeout=None):
        load_environment()
        self.api_key = os.getenv("LLM_API_KEY") if api_key is None else api_key
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL).rstrip("/")
        self.timeout = timeout or DEFAULT_LLM_TIMEOUT
        self._lock = threading.RLock()
        self._cache = {}
        self._pending = set()

    def reset(self):
        with self._lock:
            self._cache = {}
            self._pending = set()

    def scene_for(self, day, facts):
        """返回该天的分幕；未生成完时返回 None，并在后台开始生成。"""
        with self._lock:
            if day in self._cache:
                return self._cache[day]
            if day in self._pending or not facts:
                return None
            self._pending.add(day)
        thread = threading.Thread(
            target=self._generate, args=(day, list(facts)), daemon=True
        )
        thread.start()
        return None

    def _generate(self, day, facts):
        scene = None
        if self.api_key:
            try:
                scene = self._request(day, facts)
            except Exception:
                scene = None
        if not scene or not scene.get("scenes"):
            scene = _fallback_scene(day, facts)
        with self._lock:
            self._cache[day] = scene
            self._pending.discard(day)

    def _request(self, day, facts):
        compact = [
            {"time": f.get("time_label"), "kind": f.get("type"), "text": f.get("text"),
             "speaker": f.get("speaker"), "line": f.get("line")}
            for f in facts[:80]
        ]
        payload = {"day": day, "events": compact}
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": NARRATIVE_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
        content = raw["choices"][0]["message"]["content"]
        scene = json.loads(content) if isinstance(content, str) else content
        if not isinstance(scene, dict) or not isinstance(scene.get("scenes"), list):
            return None
        scene["source"] = "ai"
        return scene
