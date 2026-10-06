"""Preset-conflict open world with dynamic adaptation and player agency."""

import functools
import json
import os
import random
import threading
import urllib.error
import urllib.request

from dialogue_system import DialogueSystem
from director import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_TIMEOUT,
    Director,
    LLMDirectorProvider,
    load_environment,
)
from open_world_intent import OpenWorldIntentParser
from world_objects import WorldObjectRegistry
from effect_normalizer import (
    effect_schema_text,
    goal_rules_text,
    normalize_effects,
    unsupported_goals,
)
from entities import Item, NPC, Player
from event import execute_event
from action_layer import Action, ActionBuilder, ActionExecutor, ActionValidator
from commitments import CommitmentRegistry
from dataclasses import replace as _dataclass_replace
from intent import IntentResolver, is_explicit_follow_command
from intent_legacy import LegacyDialogueAdapter, LegacyThoughtAdapter
from npc_planner import AI_GOAL_SOURCE, NPCPlanner
from player_controller import PlayerController
from player_intent import PlayerIntentParser
from quest import Quest
from scenario import apply_timeline_change, corruption_timeline
from story_engine import timeline_summary
from story_timeline import StoryTimeline
import npc_action_decision
import story_director
import night_director
import director as director_module
import director_input as director_input_mod
from goal_manager import GoalManager
from world import EXPLORE_LOCATIONS, WorldState


DAY_SECONDS = 86400.0
DIALOGUE_SESSION_MAX_SECONDS = 2 * 3600.0
# 对话进行中，世界时间流速降为原来的 1/30（1 小时 -> 2 分钟），
# 这样天气/日程/Timeline 等"外部变化"在对话期间会明显变少。
DIALOGUE_TIME_SCALE = 1.0 / 30.0
# 谈判式对话中会让 NPC 停在原地；只有这些"真实紧急信号"才会让它继续行动。
URGENT_NPC_STATES = ("fled", "hunted", "injured")
URGENT_NPC_GOALS = ("escape", "flee")
DIALOGUE_SESSION_MAX_DISTANCE = 12.0
OFFICE = "town"
OFFICE_POSITION = EXPLORE_LOCATIONS[OFFICE]
HOME_POSITION = EXPLORE_LOCATIONS["home"]

ITEM_LABELS = {
    "ledger": "账本",
    "coins": "钱袋",
    "oil": "油",
    "key": "钥匙",
    "food": "食物",
    "tool": "工具",
}

EVIDENCE_LABELS = {
    "footprint": "脚印",
    "footprints": "脚印",
    "water_trace": "水迹",
    "hidden_drawer": "暗格",
    "altered_records": "被改动的账目",
    "ordinary_ledger": "普通账本",
    "broken_lock": "被砸坏的锁",
    "hidden_ledger": "隐藏账本",
    "burn_mark": "烧痕",
    "blood": "血迹",
    "broken_branch": "断枝",
    "ren_note": "Ren 的字条",
    "ren_hiding": "有人躲藏的痕迹",
}

LOG_TEXT_CN = {
    "player_moved": "玩家移动了位置。",
    "player_talked_to_npc": "玩家与 NPC 进行了对话。",
    "ren_discovers_ledger": "Ren 发现账本被改动。",
    "ren_seeks_evidence": "Ren 开始寻找证据。",
    "mayor_notices": "村长发现 Ren 在调查。",
    "ren_pursued": "Ren 被村长的追捕。",
    "ren_lost": "Ren 失踪了。",
    "external_audit": "外部审计员到达村庄。",
    "mayor_flees": "村长逃跑了。",
    "timeline_changed": "剧情时间线被改变。",
    "ai_director_event": "AI 导演生成了新事件。",
    "weather_changed": "天气发生了变化。",
    "player_created_evidence": "玩家留下了证据。",
    "player_rescued_npc": "玩家救下了 NPC。",
    "npc_investigated_evidence": "NPC 调查了证据。",
}

STORY_ACTIONS = {
    "ren_discovers_ledger",
    "ren_seeks_evidence",
    "mayor_notices",
    "ren_pursued",
    "ren_lost",
    "external_audit",
    "mayor_flees",
    "timeline_changed",
    "ai_director_event",
}


def _blocked_at_night(method):
    """Player actions are refused during the 22:00-24:00 Night Director phase."""
    @functools.wraps(method)
    def _wrapped(self, *args, **kwargs):
        blocked = self._night_phase_block()
        if blocked is not None:
            return blocked
        return method(self, *args, **kwargs)
    return _wrapped


class OpenWorldDirector(Director):
    """State-driven director with an optional LLM delegate."""

    def __init__(self, provider=None):
        load_environment()
        self.api_key = os.getenv("LLM_API_KEY")
        self.model = os.getenv("LLM_MODEL") or DEFAULT_LLM_MODEL
        self.base_url = (os.getenv("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL).rstrip("/")
        self.timeout = DEFAULT_LLM_TIMEOUT
        self.last_log = {}
        self.last_forecast = None
        self.last_event_source = "unknown"
        self.last_llm_rejection = None
        self.last_llm_normalization = []
        self.provider = provider
        # Step 12.1: the NPC goal provider is an implementation backend of the
        # Director, not a second AI decision-maker. It is set by OpenWorldEngine
        # (or by tests) and consumed only inside decide_goals().
        self.npc_goal_provider = None
        if self.provider is None and self.api_key:
            self.provider = LLMDirectorProvider(
                api_key=self.api_key,
                model=self.model,
                base_url=self.base_url,
                timeout=self.timeout,
            )

    def plan_next_day(self, world, context):
        """Night Director entry point (Step 12.5).

        Reuses the existing provider/agent; no second LLM. If the provider has
        no night-planning method the plan is simply "keep the current plan".
        """
        provider = getattr(self, "provider", None)
        method = getattr(provider, "plan_next_day", None) if provider is not None else None
        if callable(method):
            self.last_night_plan_error = None
            try:
                return method(context)
            except Exception as exc:
                self.last_night_plan_error = str(exc)
                return {"reasoning_summary": f"night plan provider error: {exc}"}
        return {"reasoning_summary": "夜间编导：未配置夜间规划 Provider，保持当前计划。"}

    def decide_goals(self, world, director_input):
        """The single NPC-goal decision entry point (Step 12.1).

        Player dialogue is only information. The Director reads the world
        context plus the input and decides whether an NPC goal should change.
        Only ``player_dialogue`` is wired in this step; other sources are
        accepted and currently produce no change so future inputs (player
        action, NPC observation/discovery, world events) can be added without
        touching the callers.
        """
        if director_input is None:
            return []
        source = getattr(director_input, "source", None)
        if source == director_input_mod.SOURCE_PLAYER_DIALOGUE:
            return self._decide_player_dialogue_goals(world, director_input)
        # Deterministic Director policies for player_action / npc_discovery /
        # world_event. Still the Director deciding; not a second AI.
        return director_module.decide_goals_for_input(world, director_input)

    def _decide_player_dialogue_goals(self, world, director_input):
        """Reuse the existing NPC goal provider/prompt/validation as a backend.

        The Director owns the decision; ``npc_action_decision`` only supplies a
        raw proposal and never writes. Nothing is mutated here — the result is a
        list of GoalChange objects that GoalManager alone may commit.
        """
        npc_id = getattr(director_input, "target_id", None)
        message = getattr(director_input, "text", "") or ""
        provider = getattr(self, "npc_goal_provider", None)
        if provider is None or not npc_id or world.get_npc(npc_id) is None:
            return []
        try:
            decision = npc_action_decision.decide_npc_action(world, npc_id, message, provider)
        except Exception as exc:
            decision = {
                "action_required": False,
                "goal": None,
                "reason": f"provider_error: {type(exc).__name__}",
                "raw": None,
                "error": str(exc),
            }

        backend_payload = {
            "backend_decision": {
                "action_required": bool(decision.get("action_required")),
                "goal": decision.get("goal"),
                "reason": decision.get("reason"),
                "raw": decision.get("raw"),
                "error": decision.get("error"),
                "commitment": decision.get("commitment"),
                "urgency": (decision.get("goal") or {}).get("urgency", "normal"),
            },
        }
        base_kwargs = dict(
            npc_id=npc_id,
            reason=decision.get("reason") or "",
            commitment=str(decision.get("commitment") or "immediate"),
            urgency=str((decision.get("goal") or {}).get("urgency") or "normal"),
            source="ai_director",
            payload=backend_payload,
        )

        goal = decision.get("goal")
        if not decision.get("action_required") or not goal:
            # The Director decided no change. The backend metadata is kept so
            # the caller can report why (smalltalk / rejected / provider error).
            return [director_input_mod.GoalChange(
                change_type=director_input_mod.GOAL_CHANGE_NONE,
                goal_type=(goal or {}).get("goal_type"),
                target=(goal or {}).get("target"),
                **base_kwargs,
            )]

        goal_type = goal.get("goal_type")
        target = goal.get("target")
        confidence = float(goal.get("confidence", 1.0) or 0.0)

        # Policy guard: follow only from an explicit command, never from a
        # question or an offer to come along. The Director overrides the
        # backend and decides "no goal change".
        if goal_type == "follow_player":
            stripped = str(message).strip()
            explicit = is_explicit_follow_command(message) and not stripped.endswith(("吗", "?", "？"))
            if not explicit:
                return [director_input_mod.GoalChange(
                    change_type=director_input_mod.GOAL_CHANGE_NONE,
                    goal_type=goal_type,
                    target=target,
                    confidence=confidence,
                    reason="follow requires an explicit command",
                    commitment=str(decision.get("commitment") or "immediate"),
                    source="ai_director",
                    payload={
                        "backend_decision": backend_payload["backend_decision"],
                        "director_declined": True,
                        "director_override_reason": "follow requires an explicit command",
                    },
                )]

        # Deceiving/declining is the Director deciding *not* to change the
        # world; keep the metadata so the observation layer can still report it.
        if base_kwargs["commitment"] in {"deceive", "decline"}:
            return [director_input_mod.GoalChange(
                change_type=director_input_mod.GOAL_CHANGE_NONE,
                goal_type=goal_type,
                target=target,
                confidence=confidence,
                **base_kwargs,
            )]

        return [director_input_mod.GoalChange(
            change_type=director_input_mod.GOAL_CHANGE_ADD,
            goal_type=goal_type,
            target=target,
            goal_string=npc_action_decision.encode_goal_string(goal),
            confidence=confidence,
            **base_kwargs,
        )]

    def evaluate_story_event(self, world, event):
        ren = world.characters_state.get("npc_2", {})
        mayor = world.characters_state.get("npc_1", {})
        if event["id"] == "ren_lost" and (ren.get("rescued") or ren.get("protected")):
            return {"decision": "cancel", "reason": "player protected Ren", "modified_event": None}
        if event["id"] == "mayor_flees" and (mayor.get("exposed") or mayor.get("arrested")):
            return {"decision": "cancel", "reason": "mayor was already exposed", "modified_event": None}

        # Reuse the base Director's divergence rule: if a preset event tries to
        # move an NPC away from its own active goals, re-plan the beat around
        # the real WorldState instead of force-moving the NPC.
        divergence = self.movement_divergence(world, event)
        if divergence is not None:
            return {
                "decision": "modify",
                "reason": divergence["reason"],
                "modified_event": divergence["event"],
            }
        return {"decision": "keep", "reason": "no player-driven change", "modified_event": None}

    def propose_story_event(self, world):
        """Propose one future StoryEvent and register it on the timeline."""
        if self.provider is None:
            return {
                "success": False,
                "added": False,
                "event": None,
                "reason": "no_provider",
            }
        return story_director.propose_story_event(world, self.provider)

    def generate_event(self, world, context=None):
        if self.api_key:
            event = self._request_llm_event(world, context)
            if event is not None:
                self.last_event_source = "llm"
                return event
            # The LLM answer was unusable; say so instead of pretending.
            self.last_event_source = "deterministic_fallback"
        else:
            self.last_event_source = "deterministic"
        return self._deterministic_event(world, context)

    def _deterministic_event(self, world, context=None):
        ren = world.characters_state.get("npc_2", {})
        mayor = world.characters_state.get("npc_1", {})
        ledger = world.get_item("item_ledger")
        day = int(world.time // DAY_SECONDS) + 1
        effects = []

        if not ren.get("rescued") and ren.get("alive", True):
            effects.extend([
                {"type": "add_goal", "npc_id": "npc_2", "goal": "find ledger"},
                {"type": "set_belief", "npc_id": "npc_2", "key": "mayor_is_corrupt", "value": 0.75},
                {"type": "add_memory", "npc_id": "npc_2", "content": "The village ledger contains altered entries."},
            ])

        if day >= 2:
            effects.extend([
                {"type": "add_goal", "npc_id": "npc_1", "goal": "stop Ren"},
                {"type": "set_belief", "npc_id": "npc_1", "key": "ren_is_investigating", "value": 0.85},
            ])
        if day >= 4:
            effects.append({"type": "add_goal", "npc_id": "npc_1", "goal": "frame Ren"})
        if day >= 5:
            effects.extend([
                {"type": "add_goal", "npc_id": "npc_4", "goal": "investigate mayor"},
                {"type": "set_belief", "npc_id": "npc_4", "key": "village_funds_suspicious", "value": 0.7},
            ])

        if ren.get("rescued"):
            effects.extend([
                {"type": "change_relationship", "npc_id": "npc_2", "target_id": "player", "delta": 0.2},
                {"type": "set_belief", "npc_id": "npc_2", "key": "player_helpful", "value": 0.85},
                {"type": "set_belief", "npc_id": "npc_1", "key": "player_is_threat", "value": 0.7},
            ])
        if mayor.get("exposed") or (ledger is not None and ledger.holder == "player"):
            effects.extend([
                {"type": "add_goal", "npc_id": "npc_1", "goal": "recover ledger"},
                {"type": "add_memory", "npc_id": "npc_1", "content": "The ledger is missing."},
            ])

        timeline_status = {entry["id"]: entry["status"] for entry in world.story_timeline}
        if timeline_status.get("ren_lost") == "triggered" and not ren.get("alive", True):
            effects.extend([
                {"type": "set_belief", "npc_id": "npc_3", "key": "ren_is_dead", "value": 0.85},
                {"type": "add_goal", "npc_id": "npc_3", "goal": "investigate mayor"},
                {"type": "add_memory", "npc_id": "npc_3", "content": "Ren disappeared and may never come back."},
                {"type": "dialogue", "speaker_id": "npc_3", "content": "Ren 不会就这样消失，我要查清楚村长到底做了什么。"},
            ])
        if timeline_status.get("mayor_flees") == "triggered":
            effects.extend([
                {"type": "set_belief", "npc_id": "npc_4", "key": "mayor_fled", "value": 1.0},
                {"type": "add_goal", "npc_id": "npc_4", "goal": "demand answers"},
                {"type": "add_goal", "npc_id": "npc_3", "goal": "confront mayor"},
            ])

        event = {
            "event_id": f"open_world_daily_{int(world.time)}",
            "time": world.time,
            "effects": effects,
        }
        self.last_log = {
            "source": "OpenWorldDirector",
            "decision": "generated_event",
            "reason": f"Day {day}: the AI director advanced NPC goals and beliefs.",
            "event_id": event["event_id"],
        }
        return event

    def generate_forecast(self, world):
        if self.api_key:
            forecast = self._request_llm_forecast(world)
            if forecast:
                self.last_forecast = forecast
                return forecast
        self.last_forecast = self._deterministic_forecast(world)
        return self.last_forecast

    def _request_llm_forecast(self, world):
        payload = {
            "day": int(world.time // DAY_SECONDS) + 1,
            "player_position": [world.player.x, world.player.y],
            "player_memory": [m.get("content") for m in world.player.memory[-8:]],
            "npcs": [
                {"id": npc.id, "name": npc.name, "role": npc.role, "goals": list(npc.goals), "beliefs": dict(npc.beliefs)}
                for npc in world.npcs
            ],
            "evidence": [e.get("type") for e in world.evidence[-8:]],
            "timeline": [{"id": e["id"], "status": e["status"]} for e in world.story_timeline],
        }
        system = (
            "你是 AI 世界导演。请用中文写一段 2-4 句的“故事走向预告”。"
            "第一句只写已经发生的关键事实，第二句开始写未来可能发生的事：谁会行动、什么秘密会暴露、"
            "玩家下一步可能面对什么。不要只复述事件列表，要有戏剧性和悬念。"
        )
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.9,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"].strip()
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError):
            return None

    def _deterministic_forecast(self, world):
        statuses = {entry["id"]: entry["status"] for entry in world.story_timeline}
        if statuses.get("mayor_flees") == "triggered":
            return "村长已经逃离，但他留下的账目仍在村里引发怀疑。接下来，Mira 和审计员会追查资金去向，新的目击者可能出现。"
        if statuses.get("ren_lost") == "triggered":
            return "Ren 已经失踪，村庄表面恢复平静，但 Mira 不会放弃。接下来她可能私下调查，并引出一个新的知情者。"
        return "账本异常已经暴露，Ren 正在接近真相。接下来村长会试图掩盖证据，而玩家的选择可能决定谁先找到关键线索。"

    def resolve_free_action(self, world, action):
        """Ask the LLM to judge an arbitrary action and its consequences."""
        if self.api_key:
            result = self._request_llm_free_action(world, action)
            if result:
                return result
        return {
            "success": True,
            "narrative": f"你尝试了“{action.get('description', action.get('verb', '行动'))}”，这件事在村庄里留下了一点痕迹。",
            "effects": [],
            "source": "rule",
        }

    def _request_llm_free_action(self, world, action):
        payload = {
            "action": action,
            "location": world.current_explore_location() or "open_ground",
            "time": world.time,
            "weather": dict(world.environment_state.get("weather", {})),
            "player": {"position": [world.player.x, world.player.y], "inventory": list(world.player.inventory)},
            "nearby_npcs": [
                {"id": npc.id, "name": npc.name, "position": list(world.npc_position(npc.id)), "goals": list(npc.goals)}
                for npc in world.npcs
                if world.distance_between(world.player, world.npc_position(npc.id)) <= 12.0
            ],
        }
        system = (
            "你是开放世界动作裁判。判断玩家任意自然语言动作在当前地点、时间、天气、物品和 NPC 条件下"
            "是否能成功。可以失败，可以产生后果。必须返回 JSON："
            '{"success":true,"narrative":"中文结果描述","effects":[]}。'
            "effects 必须严格使用下面的 schema，禁止使用 effect 键、嵌套 {effect_name:{...}} 形式、"
            "自造字段名，或把地点名写进 location。\n"
            + effect_schema_text() + "\n\n" + goal_rules_text() + "\n"
            "没有后果就返回空数组。不要输出 Python 或解释。"
        )
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.8,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            raw = body["choices"][0]["message"]["content"]
            if isinstance(raw, str):
                raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            result = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(result, dict):
                return None
            result.setdefault("effects", [])
            result.setdefault("narrative", "你尝试了这个动作。")
            normalized, notes = normalize_effects(result["effects"])
            rejected = unsupported_goals(normalized)
            if rejected:
                # A goal the Planner cannot execute must never become active.
                self.last_llm_rejection = {
                    "source": "ai_director",
                    "status": "rejected",
                    "reason": f"unsupported goal(s): {rejected}",
                    "validation_error": "UnsupportedGoal",
                    "original": result.get("effects"),
                    "normalized": normalized,
                    "stage": "free_action_normalizer",
                }
                return {
                    "success": False,
                    "narrative": f"AI 生成的目标不被 NPC Planner 支持，动作已拒绝：{rejected}",
                    "effects": [],
                    "source": "LLM",
                    "rejected": self.last_llm_rejection,
                }
            result["effects"] = normalized
            result["normalization"] = notes
            result["source"] = "LLM"
            self.last_llm_normalization = notes
            self.last_llm_rejection = None
            return result
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError):
            return None

    def _request_llm_event(self, world, context=None):
        payload = {
            "time": world.time,
            "day": int(world.time // DAY_SECONDS) + 1,
            "player_position": [world.player.x, world.player.y],
            "player_memory": [m.get("content") for m in world.player.memory[-8:]],
            "npcs": [
                {
                    "id": npc.id,
                    "name": npc.name,
                    "role": npc.role,
                    "position": list(world.npc_position(npc.id)),
                    "goals": list(npc.goals),
                    "beliefs": dict(npc.beliefs),
                    "memory": [m.get("content") for m in npc.memory[-5:]],
                }
                for npc in world.npcs
            ],
            "evidence": [{"type": e.get("type"), "description": e.get("description")} for e in world.evidence[-8:]],
            "timeline": [{"id": e["id"], "status": e["status"]} for e in world.story_timeline],
            "recent_player_actions": [h.get("action") for h in world.history[-10:]],
        }
        system = (
            "你是大胆的 AI 世界导演。根据当前世界状态、玩家行动和 NPC 认知，生成一个 Event DSL，"
            "制造有蝴蝶效应的新剧情。每天必须至少制造一个新事件；即使原剧情已经结束，"
            "也要给玩家新的冲突、谣言、目击或 NPC 行动。不要保守，不要停在原剧情；但要和已有状态一致。"
            "你有两条编导渠道：第一，操纵已有 NPC，给他们新目标、信念、记忆、关系，并用 move_npc 让他们移动；"
            "第二，创造新故事，允许使用 create_npc 引入新角色，允许使用 spawn_item 引入新物品。"
            "要大胆创新，让剧情从玩家行为中长出新的分支。必须返回 JSON 对象："
            '{"event_id":"...","time":0,"effects":[...]}。'
            "effects 必须严格使用下面的 schema；禁止使用 effect 键、嵌套 {effect_name:{...}} 形式、"
            "自造字段名，或把地点名写进 location。\n"
            + effect_schema_text() + "\n\n" + goal_rules_text() + "\n"
            "禁止输出其他 effect 或额外字段。"
        )
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.8,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            raw = body["choices"][0]["message"]["content"]
            event = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(event, dict) or not event.get("effects"):
                self.last_llm_rejection = {
                    "source": "ai_director",
                    "status": "rejected",
                    "reason": "LLM returned no usable effects",
                    "validation_error": "EmptyEffects",
                    "original": raw if isinstance(raw, (dict, list)) else str(raw)[:2000],
                    "normalized": None,
                    "stage": "daily_event_parse",
                }
                return None
            notes = []
            normalized, notes = normalize_effects(event.get("effects") or [])
            rejected = unsupported_goals(normalized)
            if rejected:
                self.last_llm_rejection = {
                    "source": "ai_director",
                    "status": "rejected",
                    "reason": f"unsupported goal(s): {rejected}",
                    "validation_error": "UnsupportedGoal",
                    "original": event,
                    "normalized": normalized,
                    "stage": "daily_event_normalizer",
                }
                return None
            # A plain Event only accepts event_id / time / effects.
            extra = sorted(set(event) - {"event_id", "time", "effects"})
            if extra:
                notes.append("dropped top-level " + ", ".join(extra))
            event = {key: value for key, value in event.items() if key in {"event_id", "time", "effects"}}
            event["effects"] = normalized
            event["event_id"] = event.get("event_id") or f"llm_director_{int(world.time)}"
            event["time"] = world.time
            self.last_llm_normalization = notes
            self.last_llm_rejection = None
            self.last_log = {
                "source": "LLM",
                "decision": "generated_event",
                "event_id": event["event_id"],
                "normalized": notes,
            }
            return event
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError):
            return None


def create_open_world():
    """Create the corruption-conflict village world."""
    return WorldState(
        time=0.0,
        player=Player(x=50.0, y=50.0, inventory=[], money=100.0, energy=100.0),
        npcs=[
            NPC(
                id="npc_1",
                name="Mayor Aldric",
                role="mayor",
                x=HOME_POSITION[0],
                y=HOME_POSITION[1],
                vision_range=6.0,
                position_override=list(HOME_POSITION),
                relationships={"player": 0.5, "npc_2": 0.4},
                goals=["protect_office"],
                character_seed={
                    "personality": ["calculating", "authoritative"],
                    "long_term_goal": "keep control of village funds",
                    "secret": "He falsified the village ledger and embezzled public funds.",
                    "fear": "Ren reveals the truth to the auditor.",
                    "conflict": "He wants to stay in power, but the evidence is closing in.",
                },
            ),
            NPC(
                id="npc_2",
                name="Ren",
                role="accountant",
                x=OFFICE_POSITION[0],
                y=OFFICE_POSITION[1],
                vision_range=8.0,
                position_override=list(OFFICE_POSITION),
                relationships={"player": 0.3, "npc_1": 0.4},
                goals=["find ledger"],
                character_seed={
                    "personality": ["careful", "persistent"],
                    "long_term_goal": "expose the mayor",
                    "secret": "He found altered entries in the ledger.",
                    "fear": "The mayor discovers what he knows.",
                    "conflict": "He wants proof, but revealing it may put him in danger.",
                },
            ),
            NPC(
                id="npc_3",
                name="Mira",
                role="villager",
                x=50.0,
                y=30.0,
                vision_range=7.0,
                position_override=[50.0, 30.0],
                relationships={"player": 0.4, "npc_2": 0.7},
                goals=["help Ren"],
                character_seed={
                    "personality": ["kind", "watchful"],
                    "long_term_goal": "keep the village safe",
                    "secret": "She has seen the mayor meet strangers at night.",
                    "fear": "The village falls apart if the truth stays hidden.",
                    "conflict": "She trusts Ren, but fears confronting the mayor.",
                },
            ),
            NPC(
                id="npc_4",
                name="Ivy",
                role="external_auditor",
                x=5.0,
                y=95.0,
                vision_range=6.0,
                position_override=[5.0, 95.0],
                relationships={"player": 0.3},
                goals=["observe village"],
                character_seed={
                    "personality": ["skeptical", "methodical"],
                    "long_term_goal": "complete the village audit",
                    "secret": "She suspects the records were altered.",
                    "fear": "Evidence disappears before she can act.",
                    "conflict": "She needs proof, but the mayor controls access.",
                },
            ),
        ],
        items=[
            Item(id="item_ledger", type="ledger", x=22.0, y=22.0),
            Item(id="item_funds", type="coins", x=20.0, y=22.0),
            Item(id="item_oil", type="oil", x=50.0, y=55.0),
        ],
        characters_state={
            "npc_1": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "home", "rescued": False, "exposed": False, "arrested": False},
            "npc_2": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "town", "rescued": False, "protected": False, "hunted": False},
            "npc_3": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "village", "rescued": False},
            "npc_4": {"alive": True, "injured": False, "unconscious": False, "health": 100, "location": "village", "rescued": False},
        },
        quests=[
            Quest(
                quest_id="quest_001",
                title="Expose the Mayor",
                giver_id="npc_2",
                description="Ren found altered ledger entries and needs proof.",
                target="expose Mayor Aldric",
                reward=80,
            )
        ],
        story_timeline=corruption_timeline(),
    )


class OpenWorldEngine:
    """Living world: preset timeline plus player-driven dynamic adaptation."""

    def __init__(self, director=None, tick_seconds=3600.0, real_tick_interval=0.4):
        self.director = director or OpenWorldDirector()
        self.tick_seconds = float(tick_seconds)
        # 对话中的时间缩放（1.0 = 不变慢）。可在运行期调整。
        self.dialogue_time_scale = DIALOGUE_TIME_SCALE
        self.real_tick_interval = float(real_tick_interval)
        # move_step 是玩家按一次方向键走的格数（越大越快）；
        # 取 5.0 刚好等于交互半径，避免一步跨过可互动范围。
        self.controller = PlayerController(move_step=5.0, observation_radius=12.0)
        self.intent_parser = PlayerIntentParser()
        self.intent_resolver = IntentResolver()
        self.object_registry = WorldObjectRegistry()
        self.action_builder = ActionBuilder()
        self.action_validator = ActionValidator(self.object_registry)
        self.action_executor = ActionExecutor(self.action_validator)
        self.legacy_dialogue_adapter = LegacyDialogueAdapter()
        self.legacy_thought_adapter = LegacyThoughtAdapter()
        self.open_intent = OpenWorldIntentParser()
        self.npc_planner = NPCPlanner()
        # GoalManager is the only component that commits a Director GoalChange
        # into WorldState (Step 12.1).
        self.goal_manager = GoalManager()
        # Let the director resolve location goals when re-planning divergent
        # preset events, without owning a second movement system.
        self.director._planner = self.npc_planner
        # The web runtime uses the LLM to decide NPC goals when a key exists;
        # offline tests (no key) keep the decision layer disabled. The provider
        # lives on the Director: it is a backend of decide_goals, not a second
        # AI decision-maker.
        self.director.npc_goal_provider = (
            npc_action_decision.LLMNPCGoalProvider() if os.getenv("LLM_API_KEY") else None
        )
        self.last_npc_goal_decision = None
        self.dialogue_system = DialogueSystem()
        # Step 9: a dialogue session auto-ends when the partner walks away or
        # the conversation runs too long. Set either to None to disable that
        # individual trigger (used by older session-protection tests).
        self.dialogue_session_max_seconds = DIALOGUE_SESSION_MAX_SECONDS
        self.dialogue_session_max_distance = DIALOGUE_SESSION_MAX_DISTANCE
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._running = False
        self.reset()

    @property
    def world(self):
        return self._world

    def decide_and_apply_goals(self, director_input):
        """Single NPC-goal funnel: AI Director decides, GoalManager writes.

        Every new-goal path (dialogue, discovery, player action, world event)
        must go through here. Callers supply information only.
        """
        changes = self.director.decide_goals(self._world, director_input) or []
        results = [self.goal_manager.apply(self._world, change) for change in changes]
        return {"changes": changes, "results": results}

    @property
    def npc_goal_provider(self):
        """The Director's goal-decision backend.

        Kept as an engine attribute for backwards compatibility, but it is
        stored on the Director so there is a single decision authority.
        """
        return getattr(self.director, "npc_goal_provider", None)

    @npc_goal_provider.setter
    def npc_goal_provider(self, value):
        self.director.npc_goal_provider = value

    def reset(self):
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        with self._lock:
            self._world = create_open_world()
            self._world.story_director = self.director
            self._world.npc_planner = self.npc_planner
            self._world.goal_manager = self.goal_manager
            self._world.goal_manager = self.goal_manager
            self.object_state = {}
            self.pending_object_goals = {}
            self.story_phase = 0
            self._world.object_state = self.object_state
            self._update_weather_state()
            self.tick_index = 0
            self.input_paused = False
            self.auto_running = False
            self._resume_after_input = False
            self._last_director_day = -1
            self._last_story_count = 0
            self.last_candidates = []
            self.last_intent = None
            self.clues = []
            self.director_feed = []
            self.last_dialogue_reply = None
            self.dialogue_log = []
            self.last_player_action = None
            # 对话里产生的约定（谁答应做什么、有没有真的去做）
            self.commitments = CommitmentRegistry(goal_router=self.decide_and_apply_goals)
            # 只读展示用：只由玩家 Action 管线（_finish_action）写入的行为日志。
            # 世界事件 / NPC 行为 / Director 事件都不会写这里。
            self.player_actions = []
            self._last_snapshot = None
            self.last_error = None
            self.last_story_events = []
            self.last_director_report = None
            self.last_night_director_report = None
            self.night_phase_entered_at = None
            self.director_reports = []
            self.last_story_proposal = None
            self.last_npc_goal_decision = None
            self._last_event = {
                "time": self._world.time,
                "type": "open_world_reset",
                "description": "Corruption-conflict village reset.",
            }
        return self.current()

    def adopt_world(self, world):
        """Continue an existing (for example loaded) WorldState in this runtime."""
        with self._lock:
            self._world = world
            self._world.story_director = self.director
            self._world.npc_planner = self.npc_planner
            if getattr(world, "object_state", None) is None:
                world.object_state = {}
            self.object_state = world.object_state
            self._last_director_day = int(world.time // DAY_SECONDS)
            self._last_story_count = len(world.story_event_history)
            self.last_story_proposal = None
            self.last_story_events = []
            self.last_night_director_report = getattr(world, "night_director_report", None)
            self.night_phase_entered_at = None
            return self.current()

    def start(self, interval=None):
        if self._thread is not None and self._thread.is_alive():
            return self.current()
        interval = self.real_tick_interval if interval is None else float(interval)
        self._stop_event.clear()
        with self._lock:
            self._running = True
        self._thread = threading.Thread(target=self._run_loop, args=(interval,), daemon=True)
        self._thread.start()
        return self.current()

    def pause(self):
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            self._running = False
            self._thread = None
        return self.current()

    def pause_for_input(self):
        if not self.input_paused:
            self._resume_after_input = self.auto_running or self._running
            self.pause()
            self.input_paused = True
        return self.current()

    def resume_after_input(self):
        should_resume = self._resume_after_input
        self.input_paused = False
        self._resume_after_input = False
        if should_resume:
            self.auto_running = True
            self.start()
        return self.current()

    def tick(self):
        with self._lock:
            self._maybe_auto_end_dialogue_session()
            self._refresh_phase()
            before_story = len(self._world.story_event_history)
            self._advance_tick(before_story)
            self.tick_index += 1
            self.last_story_events = [
                dict(entry)
                for entry in self._world.story_event_history[before_story:]
            ]
            description = self._describe_tick()
            self._last_event = {
                "time": self._world.time,
                "type": "open_world_tick",
                "description": description,
                "story_events": [entry["event_id"] for entry in self.last_story_events],
            }
        return self.current()

    def _advance_tick(self, before_story):
        """Advance one tick across phase boundaries (Step 12.5).

        The tick consumes its full time step. When it crosses 22:00 the Night
        Director runs immediately; during 22:00-24:00 ordinary StoryEvents and
        the NPC Planner do not run. Ordinary day-side systems run once per tick
        when the tick ends in the day phase.
        """
        remaining = float(self._tick_time_seconds())
        guard = 0
        while remaining > 1e-9 and guard < 8:
            guard += 1
            phase = self._refresh_phase()
            if phase == night_director.PHASE_NIGHT_DIRECTOR:
                if not self._night_director_done():
                    self._run_night_director()
                    self._update_weather_state()
                boundary = night_director.next_day_start_second(self._world.time)
                segment = min(remaining, max(0.0, boundary - self._world.time))
                if segment <= 0:
                    break
                self._world.advance_time(segment, run_story_events=False)
                self._update_weather_state()
                remaining -= segment
                self._refresh_phase()
                continue
            # Day segment: stop exactly on the 22:00 boundary.
            boundary = night_director.night_start_second(self._world.time)
            if boundary > self._world.time:
                segment = min(remaining, boundary - self._world.time)
            else:
                segment = remaining
            if segment <= 1e-9:
                segment = remaining
            target = self._world.time + segment
            self._world.advance_time(
                segment, run_story_events=not night_director.is_night(target)
            )
            self._tag_goals_from_new_story_events(before_story)
            self._update_weather_state()
            remaining -= segment
            self._refresh_phase()
            if (
                self._world.phase == night_director.PHASE_NIGHT_DIRECTOR
                and not self._night_director_done()
            ):
                self._run_night_director()
                self._update_weather_state()

        # Ordinary day-side systems run once per tick, only in the day phase.
        self._refresh_phase()
        if self._world.phase == night_director.PHASE_DAY:
            # 约定先于 Planner 检查：NPC 到达后先进入"等待玩家"状态，再规划。
            self.commitments.tick(self._world, self.tick_index)
            self._update_npcs_with_planner()
            # Daily orchestration happens once, in the 22:00-24:00 Night
            # Director phase. The legacy _maybe_daily_director must not run
            # again at the 00:00 day boundary.

    # -- phase state machine (Step 12.5) -----------------------------------
    def _refresh_phase(self):
        """Set the explicit world phase from the current time."""
        phase = night_director.phase_for_time(self._world.time)
        if self._world.phase == phase:
            return phase
        self._world.phase = phase
        if phase == night_director.PHASE_NIGHT_DIRECTOR:
            self._enter_night_phase()
        return phase

    def _enter_night_phase(self):
        """22:00: ordinary play pauses; every Goal and session is preserved.

        An active dialogue session is *not* force-ended: player actions are
        refused at the phase guard, and the session resumes when the world
        returns to the day phase. This keeps deferred StoryEvents deferred.
        """
        self._world.night_director_day = None
        self.night_phase_entered_at = self._world.time

    def _night_director_done(self):
        return self._world.night_director_day == night_director.day_index(self._world.time)

    def _run_night_director(self):
        report = night_director.run_night_director(self._world, self.director)
        self._world.night_director_report = report
        self._world.night_director_day = night_director.day_index(self._world.time)
        # The Night Director is the single daily orchestration entry: it plans
        # the next day, so the legacy daily director must not run for that day.
        self._world.daily_orchestration_day = self._world.night_director_day + 1
        self.last_night_director_report = report
        self.director_feed.append({
            "time": self._world.time,
            "text": "夜间编导完成：" + str(report.get("reasoning_summary", ""))[:80],
            "event_id": None,
            "source": "night_director",
        })
        return report

    def _night_phase_block(self):
        """Structured refusal for player actions during the night phase."""
        if getattr(self._world, "phase", night_director.PHASE_DAY) == night_director.PHASE_NIGHT_DIRECTOR:
            return {
                "success": False,
                "error": "night_director_phase",
                "message": "当前正在进行每日世界编排（22:00-24:00），请稍候。",
                "phase": night_director.PHASE_NIGHT_DIRECTOR,
                "step": self.current(),
            }
        return None

    @_blocked_at_night
    def move(self, direction):
        self.pause_for_input()
        vectors = {
            "up": (0.0, self.controller.move_step), "w": (0.0, self.controller.move_step),
            "down": (0.0, -self.controller.move_step), "s": (0.0, -self.controller.move_step),
            "left": (-self.controller.move_step, 0.0), "a": (-self.controller.move_step, 0.0),
            "right": (self.controller.move_step, 0.0), "d": (self.controller.move_step, 0.0),
        }
        vector = vectors.get(str(direction).strip().lower())
        if vector is None:
            return self._fail_action(f"Unknown movement direction: {direction}.")
        action = Action(
            "move",
            actor_id="player",
            parameters={"x": self._world.player.x + vector[0], "y": self._world.player.y + vector[1]},
            raw_text=f"move {direction}",
        )
        validation = self.action_validator.validate(self._world, action)
        if not validation.valid:
            return self._fail_action(validation.reason)
        execution = self.action_executor.execute(self._world, action, validation=validation, runtime=self)
        return self._finish_action(execution.success, execution.message, action.to_dict(), f"move {direction}")

    @_blocked_at_night
    def execute_action(self, action, raw_text=None):
        self.pause_for_input()
        success, message, parsed = self.controller.execute_action(self._world, action, raw_text)
        return self._finish_action(success, message, parsed, raw_text or "action")

    @_blocked_at_night
    def analyze_thought(self, text):
        self.pause_for_input()
        nearby_objects = self.object_registry.visible_objects(self._world)
        nearby_npcs = self.controller.observe(self._world)["npcs"]
        resolved_intent = self.intent_resolver.resolve(
            text,
            source="thought",
            actor_id="player",
            target_id=None,
            world=self._world,
            context={"nearby_objects": nearby_objects, "nearby_npcs": nearby_npcs},
        )
        self.last_intent = resolved_intent
        action_candidate = self.action_builder.build(resolved_intent)
        action_validation = self.action_validator.validate(self._world, action_candidate)
        self._record_player_memory("thought", text, {"location": self._player_location()})
        candidates, nearby_objects, nearby_npcs = self.legacy_thought_adapter.analyze(
            self, text, resolved_intent
        )
        self.last_candidates = candidates
        if candidates:
            self.last_error = None
        else:
            llm_reason = getattr(self.open_intent, "last_llm_reason", None)
            self.last_error = llm_reason or "没有生成 Action，因为当前位置附近没有合适的对象、NPC 或可执行条件。"
        self._last_event = {
            "time": self._world.time,
            "type": "intent_analysis",
            "description": f"AI proposed {len(candidates)} possible Actions.",
            "input": text,
        }
        return {
            "success": bool(candidates),
            "message": self.last_error or "Candidate Actions generated.",
            "candidates": list(candidates),
            "source": getattr(self.open_intent, "last_source", "rule"),
            "resolved_intent": resolved_intent.to_dict(),
            "action_candidate": action_candidate.to_dict(),
            "action_validation": {
                "valid": action_validation.valid,
                "reason": action_validation.reason,
                "normalized_parameters": action_validation.normalized_parameters,
            },
            "step": self.current(),
        }

    @_blocked_at_night
    def protect_ren(self):
        """Protect Ren, changing NPC beliefs and the future timeline."""
        self.pause_for_input()
        ren = self.world.get_npc("npc_2")
        if ren is None:
            return self._fail_action("Ren is not present.")
        if self.world.distance_between(self.world.player, self.world.npc_position("npc_2")) > 5.0:
            return self._fail_action("Ren is not nearby.")
        state = self.world.characters_state.setdefault("npc_2", {})
        state["protected"] = True
        state["rescued"] = True
        ren.change_relationship("player", 0.4)
        ren.set_belief("player_saved_me", 1.0)
        ren.add_memory({"time": self.world.time, "source": "player", "content": "The player helped me before the mayor's people arrived."})
        self.world.characters_state.setdefault("npc_1", {})["player_is_threat"] = True
        self._record_player_memory("action", "protected Ren", {"npc_id": "npc_2"})
        self._apply_protection_timeline_changes()
        return self._finish_action(True, "Protected Ren. The mayor now sees you as a threat.", {"type": "protect_ren"}, "protect Ren")

    @_blocked_at_night
    def take_ledger(self):
        """Take the hidden ledger, causing the mayor to react."""
        self.pause_for_input()
        ledger = self.world.get_item("item_ledger")
        if ledger is None:
            return self._fail_action("The ledger no longer exists.")
        if ledger.holder is not None:
            return self._fail_action("The ledger is already held.")
        success, message = self.world.pick("item_ledger")
        if not success:
            return self._fail_action(message)
        mayor = self.world.get_npc("npc_1")
        if mayor is not None:
            mayor.set_belief("ledger_missing", 1.0)
            mayor.add_memory({"time": self.world.time, "source": "player", "content": "The ledger disappeared."})
            # The Director decides the mayor's reaction; the suggested goal is
            # information only and GoalManager performs the write.
            self.decide_and_apply_goals(director_input_mod.DirectorInput(
                source=director_input_mod.SOURCE_PLAYER_ACTION,
                actor_id="player",
                target_id="npc_1",
                payload={
                    "action": "take_ledger",
                    "item_id": "item_ledger",
                    "npc_id": "npc_1",
                },
                context={
                    "time": self.world.time,
                    "affected_npcs": [
                        {"npc_id": "npc_1", "suggested_goal": "recover ledger"},
                    ],
                },
                time=self.world.time,
            ))
        self.world.characters_state.setdefault("npc_1", {})["exposed"] = True
        self._record_player_memory("action", "took the hidden ledger", {"item_id": "item_ledger"})
        return self._finish_action(True, "Took the hidden ledger. The mayor is now hunting for it.", {"type": "take_ledger"}, "take ledger")

    @_blocked_at_night
    def npc_follow_player(self, npc_id):
        self.pause_for_input()
        npc = self._world.get_npc(npc_id)
        if npc is None:
            return self._fail_action(f"NPC not found: {npc_id}.")
        if self._world.distance_between(self._world.player, self._world.npc_position(npc_id)) > 8.0:
            return self._fail_action(f"{npc.name} 离你太远，无法跟随。")
        self._set_follow_state(npc_id, True)
        npc.add_memory({"time": self._world.time, "source": "player", "content": "The player asked me to follow them."})
        self._record_player_memory("action", f"{npc.name} agreed to follow", {"npc_id": npc_id})
        return self._finish_action(True, f"{npc.name} 开始跟着你。", {"type": "npc_follow_player", "npc_id": npc_id}, "follow me")

    @_blocked_at_night
    def ask_for_hammer(self, npc_id):
        self.pause_for_input()
        npc = self._world.get_npc(npc_id)
        if npc is None:
            return self._fail_action("附近没有可以求助的人。")
        if self._world.distance_between(self._world.player, self._world.npc_position(npc_id)) > 8.0:
            return self._fail_action(f"{npc.name} 离你太远。")
        relationship = npc.relationships.get("player", 0.5)
        if relationship < 0.4:
            mayor = self._world.get_npc("npc_1")
            if mayor is not None and self._world.distance_between(self._world.player, self._world.npc_position("npc_1")) <= 8.0:
                mayor.set_belief("player_is_threat", 0.8)
            self._record_player_memory("ask_hammer_failed", f"asked {npc.name} for a hammer", {"npc_id": npc_id})
            return self._finish_action(False, f"{npc.name} 拒绝帮你，还可能惊动了村长。", {"type": "ask_for_hammer", "npc_id": npc_id}, "ask hammer")
        self.object_state["drawer_open"] = True
        self.object_state["drawer_searched"] = False
        self.object_state.pop("need_hammer", None)
        self._world.object_state = self.object_state
        self._world.evidence.append({"type": "broken_lock", "owner": "player", "location": list(HOME_POSITION), "description": "村民借来的锤子砸开了锁。", "created_at": self._world.time, "investigated": False})
        self._record_player_memory("ask_hammer", f"{npc.name} helped you open the lock", {"npc_id": npc_id})
        return self._finish_action(True, f"{npc.name} 借给你锤子，你砸开了抽屉锁。", {"type": "ask_for_hammer", "npc_id": npc_id}, "ask hammer")

    @_blocked_at_night
    def break_object(self, object_id):
        self.pause_for_input()
        if object_id != "mayor_desk_lock":
            return self._fail_action(f"Cannot break {object_id}.")
        mayor = self._world.get_npc("npc_1")
        if mayor is not None and self._world.distance_between(self._world.player, self._world.npc_position("npc_1")) <= 10.0:
            mayor.set_belief("player_is_threat", 0.8)
            mayor.change_relationship("player", -0.2)
            self._record_player_memory("break_lock_failed", "tried to break the lock", {"object": object_id})
            return self._finish_action(False, "你试图砸锁，但被村长发现了。", {"type": "break_object", "object_id": object_id}, "break lock")
        rng = random.Random(f"{object_id}:{int(self._world.time)}")
        if rng.random() < 0.25:
            self.object_state["need_hammer"] = True
            self._world.object_state = self.object_state
            self._record_player_memory("break_lock_failed", "failed to break the lock", {"object": object_id})
            return self._finish_action(False, "锁太坚固，你没能打开。或许可以向附近村民借锤子。", {"type": "break_object", "object_id": object_id}, "break lock")
        self.object_state["drawer_open"] = True
        self.object_state["drawer_searched"] = False
        self.object_state.pop("need_hammer", None)
        self._world.object_state = self.object_state
        self._world.evidence.append({"type": "broken_lock", "owner": "player", "location": list(HOME_POSITION), "description": "抽屉的锁被砸坏了。", "created_at": self._world.time, "investigated": False})
        self._record_player_memory("break_lock", "broke the desk lock", {"object": object_id})
        return self._finish_action(True, "你砸开了抽屉锁，发现里面似乎有东西。", {"type": "break_object", "object_id": object_id}, "break lock")

    @_blocked_at_night
    def open_drawer(self, object_id):
        self.pause_for_input()
        if not self.object_state.get("drawer_open"):
            return self._fail_action("抽屉还是锁着的。")
        if self.object_state.get("drawer_searched"):
            return self._finish_action(True, "抽屉已经打开过了。", {"type": "open_drawer", "object_id": object_id}, "open drawer")
        self.object_state["drawer_searched"] = True
        self.object_state["ledger_revealed"] = True
        self._world.object_state = self.object_state
        self._world.evidence.append({"type": "hidden_ledger", "owner": "mayor", "location": [self._world.player.x, self._world.player.y], "description": "抽屉里藏着一本账本。", "created_at": self._world.time, "investigated": False})
        self._record_player_memory("open_drawer", "opened the hidden drawer", {"object": object_id})
        return self._finish_action(True, "你打开抽屉，看到里面有一本隐藏账本。", {"type": "open_drawer", "object_id": object_id}, "open drawer")

    @_blocked_at_night
    def search_object(self, object_id):
        self.pause_for_input()
        if object_id == "__location__":
            self._record_player_memory("search", "searched the current location", {"object": object_id})
            return self._finish_action(True, "You searched the area but found nothing new.", {"type": "search_object", "object_id": object_id}, "search")
        obj = self.object_registry.get(object_id)
        if obj is None:
            return self._fail_action(f"Unknown object: {object_id}.")
        self.object_state[object_id] = True
        self._world.object_state = self.object_state
        found = None
        if "desk" in object_id:
            found = "发现桌子后面似乎有一个暗格。"
            self.object_state["mayor_hidden_drawer"] = True
            mayor = self._world.get_npc("npc_1")
            if mayor is not None and self._world.distance_between(self._world.player, self._world.npc_position("npc_1")) <= 8.0:
                mayor.change_relationship("player", -0.1)
                mayor.set_belief("player_searching_office", 0.5)
        elif "bookshelf" in object_id:
            found = "找到一本普通账本。"
            self._world.evidence.append({"type": "ordinary_ledger", "owner": "mayor", "location": list(HOME_POSITION), "description": "一本普通的村庄账本。", "created_at": self._world.time, "investigated": False})
        self._record_player_memory("search", f"searched {object_id}", {"object": object_id})
        message = f"你搜索了 {object_id}，" + (found or "但暂时没有发现新东西。")
        return self._finish_action(True, message, {"type": "search_object", "object_id": object_id}, "search")

    @_blocked_at_night
    def inspect_object(self, object_id):
        self.pause_for_input()
        obj = self.object_registry.get(object_id)
        if obj is None:
            return self._fail_action(f"Unknown object: {object_id}.")
        self.object_state[object_id] = True
        self._world.object_state = self.object_state
        if object_id == "mayor_desk":
            self.object_state["mayor_hidden_drawer"] = True
            self._world.evidence.append({"type": "hidden_drawer", "owner": "mayor", "location": list(HOME_POSITION), "description": "A hidden drawer behind the mayor's desk.", "created_at": self._world.time, "investigated": False})
        elif "bookshelf" in object_id:
            self._world.evidence.append({"type": "ordinary_ledger", "owner": "mayor", "location": list(HOME_POSITION), "description": "一本普通的村庄账本。", "created_at": self._world.time, "investigated": False})
        self._record_player_memory("inspect", f"inspected {object_id}", {"object": object_id})
        message = "你检查了书架，获得了一本普通账本。" if "bookshelf" in object_id else f"你检查了 {object_id}。"
        return self._finish_action(True, message, {"type": "inspect_object", "object_id": object_id}, "inspect")

    @_blocked_at_night
    def take_object(self, object_id):
        if object_id == "ledger":
            return self.take_ledger()
        self.pause_for_input()
        obj = self.object_registry.get(object_id)
        if obj is None:
            return self._fail_action(f"Unknown object: {object_id}.")
        self.object_state[object_id] = True
        self._world.object_state = self.object_state
        self._world.evidence.append({"type": object_id, "owner": "unknown", "location": [self._world.player.x, self._world.player.y], "description": obj.get("description", object_id), "created_at": self._world.time, "investigated": False})
        self._record_player_memory("take", f"took {object_id}", {"object": object_id})
        return self._finish_action(True, f"You took {object_id}.", {"type": "take_object", "object_id": object_id}, "take")

    @_blocked_at_night
    def follow_object(self, object_id):
        self.pause_for_input()
        target = HOME_POSITION if "desk" in object_id else OFFICE_POSITION
        success, message, parsed = self.controller.execute_action(self._world, {"type": "move", "x": target[0], "y": target[1]}, "follow")
        self._record_player_memory("follow", f"followed {object_id}", {"object": object_id})
        if success:
            if object_id == "footprints":
                self._record_clue("脚印通向办公室，Ren 可能就在附近。")
                self._world.evidence.append({"type": "ren_hiding", "owner": "unknown", "location": list(OFFICE_POSITION), "description": "脚印在办公室附近中断了。", "created_at": self._world.time, "investigated": False})
                message += " 你发现脚印在办公室附近中断了。"
            elif object_id == "ren_camp":
                self._record_clue("这里有人躲藏过的痕迹，Ren 最近来过。")
        result = self._finish_action(success, message, parsed, "follow")
        if success:
            self._director_react("follow_object")
        return result

    @_blocked_at_night
    def follow_npc(self, npc_id):
        self.pause_for_input()
        npc = self._world.get_npc(npc_id)
        if npc is None:
            return self._fail_action(f"Unknown NPC: {npc_id}.")
        pos = self._world.npc_position(npc_id)
        success, message, parsed = self.controller.execute_action(self._world, {"type": "move", "x": pos[0], "y": pos[1]}, "follow")
        self._record_player_memory("follow", f"followed {npc_id}", {"npc_id": npc_id})
        if success:
            if npc_id == "npc_2":
                self._record_clue("你跟着 Ren，他似乎正前往办公室寻找账本。")
            elif npc_id == "npc_1":
                self._record_clue("村长看起来很紧张，不停回头张望。")
            elif npc_id == "npc_4":
                self._record_clue("Ivy 正前往办公室核对账目。")
        result = self._finish_action(success, message, parsed, "follow")
        if success:
            self._director_react("follow_npc")
        return result

    @_blocked_at_night
    def wait_world(self, seconds):
        self.pause_for_input()
        success, message = self._world.wait(float(seconds))
        return self._finish_action(success, message, {"type": "wait", "seconds": float(seconds)}, "wait")

    def _layered_action_from_candidate(self, candidate):
        action = candidate.get("action", {})
        action_type = action.get("type")
        if action_type == "move":
            return Action("move", parameters={"x": action.get("x"), "y": action.get("y")}, raw_text=candidate.get("label", "move"))
        if action_type == "search_object":
            return Action("search", target_id=action.get("object_id"), raw_text=candidate.get("label", "search"))
        if action_type == "inspect_object":
            return Action("inspect", target_id=action.get("object_id"), raw_text=candidate.get("label", "inspect"))
        if action_type == "take_object":
            return Action("take", target_id=action.get("object_id"), raw_text=candidate.get("label", "take"))
        if action_type == "open_drawer":
            return Action("open", target_id=action.get("object_id"), raw_text=candidate.get("label", "open"))
        if action_type == "npc_follow_player":
            return Action("follow", target_id=action.get("npc_id"), raw_text=candidate.get("label", "follow"))
        if action_type == "talk":
            return Action("talk", target_id=action.get("npc_id"), parameters={"text": action.get("text", "")}, raw_text=candidate.get("label", "talk"))
        return None

    def execute_via_action_layer(self, action, raw_text=None):
        validation = self.action_validator.validate(self._world, action)
        if not validation.valid:
            return {"success": False, "error": validation.reason, "message": validation.reason, "step": self.current()}
        execution = self.action_executor.execute(self._world, action, validation=validation, runtime=self)
        if execution.data.get("legacy_result"):
            result = dict(execution.data["legacy_result"])
            result["action_layer"] = True
            result["action"] = action.to_dict()
            result["validation"] = {"valid": validation.valid, "reason": validation.reason}
            return result
        result = self._finish_action(execution.success, execution.message, action.to_dict(), raw_text or action.raw_text)
        result["action_layer"] = True
        result["validation"] = {"valid": validation.valid, "reason": validation.reason}
        return result

    @_blocked_at_night
    def execute_candidate(self, candidate_id):
        candidate = next((item for item in self.last_candidates if item["id"] == candidate_id), None)
        if candidate is None:
            return {"success": False, "error": f"Unknown candidate: {candidate_id}.", "step": self.current()}
        layered_action = self._layered_action_from_candidate(candidate)
        if layered_action is not None:
            result = self.execute_via_action_layer(layered_action, raw_text=candidate.get("label", ""))
            result["candidate"] = candidate
            return result
        action_type = candidate["action"].get("type")
        if action_type == "protect_ren":
            result = self.protect_ren()
        elif action_type == "take_ledger":
            result = self.take_ledger()
        elif action_type == "search_object":
            result = self.search_object(candidate["action"].get("object_id"))
        elif action_type == "inspect_object":
            result = self.inspect_object(candidate["action"].get("object_id"))
        elif action_type == "take_object":
            result = self.take_object(candidate["action"].get("object_id"))
        elif action_type == "follow_object":
            result = self.follow_object(candidate["action"].get("object_id"))
        elif action_type == "follow_npc":
            result = self.follow_npc(candidate["action"].get("npc_id"))
        elif action_type == "break_object":
            result = self.break_object(candidate["action"].get("object_id"))
        elif action_type == "open_drawer":
            result = self.open_drawer(candidate["action"].get("object_id"))
        elif action_type == "ask_for_hammer":
            result = self.ask_for_hammer(candidate["action"].get("npc_id"))
        elif action_type == "npc_follow_player":
            result = self.npc_follow_player(candidate["action"].get("npc_id"))
        elif action_type == "free_action":
            result = self.execute_free_action(candidate["action"])
        elif action_type == "wait":
            result = self.wait_world(candidate["action"].get("seconds", 3600))
        else:
            result = self.execute_action(candidate["action"], raw_text=candidate["label"])
        result["candidate"] = candidate
        return result

    @_blocked_at_night
    def execute_free_action(self, action):
        self.pause_for_input()
        description = action.get("description") or action.get("verb") or "自由行动"
        resolution = self.director.resolve_free_action(self._world, action)
        success = bool(resolution.get("success", True))
        narrative = str(resolution.get("narrative") or f"你尝试了{description}。")
        effects = resolution.get("effects") or []
        if success and effects:
            event = {
                "event_id": f"free_action_{int(self._world.time)}_{int(random.random() * 100000)}",
                "time": self._world.time,
                "effects": effects,
            }
            try:
                execute_event(self._world, event)
            except Exception as exc:
                return self._fail_action(
                    "AI 生成的动作后果不符合 Event DSL，已拒绝执行"
                    f"（{type(exc).__name__}: {exc}）。"
                )
        self._record_player_memory("free_action", description, {"action": action, "success": success})
        result = self._finish_action(success, narrative, action, description)
        result["resolution_source"] = resolution.get("source", "rule")
        return result

    @_blocked_at_night
    def talk(self, npc_id, message):
        self.pause_for_input()
        self._begin_dialogue_session(npc_id)
        self._record_player_memory("dialogue", message, {"npc_id": npc_id})
        nearby_objects = self.object_registry.visible_objects(self._world)
        nearby_npcs = self.controller.observe(self._world)["npcs"]
        resolved_intent = self.intent_resolver.resolve(
            message,
            source="dialogue",
            actor_id="player",
            target_id=npc_id,
            world=self._world,
            context={"nearby_objects": nearby_objects, "nearby_npcs": nearby_npcs},
        )
        # Follow safety guard: a follow intent only makes sense as an explicit
        # order. If the player asked/offered to come along, downgrade to a
        # neutral conversation so nothing world-changing happens.
        if resolved_intent.intent_type == "follow" and not is_explicit_follow_command(message):
            resolved_intent = _dataclass_replace(
                resolved_intent,
                intent_type="conversation_only",
                reason="follow guard: request/question, not a command",
            )
        # The NPC being talked to is the only valid target for NPC-directed
        # dialogue: the LLM must not be able to redirect it to another NPC.
        if resolved_intent.target_id != npc_id and resolved_intent.intent_type in {
            "follow", "stop_follow", "talk", "ask", "conversation_only",
        }:
            resolved_intent = _dataclass_replace(
                resolved_intent,
                target_id=npc_id,
                reason="dialogue target pinned to the current NPC",
            )
        self.last_intent = resolved_intent
        action = self.action_builder.build(resolved_intent)
        validation = self.action_validator.validate(self._world, action)

        # Follow is a GOAL now, not a directly executed Action. When a goal
        # provider is configured, an explicit follow order goes through the NPC
        # Action Decision (Event DSL) instead of the old _execute_follow path.
        if action.action_type == "follow" and getattr(self, "npc_goal_provider", None) is not None:
            return self._talk_follow_via_decision(npc_id, message, resolved_intent)

        migrated_dialogue_types = {"talk", "follow", "stop_follow", "conversation_only"}
        if action.action_type not in migrated_dialogue_types:
            result = self.legacy_dialogue_adapter.execute_talk(self, npc_id, message, resolved_intent)
            if result.get("success") and resolved_intent.intent_type != "stop_follow":
                result["npc_goal_decision"] = self._maybe_decide_npc_goal(npc_id, message)
            return result

        if not validation.valid:
            return {
                "success": False,
                "message": validation.reason,
                "reply": None,
                "intent": resolved_intent.intent_type,
                "resolved_intent": resolved_intent.to_dict(),
                "following": False,
                "step": self.current(),
            }

        execution = self.action_executor.execute(
            self._world,
            action,
            validation=validation,
            runtime=self,
        )
        reply = execution.data.get("reply")
        if execution.success and action.action_type == "talk":
            reply = self.dialogue_system.generate_reply(self._world, npc_id, message)
        if execution.success and action.action_type in {"talk", "conversation_only"}:
            note_intent = getattr(self.dialogue_system, "note_intent", None)
            if callable(note_intent):
                note_intent(self._world, npc_id, resolved_intent.intent_type)
        following = bool(self._world.characters_state.get(npc_id, {}).get("following_player"))
        if execution.success:
            self.last_dialogue_reply = reply
            npc = self._world.get_npc(npc_id)
            self.dialogue_log.append({
                "time": self._world.time,
                "player": message,
                "npc_id": npc_id,
                "npc": npc.name if npc is not None else npc_id,
                "reply": reply,
                "intent": resolved_intent.intent_type,
                "following": following,
                "reply_source": getattr(self.dialogue_system, "last_source", "rule"),
                "history_count": getattr(self.dialogue_system, "last_history_count", 0),
            })
        self._finish_action(execution.success, execution.message, action.to_dict(), f"talk {npc_id}")
        intent_alias = (
            "request_follow" if resolved_intent.intent_type == "follow"
            else "stop_follow" if resolved_intent.intent_type == "stop_follow"
            else resolved_intent.intent_type
        )
        decision = None
        if execution.success and resolved_intent.intent_type != "stop_follow":
            # A stop order must not be overridden by a new goal proposal.
            decision = self._maybe_decide_npc_goal(npc_id, message)
        return {
            "success": execution.success,
            "message": execution.message,
            "reply": reply,
            "npc_goal_decision": decision,
            "reply_source": getattr(self.dialogue_system, "last_source", "rule"),
            "intent": intent_alias,
            "resolved_intent": resolved_intent.to_dict(),
            "following": following,
            "step": self.current(),
        }

    def _talk_follow_via_decision(self, npc_id, message, resolved_intent):
        """Explicit follow orders go through the NPC goal decision.

        The goal decision commits follow_player through the Event DSL
        (set_follow_target), and the reply comes from DialogueSystem. The old
        Action Layer _execute_follow path is not used here.
        """
        decision = self._maybe_decide_npc_goal(npc_id, message)
        followed = bool(
            decision
            and decision.get("action_required")
            and (decision.get("goal") or {}).get("goal_type") == "follow_player"
        )
        reply = None
        try:
            reply = self.dialogue_system.generate_reply(self._world, npc_id, message)
        except Exception as exc:
            self.last_error = str(exc)
        self.last_dialogue_reply = reply
        npc = self._world.get_npc(npc_id)
        following = bool(self._world.characters_state.get(npc_id, {}).get("following_player"))
        self.dialogue_log.append({
            "time": self._world.time,
            "player": message,
            "npc_id": npc_id,
            "npc": npc.name if npc is not None else npc_id,
            "reply": reply,
            "intent": "request_follow" if followed else "conversation_only",
            "following": following,
            "reply_source": getattr(self.dialogue_system, "last_source", "rule"),
            "history_count": getattr(self.dialogue_system, "last_history_count", 0),
        })
        self._finish_action(
            True,
            "NPC follow handled via the goal decision.",
            {"type": "follow", "npc_id": npc_id},
            f"talk {npc_id}",
        )
        return {
            "success": True,
            "message": "NPC follow handled via the goal decision.",
            "reply": reply,
            "reply_source": getattr(self.dialogue_system, "last_source", "rule"),
            "intent": "request_follow" if followed else "conversation_only",
            "resolved_intent": resolved_intent.to_dict(),
            "following": following,
            "npc_goal_decision": decision,
            "step": self.current(),
        }

    def _maybe_decide_npc_goal(self, npc_id, message):
        """Route one dialogue through the AI Director, then GoalManager.

        Step 12.1: player dialogue is only *information*. The Director is the
        single decider of NPC goals; GoalManager is the only writer. The
        existing NPCGoalDecision provider/prompt/validation is reused as the
        Director's backend, but it no longer applies goals itself.
        """
        provider = getattr(self, "npc_goal_provider", None)
        if provider is None:
            self.last_npc_goal_decision = None
            return None

        director_input = director_input_mod.DirectorInput(
            source=director_input_mod.SOURCE_PLAYER_DIALOGUE,
            actor_id="player",
            target_id=npc_id,
            text=message,
            context={
                "time": self._world.time,
                "location": self._player_location(),
                "nearby_npcs": [
                    {"id": npc.id, "name": npc.name}
                    for npc in self._world.npcs
                    if self._world.distance_between(
                        self._world.player, self._world.npc_position(npc.id)
                    ) <= 12.0
                ],
            },
            time=self._world.time,
        )
        try:
            changes = self.director.decide_goals(self._world, director_input) or []
        except Exception as exc:
            decision = {
                "action_required": False,
                "goal": None,
                "reason": f"director_error: {exc}",
                "raw": None,
                "error": str(exc),
            }
            self.last_npc_goal_decision = decision
            return decision

        change = changes[0] if changes else None
        npc = self._world.get_npc(npc_id)

        if change is None:
            decision = {
                "action_required": False,
                "goal": None,
                "reason": "director_no_goal",
                "raw": None,
                "error": None,
            }
            self.last_npc_goal_decision = decision
            return decision

        payload = dict(change.payload or {})
        backend = dict(payload.get("backend_decision") or {})
        declined = bool(payload.get("director_declined"))
        override_reason = payload.get("director_override_reason")
        change_type = change.change_type

        if declined:
            goal = None
        else:
            goal = backend.get("goal")
            if goal is None and change.goal_type:
                goal = {
                    "goal_type": change.goal_type,
                    "target": change.target,
                    "reason": change.reason,
                    "confidence": change.confidence,
                    "commitment": change.commitment,
                }

        commitment = str(change.commitment or "immediate")
        if change_type == director_input_mod.GOAL_CHANGE_ADD:
            action_required = True
        elif not declined and commitment in {"deceive", "decline"} and goal is not None:
            action_required = True
        else:
            action_required = False

        reason = override_reason or backend.get("reason") or change.reason or "no_action"
        if commitment == "deceive":
            reason = "口头答应但没有动身"
        elif commitment == "decline":
            reason = "拒绝了这件事"

        decision = {
            "action_required": action_required,
            "goal": goal,
            "commitment": commitment,
            "reason": reason,
            "raw": backend.get("raw"),
            "error": backend.get("error"),
        }
        goal_string = change.resolved_goal_string()

        if change_type == director_input_mod.GOAL_CHANGE_NONE:
            # The Director decided not to change the world. Keep the promise
            # visible to the observation layer, but write no goal.
            decision["commitment_applied"] = False
            decision["applied_goal"] = None
            self.commitments.register(
                npc_id=npc_id,
                npc_name=(npc.name if npc is not None else npc_id),
                goal=goal or {"goal_type": change.goal_type, "target": change.target},
                goal_string="",
                commitment=commitment,
                tick_index=self.tick_index,
                reason=decision["reason"],
            )
            self.last_npc_goal_decision = decision
            return decision

        if npc is not None and goal_string and goal_string in npc.goals:
            decision["already_present"] = True
            decision["action_required"] = False
            decision["reason"] = "goal already present"
        else:
            result = self.goal_manager.apply(self._world, change)
            decision["goal_manager"] = result
            if result.get("applied"):
                decision["applied_goal"] = result.get("goal_string")
            else:
                decision["applied_goal"] = None
                decision["reason"] = f"apply_failed: {result.get('reason')}"
        try:
            self.commitments.register(
                npc_id=npc_id,
                npc_name=(npc.name if npc is not None else npc_id),
                goal=goal,
                goal_string=goal_string,
                commitment=commitment,
                tick_index=self.tick_index,
                reason=decision.get("reason", ""),
            )
        except Exception:
            pass
        self.last_npc_goal_decision = decision
        return decision

    def _npc_accepts_follow(self, npc_id):
        npc = self._world.get_npc(npc_id)
        if npc is None:
            return False
        state = self._world.characters_state.get(npc_id, {})
        if not state.get("alive", True) or state.get("unconscious", False):
            return False
        if state.get("fled") or state.get("follow_player"):
            return bool(state.get("follow_player"))
        return npc.relationships.get("player", 0.0) >= 0.3

    def _set_follow_state(self, npc_id, following):
        if not following and getattr(self, "commitments", None) is not None:
            self.commitments.cancel_for_npc(npc_id, "玩家让TA不用跟着了", by_player=True)
        # Follow goals are decided by the Director and written by GoalManager;
        # this legacy entry point only supplies the player action information.
        director_input = director_input_mod.DirectorInput(
            source=director_input_mod.SOURCE_PLAYER_ACTION,
            actor_id="player",
            target_id=npc_id,
            payload={
                "action": "follow" if following else "stop_follow",
                "npc_id": npc_id,
            },
            context={"time": self._world.time},
            time=self._world.time,
        )
        self.decide_and_apply_goals(director_input)

    @staticmethod
    def _npc_action_label(world, npc):
        """Return a readable NPC action backed by the Planner state."""
        state = world.characters_state.get(npc.id, {})
        if state.get("following_player") or state.get("follow_player"):
            return "Following Player"
        current = npc.current_action or {}
        action_type = current.get("action_type")
        if action_type == "move":
            params = dict(current.get("parameters", {}))
            if current.get("complete"):
                return "Move complete"
            x = params.get("x")
            y = params.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return f"Moving to ({x:g}, {y:g})"
            return "Moving"
        progress = npc.action_progress or {}
        if progress.get("action"):
            return str(progress["action"])
        if action_type:
            return str(action_type)
        return "Idle"

    def snapshot(self):
        """只读快照。

        世界正在推进（例如每天结束时要调用 AI 编导，可能耗时几十秒）时，
        这里**不会阻塞等待锁**，而是返回上一帧快照——否则前端每秒轮询会
        被卡住，看起来像"页面卡死"。
        """
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            cached = getattr(self, "_last_snapshot", None)
            if cached is not None:
                data = json.loads(json.dumps(cached, ensure_ascii=False))
                # 世界数据可能是上一帧，但"运行/暂停/输入暂停"这些运行标志
                # 必须返回实时值，否则刚点继续/暂停时界面会显示反了。
                live = data.get("open_world")
                if isinstance(live, dict):
                    live["running"] = bool(self._running)
                    live["input_paused"] = bool(self.input_paused)
                    live["tick"] = int(self.tick_index)
                return data
            self._lock.acquire()
            acquired = True
        try:
            data = self._build_snapshot()
            self._last_snapshot = data
            return json.loads(json.dumps(data, ensure_ascii=False))
        finally:
            self._lock.release()

    def _build_snapshot(self):
        with self._lock:
            world = self._world
            return {
                "time": world.time,
                "phase": getattr(world, "phase", night_director.PHASE_DAY),
                "night_director": {
                    "day": getattr(world, "night_director_day", None),
                    "orchestration_day": getattr(world, "daily_orchestration_day", None),
                    "active": getattr(world, "phase", "") == night_director.PHASE_NIGHT_DIRECTOR,
                    "report": (
                        dict(world.night_director_report)
                        if isinstance(getattr(world, "night_director_report", None), dict)
                        else None
                    ),
                },
                "player": {
                    "position": [world.player.x, world.player.y],
                    "inventory": list(world.player.inventory),
                    "money": world.player.money,
                    "energy": world.player.energy,
                    "memory": [dict(entry) for entry in world.player.memory[-8:]],
                },
                "npcs": [
                    {
                        "id": npc.id,
                        "name": npc.name,
                        "role": npc.role,
                        "position": list(world.npc_position(npc.id)),
                        "beliefs": dict(npc.beliefs),
                        "relationships": dict(npc.relationships),
                        "goals": list(npc.goals),
                        "memory": [dict(entry) for entry in npc.memory[-5:]],
                        "personality": list(npc.personality),
                        "character_seed": dict(npc.character_seed),
                        "status": dict(world.characters_state.get(npc.id, {})),
                        "following_player": bool(world.characters_state.get(npc.id, {}).get("following_player") or world.characters_state.get(npc.id, {}).get("follow_player")),
                        "follow_target": world.characters_state.get(npc.id, {}).get("follow_target"),
                        "current_action": self._npc_action_label(world, npc),
                        "action_progress": dict(npc.action_progress),
                        # 只读展示字段（不参与模拟）：目标生命周期状态
                        "goal_states": {key: dict(value) for key, value in npc.goal_states.items()},
                    }
                    for npc in self._visible_npcs(world)
                ],
                "items": [
                    {
                        "id": item.id,
                        "type": item.type,
                        "label": ITEM_LABELS.get(item.type, item.type),
                        "position": [item.x, item.y],
                        "holder": item.holder,
                    }
                    for item in world.items
                ],
                "evidence": [
                    {**dict(entry), "label": EVIDENCE_LABELS.get(entry.get("type"), entry.get("type"))}
                    for entry in world.evidence
                ],
                "event_log": self._build_event_log(world),
                # 只读展示字段：真实发生的对话（NPC 之间 / NPC 独白）
                "dialogues": [dict(entry) for entry in world.dialogues],
                "environment": json.loads(json.dumps(world.environment_state)),
                "timeline": timeline_summary(world),
                "history": [dict(entry) for entry in world.history[-12:]],
                "event_history": [dict(entry) for entry in world.event_history],
                "story_event_history": [dict(entry) for entry in world.story_event_history],
                "objects": self.object_registry.visible_objects(world),
                "observation": self._observation_with_objects(world),
                "open_world": {
                    "tick": self.tick_index,
                    "running": self._running,
                    "input_paused": self.input_paused,
                    "day": int(world.time // DAY_SECONDS) + 1,
                    "candidates": [dict(item) for item in self.last_candidates],
                    "objective": self._current_objective(),
                    "clues": [dict(item) for item in self.clues[-8:]],
                    "director_feed": [dict(item) for item in self.director_feed[-8:]],
                    "last_dialogue_reply": self.last_dialogue_reply,
                    "dialogue_log": [dict(item) for item in self.dialogue_log[-8:]],
                    "dialogue_history_by_npc": self._dialogue_history_by_npc(),
                    "dialogue_session": self._dialogue_session_view(),
                    "last_player_action": dict(self.last_player_action) if self.last_player_action else None,
                    "player_actions": [dict(item) for item in self.player_actions[-80:]],
                    "director": dict(self.director.last_log),
                    "last_story_proposal": dict(self.last_story_proposal) if self.last_story_proposal else None,
                    "daily_report": dict(self.last_director_report) if self.last_director_report else None,
                    "director_reports": [dict(item) for item in self.director_reports[-6:]],
                    "error": self.last_error,
                },
            }

    def _build_event_log(self, world):
        entries = []
        for history in world.history:
            action = history.get("action", "")
            actor = history.get("actor") or "player"
            source = "story" if action in STORY_ACTIONS else ("player" if actor == "player" else "world")
            text = LOG_TEXT_CN.get(action, history.get("content", ""))
            entries.append(
                {
                    "time": history.get("time", world.time),
                    "source": source,
                    "actor": actor,
                    "action": action,
                    "text": text,
                }
            )
        entries.sort(key=lambda item: item["time"])
        return entries[-40:]

    def current(self):
        return {"event": dict(self._last_event), "snapshot": self.snapshot()}

    def _run_loop(self, interval):
        while not self._stop_event.is_set():
            self.tick()
            if self._stop_event.wait(interval):
                break
        with self._lock:
            self._running = False

    def _fail_action(self, message):
        return self._finish_action(False, message, None, "open_world_action")

    def _custom_candidates(self, text, candidates, nearby_objects=None, nearby_npcs=None):
        lowered = text.lower()
        nearby_objects = nearby_objects or []
        nearby_npcs = nearby_npcs or []
        extra = []
        def add(candidate):
            signature = json.dumps(candidate["action"], sort_keys=True)
            if any(item.get("signature") == signature for item in candidates + extra):
                return
            candidate["id"] = f"custom_{len(extra) + 1}"
            candidate["signature"] = signature
            extra.append(candidate)

        ledger = self._world.get_item("item_ledger")
        ledger_visible = ledger is not None and ledger.holder is None and "drawer_open" in self.object_state and self._player_location() == "home"
        if ledger_visible and any(token in lowered for token in ("ledger", "账本", "account", "记录")):
            add({"id": "", "label": "拿走隐藏账本", "action": {"type": "take_ledger"}, "reason": "抽屉已经打开，账本就在眼前。", "risk": "high", "possible_outcome": "村长会怀疑你。", "signature": ""})

        ren_near = any(npc.get("id") == "npc_2" for npc in nearby_npcs)
        if ren_near and any(token in lowered for token in ("protect", "save", "help ren", "保护", "救", "帮助ren")):
            add({"id": "", "label": "保护 Ren", "action": {"type": "protect_ren"}, "reason": "Ren 就在附近，可以立即保护他。", "risk": "medium", "possible_outcome": "改变剧情，阻止 Ren 失踪。", "signature": ""})

        if self.object_state.get("drawer_open") and not self.object_state.get("drawer_searched") and self._player_location() == "home":
            add({"id": "", "label": "打开抽屉", "action": {"type": "open_drawer", "object_id": "mayor_hidden_drawer"}, "reason": "锁已经坏了，现在可以打开抽屉。", "risk": "low", "possible_outcome": "看到抽屉里藏着什么。", "signature": ""})
        if self.object_state.get("drawer_searched") and self.object_state.get("ledger_revealed") and ledger is not None and ledger.holder is None and self._player_location() == "home":
            add({"id": "", "label": "拿走隐藏账本", "action": {"type": "take_ledger"}, "reason": "账本就在抽屉里。", "risk": "high", "possible_outcome": "获得关键证据，但可能被村长发现。", "signature": ""})

        # A previously desired object goal becomes available when the object appears.
        if self.pending_object_goals.get("bookshelf") and any(obj.get("id") == "mayor_bookshelf" for obj in nearby_objects):
            add({"id": "", "label": "检查书架", "action": {"type": "inspect_object", "object_id": "mayor_bookshelf"}, "reason": "你之前想检查书架，现在书架就在附近。", "risk": "low", "possible_outcome": "可能找到普通账本或线索。", "signature": ""})

        if self.object_state.get("need_hammer") and nearby_npcs:
            npc = nearby_npcs[0]
            add({"id": "", "label": f"向 {npc['name']} 借锤子", "action": {"type": "ask_for_hammer", "npc_id": npc["id"]}, "reason": "锁打不开，需要工具帮助。", "risk": "medium", "possible_outcome": "可能借到锤子，也可能暴露给村长。", "signature": ""})

        give_item_id = None
        if self._world.player.inventory:
            if any(token in lowered for token in ("ledger", "账本")) and "item_ledger" in self._world.player.inventory:
                give_item_id = "item_ledger"
            else:
                give_item_id = self._world.player.inventory[0]
        if give_item_id and nearby_npcs and any(token in lowered for token in ("给", "交给", "give", "hand", "交给")):
            npc = nearby_npcs[0]
            item = self._world.get_item(give_item_id)
            label = item.label if hasattr(item, "label") else item.type if item else give_item_id
            add({"id": "", "label": f"把 {label} 交给 {npc['name']}", "action": {"type": "give_item", "item_id": give_item_id, "target": npc["id"]}, "reason": "把物品交给附近 NPC。", "risk": "medium", "possible_outcome": "对方会接受或拒绝，并影响关系。", "signature": ""})

        return extra + candidates

    def _update_pending_object_goals(self, text, nearby_objects):
        object_ids = {obj.get("id") for obj in nearby_objects}
        if any(token in text for token in ("书架", "bookshelf")):
            self.pending_object_goals["bookshelf"] = "mayor_bookshelf" not in object_ids
        if any(token in text for token in ("锁", "lock", "砸")):
            self.pending_object_goals["lock"] = "mayor_desk_lock" not in object_ids

    def _apply_protection_timeline_changes(self):
        try:
            apply_timeline_change(
                self.world,
                {"type": "timeline_change", "changes": [{"event": "ren_lost", "status": "cancelled"}], "npc_changes": []},
            )
        except ValueError:
            pass
        self._ensure_story_event("player_saved_Ren", self.world.time, "The player protected Ren from the mayor's hunt.", "triggered")
        self._ensure_story_event("village_investigation", self.world.time + DAY_SECONDS, "Villagers begin investigating the mayor.", "pending")

    def _ensure_story_event(self, event_id, trigger_time, description, status):
        timeline = self.world.story_timeline
        if not isinstance(timeline, StoryTimeline):
            timeline = StoryTimeline(timeline)
            self.world.story_timeline = timeline
        if timeline.get_event(event_id) is not None:
            return
        timeline.add_event({
            "event_id": event_id,
            "title": description,
            "description": description,
            "trigger_at": trigger_time,
            "status": status,
            "source": "open_world",
            "effects": [],
        })

    def _merge_candidates(self, primary, secondary):
        merged = list(primary)
        seen = {item.get("signature") for item in merged}
        for item in secondary:
            signature = item.get("signature")
            if signature not in seen:
                item["id"] = f"candidate_{len(merged) + 1}"
                merged.append(item)
                seen.add(signature)
        return merged

    def _observation_with_objects(self, world):
        observation = self.controller.observe(world)
        visible_ids = {npc.id for npc in self._visible_npcs(world)}
        observation["npcs"] = [npc for npc in observation["npcs"] if npc["id"] in visible_ids]
        observation["objects"] = self.object_registry.visible_objects(world)
        return observation

    def _visible_npcs(self, world):
        visible = []
        for npc in world.npcs:
            state = world.characters_state.get(npc.id, {})
            if npc.id == "npc_2" and not state.get("alive", True):
                continue
            if npc.id == "npc_1" and state.get("fled"):
                continue
            visible.append(npc)
        return visible

    def _finish_action(self, success, message, parsed, raw_text):
        self.last_error = None if success else message
        # 玩家行为的唯一可信来源：只有玩家 Action 管线会走到这里。
        action_type = ""
        if isinstance(parsed, dict):
            action_type = str(parsed.get("type") or parsed.get("action_type") or "")
        self.player_actions.append({
            "time": self._world.time,
            "action": action_type,
            "raw_text": str(raw_text or ""),
            "success": bool(success),
            "message": str(message or ""),
            "category": "player_action",
        })
        self.player_actions = self.player_actions[-200:]
        if success:
            self.last_player_action = {"time": self._world.time, "action": parsed, "raw_text": raw_text, "message": message}
            self._last_event = {"time": self._world.time, "type": "player_action", "description": message, "action": parsed}
            self.resume_after_input()
            if parsed and parsed.get("type") in {"search_object", "inspect_object", "break_object", "open_drawer", "protect_ren", "take_ledger"}:
                self._director_react(parsed.get("type"))
        return {"success": success, "message": message, "action": parsed, "error": None if success else message, "step": self.current()}

    def _record_clue(self, content):
        clue = {"time": self._world.time, "text": content}
        self.clues.append(clue)
        self._world.player.memory.append({"time": self._world.time, "type": "clue", "content": content, "context": {}})

    def _director_react(self, reason):
        event = self.director.generate_event(self._world, context="player_action")
        if event is None:
            fallback = getattr(self.director, "_deterministic_event", None)
            if fallback is not None:
                event = fallback(self._world, context="player_action")
        if event is None:
            return
        try:
            execute_event(self._world, event)
        except Exception:
            return
        summary = f"AI Director：因为玩家 {reason}，世界发生了新的变化。"
        self.director_feed.append({"time": self._world.time, "text": summary, "event_id": event.get("event_id")})
        self.last_director_report = dict(getattr(self, "last_director_report", {}) or {})

    def _current_objective(self):
        ledger = self._world.get_item("item_ledger")
        if ledger is not None and ledger.holder == "player":
            return "你已经拿到账本，去找 Ren 或审计员 Ivy。"
        if self.object_state.get("drawer_searched") and self.object_state.get("ledger_revealed"):
            return "打开抽屉后看到了账本，把它拿走。"
        if self.object_state.get("drawer_open") and not self.object_state.get("drawer_searched"):
            return "抽屉已经打开，看看里面有什么。"
        if self.object_state.get("mayor_hidden_drawer"):
            return "你在办公室发现了暗格，想办法打开它。"
        timeline_ids = {entry["id"]: entry["status"] for entry in self._world.story_timeline}
        if self.story_phase >= 1:
            if timeline_ids.get("mira_seeks_truth") == "triggered":
                return "第一幕已经结束，去和 Mira 谈谈她接下来想查什么。"
            if timeline_ids.get("village_manhunt") == "pending":
                return "寻找逃跑村长的线索。"
            if timeline_ids.get("new_arrival") == "pending":
                return "留意新出现的陌生旅人。"
            if timeline_ids.get("village_heals") == "pending":
                return "帮助村庄恢复正常。"
        if self._player_location() == "home":
            return "搜索村长的桌子、书架和锁，寻找账本线索。"
        return "四处探索，寻找村长办公室。"

    def _record_player_memory(self, kind, content, context):
        self._world.player.memory.append({"time": self._world.time, "type": kind, "content": content, "context": context})

        self._world.player.memory.append({"time": self._world.time, "type": kind, "content": content, "context": context})

    def _player_location(self):
        return self._world.current_explore_location() or "open_ground"

    def _update_weather_state(self):
        weather = self._world.environment_state.setdefault("weather", {})
        rem = self._world.time % DAY_SECONDS
        if rem < 6 * 3600:
            condition, rain, temperature = "Sunny", False, 20.0
        elif rem < 12 * 3600:
            condition, rain, temperature = "Cloudy", False, 16.0
        elif rem < 18 * 3600:
            condition, rain, temperature = "Rain", True, 12.0
        else:
            condition, rain, temperature = "Cloudy", False, 14.0
        if weather.get("condition") != condition:
            weather["condition"] = condition
            weather["rain"] = rain
            weather["temperature"] = temperature
            self._world.add_history("weather_changed", "world", f"Weather changed to {condition}.", actor="environment")

    def _update_npcs_with_planner(self):
        """Run one planner step per NPC through the shared Action pipeline.

        Each NPC plans one Action from its own goals/memory/observation. The
        Action must pass the shared ActionValidator and ActionExecutor before it
        can touch WorldState, then the result is fed back as memory/progress.
        """
        for npc in list(self._world.npcs):
            state = self._world.characters_state.get(npc.id, {})
            if not state.get("alive", True):
                self._terminate_npc_goals(npc)
                continue
            self._apply_goal_lifecycle(npc)
            # 正在和玩家对话的 NPC 停在原地；只有"非常紧急"的事才继续行动。
            if npc.id == self._dialogue_partner_id() and not self._npc_has_urgent_business(npc):
                npc.current_action = {}
                continue
            action = self.npc_planner.plan_next_action(self._world, npc.id)
            for raw_goal in list(getattr(self.npc_planner, "last_unplanned_goals", []) or []):
                # An AI goal the Planner keeps failing to action must fall into
                # the existing lifecycle instead of outranking work forever.
                if self._is_ai_director_goal(npc, raw_goal):
                    self._register_goal_attempt(npc, raw_goal, f"blocked AI goal: {raw_goal}")
            if action is None:
                continue
            validation = self.action_validator.validate(self._world, action)
            if not validation.valid:
                npc.current_action = {}
                npc.action_progress = {
                    "action": action.action_type,
                    "success": False,
                    "error": validation.reason,
                    "time": self._world.time,
                }
                self._register_goal_failure(npc, action, validation.reason)
                self._world.add_history(
                    "npc_action_rejected",
                    npc.id,
                    f"{npc.name}: {validation.reason}",
                    actor=npc.id,
                )
                continue
            result = self.action_executor.execute(
                self._world,
                action,
                validation=validation,
                runtime=self,
            )
            self._apply_npc_action_feedback(npc, action, result)

    def _apply_npc_action_feedback(self, npc, action, result):
        goal = dict(action.source_intent or {})
        goal_type = goal.get("goal_type")
        raw_goal = str(goal.get("raw") or "")
        if result.success:
            no_progress = (
                action.action_type == "search" and not result.data.get("evidence_discovered")
            ) or (
                action.action_type == "investigate" and not result.data.get("evidence_id")
            )
            if action.action_type == "search" and goal_type == "find_item":
                # The planner only searches when it cannot perceive the item, so
                # a search never completes this goal: count it as an attempt.
                self._register_goal_failure(
                    npc, action, f"searched but could not find {goal.get('target_id')}"
                )
            elif no_progress and self._is_ai_director_goal(npc, raw_goal):
                # An AI goal that keeps making no progress must fall into the
                # existing attempt/failure lifecycle instead of holding the
                # Planner at priority 0.8 forever.
                self._register_goal_failure(
                    npc, action, f"no progress on AI goal: {raw_goal}"
                )
            else:
                self._clear_goal_attempts(npc, raw_goal)
            if action.action_type == "take":
                item_id = result.data.get("item_id") or action.target_id
                npc.add_memory({
                    "time": self._world.time,
                    "source": "planner",
                    "content": f"Took {item_id}.",
                    "goal_type": goal_type,
                })
                self._complete_npc_goal(npc, goal_type, goal.get("target_id"))
            elif action.action_type in {"search", "inspect", "investigate", "talk"}:
                npc.add_memory({
                    "time": self._world.time,
                    "source": "planner",
                    "content": result.message or action.reason,
                    "goal_type": goal_type,
                })
                if action.action_type == "talk" and goal_type in {"talk_to", "help_npc", "find_npc"}:
                    self._resolve_npc_goal(npc, raw_goal, "completed", "talked to the target")
                discovered = bool(result.data.get("evidence_id")) or bool(result.data.get("evidence_discovered"))
                if discovered and goal_type == "investigate":
                    # Finding evidence satisfies the investigation and frees the
                    # planner to pick up the follow-up goal the discovery queued.
                    self._resolve_npc_goal(npc, raw_goal, "completed", "evidence discovered")
            npc.action_progress = {
                "action": action.action_type,
                "success": True,
                "goal_type": goal_type,
                "time": self._world.time,
            }
        else:
            npc.action_progress = {
                "action": action.action_type,
                "success": False,
                "error": result.message,
                "goal_type": goal_type,
                "time": self._world.time,
            }
            if action.action_type == "move":
                npc.current_action = {}
            self._register_goal_failure(npc, action, result.message)

    def _terminate_npc_goals(self, npc, status="failed", reason="npc is not alive"):
        """A dead/absent NPC keeps no active goals and produces no action."""
        # Clear the suspension stack first so termination can never "resume".
        npc.active_goal = None
        npc.suspended_goal_stack = []
        for raw in list(npc.goals):
            entry = self._goal_states(npc).get(str(raw), {})
            if entry.get("status") in {"completed", "failed", "cancelled"}:
                continue
            self._resolve_npc_goal(npc, str(raw), status, reason)
        npc.current_action = {}
        npc.action_progress = {
            "action": "none",
            "success": False,
            "error": reason,
            "time": self._world.time,
        }

    def _tag_ai_director_goals(self, effects, source=AI_GOAL_SOURCE):
        """Mark goals an AI event just queued so the Planner can prefer them.

        Only goals that really landed in npc.goals are tagged, and the tag is
        written once. The Planner derives the boost from this tag, so priority
        never creeps up tick after tick.
        """
        for effect in effects or []:
            if not isinstance(effect, dict) or effect.get("type") != "add_goal":
                continue
            goal = effect.get("goal")
            npc = self._world.get_npc(effect.get("npc_id"))
            if npc is None or not isinstance(goal, str) or goal not in npc.goals:
                continue
            entry = self._goal_states(npc).setdefault(
                str(goal), {"status": "active", "attempts": 0, "reason": ""}
            )
            entry["source"] = source

    def _tag_goals_from_new_story_events(self, history_start):
        """Tag goals created by AI-sourced StoryEvents resolved since the index."""
        for entry in self._world.story_event_history[history_start:]:
            if entry.get("final_status") not in {"triggered", "completed"}:
                continue
            event_id = entry.get("event_id")
            event = self._world.story_timeline.get_event(event_id) if event_id else None
            if not event or event.get("source") != "ai_director":
                continue
            self._tag_ai_director_goals(event.get("effects"))

    def _is_ai_director_goal(self, npc, raw_goal):
        entry = self._goal_states(npc).get(str(raw_goal or ""))
        return isinstance(entry, dict) and entry.get("source") == AI_GOAL_SOURCE

    def _goal_states(self, npc):
        if not isinstance(getattr(npc, "goal_states", None), dict):
            npc.goal_states = {}
        return npc.goal_states

    def _apply_goal_lifecycle(self, npc):
        for outcome in self.npc_planner.evaluate_goals(self._world, npc.id):
            self._resolve_npc_goal(npc, outcome["goal"], outcome["status"], outcome["reason"])

    def _resolve_npc_goal(self, npc, raw_goal, status, reason=""):
        raw_goal = str(raw_goal or "")
        if not raw_goal:
            return
        # The same intent can live in npc.goals under several aliases, e.g. the
        # story seed "find ledger" and an LLM goal "find_item:item_ledger".
        # Terminal states must collapse those aliases, otherwise the planner
        # simply re-runs the identical search under the next alias forever.
        keys = self._equivalent_goal_keys(npc, raw_goal) if status in {"completed", "failed"} else [raw_goal]
        for key in keys:
            self._set_goal_state(npc, key, status, reason)
        if status in {"completed", "failed"}:
            self._resume_after_active_goal(npc, keys)

    def _resume_after_active_goal(self, npc, finished_keys):
        """When the active goal ends, resume the latest suspended goal (12.3)."""
        active = getattr(npc, "active_goal", None)
        if not active:
            return None
        finished = {str(key) for key in finished_keys}
        if active not in finished:
            return None
        npc.active_goal = None
        return self.goal_manager.resume_next_suspended(self._world, npc)

    def _set_goal_state(self, npc, raw_goal, status, reason):
        states = self._goal_states(npc)
        entry = states.setdefault(raw_goal, {"status": "active", "attempts": 0, "reason": ""})
        if entry.get("status") in {"completed", "failed"} and entry.get("status") != status:
            # Never overwrite a terminal outcome with a conflicting one.
            return
        changed = entry.get("status") != status
        entry["status"] = status
        entry["reason"] = str(reason or entry.get("reason", ""))
        # The goal stays in npc.goals (story conditions may still reference it);
        # the planner skips terminal goals and clears their current action.
        current = npc.current_action or {}
        if str((current.get("source_intent") or {}).get("raw", "")) == raw_goal:
            npc.current_action = {}
        if changed:
            npc.add_memory({
                "time": self._world.time,
                "source": "planner",
                "content": f"Goal {status}: {raw_goal}",
                "goal_state": status,
            })

    def _goal_identity(self, goal):
        """Comparable identity so goal aliases sharing a target collapse."""
        target = str(goal.target_id or "").lower()
        if goal.goal_type == "find_item" and target:
            item_ids = sorted({
                str(item.id).lower()
                for item in self._world.items
                if target in {str(item.id).lower(), str(item.type).lower()}
            })
            if item_ids:
                return ("find_item", tuple(item_ids))
        return (goal.goal_type, target)

    def _equivalent_goal_keys(self, npc, raw_goal):
        """Raw goal strings in npc.goals that mean the same thing as raw_goal."""
        parsed = self.npc_planner.parse_goal(raw_goal)
        identity = self._goal_identity(parsed)
        keys = [str(raw_goal)]
        if not identity[1] and parsed.goal_type != "wait":
            return keys
        if parsed.goal_type == "unknown":
            return keys
        for raw in list(npc.goals):
            key = str(raw)
            if key in keys:
                continue
            if self._goal_identity(self.npc_planner.parse_goal(raw)) == identity:
                keys.append(key)
        return keys

    def _clear_goal_attempts(self, npc, raw_goal):
        if not raw_goal:
            return
        entry = self._goal_states(npc).get(raw_goal)
        if entry is not None:
            entry["attempts"] = 0

    def _register_goal_failure(self, npc, action, message):
        self._register_goal_attempt(npc, (action.source_intent or {}).get("raw"), message)

    def _register_goal_attempt(self, npc, raw_goal, message):
        raw_goal = str(raw_goal or "")
        if not raw_goal:
            return
        states = self._goal_states(npc)
        entry = states.setdefault(raw_goal, {"status": "active", "attempts": 0, "reason": ""})
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["reason"] = str(message or "action failed")
        if entry["attempts"] >= self.npc_planner.MAX_GOAL_ATTEMPTS:
            self._resolve_npc_goal(npc, raw_goal, "failed", entry["reason"])

    def _complete_npc_goal(self, npc, goal_type, target_id):
        if not goal_type:
            return
        target = str(target_id or "").lower()
        remaining = []
        for goal in npc.goals:
            parsed = self.npc_planner.parse_goal(goal)
            if parsed.goal_type == goal_type and str(parsed.target_id or "").lower() == target:
                continue
            remaining.append(goal)
        npc.goals = remaining

    def _update_npc_positions_by_goals(self):
        moves = {
            "npc_2": "find ledger",
            "npc_1": "stop Ren",
            "npc_3": "help Ren",
            "npc_4": "investigate mayor",
        }
        for npc in self._world.npcs:
            state = self._world.characters_state.get(npc.id, {})
            if state.get("following_player") or state.get("follow_player") or "follow player" in npc.goals:
                self._step_npc_toward(npc, (self._world.player.x, self._world.player.y), 2.0)
        for npc_id, goal in moves.items():
            npc = self._world.get_npc(npc_id)
            state = self._world.characters_state.get(npc_id, {})
            if npc is None or goal not in npc.goals:
                continue
            if state.get("following_player") or state.get("follow_player") or "follow player" in npc.goals:
                continue
            if npc_id == "npc_2" and not state.get("alive", True):
                continue
            if npc_id == "npc_1" and state.get("fled"):
                continue
            target = OFFICE_POSITION if npc_id in {"npc_2", "npc_4"} else self._world.npc_position("npc_2")
            self._step_npc_toward(npc, target, 1.5)

    def _step_npc_toward(self, npc, target, step):
        current = self._world.npc_position(npc.id)
        dx = target[0] - current[0]
        dy = target[1] - current[1]
        distance = (dx * dx + dy * dy) ** 0.5
        if distance == 0:
            return
        scale = min(1.0, step / distance)
        npc.position_override = [current[0] + dx * scale, current[1] + dy * scale]
        npc.x, npc.y = npc.position_override

    def _maybe_auto_end_dialogue_session(self):
        """End a dialogue session when the partner left or it ran too long.

        The manual End Conversation path stays available; this only guarantees
        the world does not stall waiting for a click.
        """
        session = getattr(self._world, "dialogue_session", None)
        if not isinstance(session, dict) or not session.get("active"):
            return None
        npc_id = session.get("npc_id")
        npc = self._world.get_npc(npc_id) if npc_id else None
        if npc is None:
            return self.end_dialogue_session()
        started_at = session.get("started_at")
        max_seconds = getattr(self, "dialogue_session_max_seconds", DIALOGUE_SESSION_MAX_SECONDS)
        max_distance = getattr(self, "dialogue_session_max_distance", DIALOGUE_SESSION_MAX_DISTANCE)
        too_long = (
            max_seconds is not None
            and started_at is not None
            and (self._world.time - float(started_at)) >= float(max_seconds)
        )
        far = max_distance is not None and self._world.distance_between(
            (self._world.player.x, self._world.player.y),
            self._world.npc_position(npc.id),
        ) > float(max_distance)
        if too_long or far:
            return self.end_dialogue_session()
        return None

    def _begin_dialogue_session(self, npc_id):
        """Mark the current NPC as the protected dialogue partner."""
        world = self._world
        session = getattr(world, "dialogue_session", None)
        if not isinstance(session, dict):
            session = {"active": False, "npc_id": None, "started_at": None}
            world.dialogue_session = session
        if session.get("active") and session.get("npc_id") != npc_id:
            # The player explicitly switched NPC: release the previous session.
            self.end_dialogue_session()
        if not session.get("active"):
            session["active"] = True
            session["npc_id"] = npc_id
            session["started_at"] = world.time
        return session

    def _dialogue_partner_id(self):
        """当前正在对话的 NPC（没有会话时返回 None）。"""
        session = getattr(self._world, "dialogue_session", None)
        if isinstance(session, dict) and session.get("active"):
            return session.get("npc_id")
        return None

    def _tick_time_seconds(self):
        """对话进行中世界时间流速放慢，其余时间保持原速。"""
        if self._dialogue_partner_id():
            scale = float(getattr(self, "dialogue_time_scale", DIALOGUE_TIME_SCALE))
            return max(1.0, float(self.tick_seconds) * scale)
        return float(self.tick_seconds)

    def _npc_has_urgent_business(self, npc):
        """NPC 是否有"非常紧急"的真实理由可以离开对话（否则停住）。"""
        state = self._world.characters_state.get(npc.id, {})
        for flag in URGENT_NPC_STATES:
            if state.get(flag):
                return True
        for goal in list(getattr(npc, "goals", []) or []):
            text = str(goal).strip().lower()
            if text in URGENT_NPC_GOALS or text.startswith("escape:"):
                return True
        return False

    def _dialogue_session_view(self):
        session = getattr(self._world, "dialogue_session", None) or {}
        npc_id = session.get("npc_id") if session.get("active") else None
        npc = self._world.get_npc(npc_id) if npc_id else None
        return {
            "active": bool(session.get("active")),
            "npc_id": npc_id,
            "npc_name": npc.name if npc is not None else None,
            "started_at": session.get("started_at"),
        }

    def _dialogue_history_by_npc(self):
        grouped = {}
        for entry in self.dialogue_log[-40:]:
            npc_id = entry.get("npc_id")
            if not npc_id:
                continue
            grouped.setdefault(npc_id, []).append(dict(entry))
        return {npc_id: entries[-12:] for npc_id, entries in grouped.items()}

    def end_dialogue_session(self):
        """End the protected session and re-check any deferred StoryEvents."""
        with self._lock:
            session = getattr(self._world, "dialogue_session", None)
            ended_npc = None
            if isinstance(session, dict) and session.get("active"):
                ended_npc = session.get("npc_id")
                session["active"] = False
                session["npc_id"] = None
                session["started_at"] = None
            before = len(self._world.story_event_history)
            self._world.check_story_events()
            triggered = [dict(entry) for entry in self._world.story_event_history[before:]]
            self.last_story_events = triggered
            return {
                "success": True,
                "message": (
                    f"Ended dialogue session with {ended_npc}."
                    if ended_npc else "No active dialogue session."
                ),
                "ended_npc_id": ended_npc,
                "triggered_events": [entry.get("event_id") for entry in triggered],
                "step": self.current(),
            }

    def run_director_cycle(self):
        """Collect context, ask the Director for a future StoryEvent, add it.

        This is the explicit, testable entry point. The daily tick decides when
        to call it; the Director only proposes data and StoryTimeline stores it.
        """
        with self._lock:
            proposer = getattr(self.director, "propose_story_event", None)
            if proposer is None:
                result = {
                    "success": False,
                    "added": False,
                    "event": None,
                    "reason": "director_does_not_support_proposals",
                }
            else:
                result = proposer(self._world)
            self.last_story_proposal = result
            return result

    def _maybe_daily_director(self):
        """Legacy daily director (kept for compatibility).

        In the OpenWorld runtime this never performs a daily orchestration: the
        22:00-24:00 Night Director is the single entry, and it records the
        planned day in ``world.daily_orchestration_day``. Calling this method
        directly (e.g. from a test) still works when no orchestration is
        recorded for that day.
        """
        day = int(self._world.time // DAY_SECONDS)
        if day == self._last_director_day:
            return
        done = getattr(self._world, "daily_orchestration_day", None)
        if done is not None and int(done) >= day:
            self._last_director_day = day
            return
        self._last_director_day = day
        self._maybe_continue_story()
        event = self.director.generate_event(self._world, context="daily_director")
        event_source = getattr(self.director, "last_event_source", "deterministic")
        fallback_reason = None
        llm_rejection = getattr(self.director, "last_llm_rejection", None)
        if event is not None:
            try:
                execute_event(self._world, event)
            except Exception as exc:
                fallback_reason = f"{type(exc).__name__}: {exc}"
                if event_source == "llm":
                    llm_rejection = llm_rejection or {
                        "source": "ai_director",
                        "status": "rejected",
                        "reason": fallback_reason,
                        "validation_error": type(exc).__name__,
                        "original": event,
                        "normalized": None,
                        "stage": "daily_event_executor",
                    }
                fallback = getattr(self.director, "_deterministic_event", None)
                if fallback is not None:
                    try:
                        event = fallback(self._world, context="daily_director")
                        execute_event(self._world, event)
                        event_source = "deterministic_fallback"
                    except Exception:
                        event = None
                        event_source = "failed"
                else:
                    event = None
                    event_source = "failed"
            if event is not None:
                if event_source == "llm":
                    # An AI-authored event? Its goals get the short-term edge.
                    self._tag_ai_director_goals(event.get("effects"))
                if event_source == "llm":
                    feed_text = "AI Director 今日剧情（LLM）：" + str(event.get("event_id"))
                elif event_source == "deterministic_fallback":
                    feed_text = (
                        "LLM StoryEvent rejected; deterministic fallback used. "
                        f"({fallback_reason})"
                    )
                else:
                    feed_text = "AI Director 今日剧情：" + str(event.get("event_id"))
                self.director_feed.append({
                    "time": self._world.time,
                    "text": feed_text,
                    "event_id": event.get("event_id"),
                    "source": event_source,
                })
        story_proposal = self.run_director_cycle()
        if story_proposal.get("added"):
            proposal_id = story_proposal["event"]["event_id"]
            self.director_feed.append({
                "time": self._world.time,
                "text": "AI Director 新增未来剧情：" + proposal_id,
                "event_id": proposal_id,
                "source": "ai_director",
                "normalization": list(story_proposal.get("normalization") or []),
            })
        elif story_proposal.get("rejection"):
            rejection = story_proposal["rejection"]
            self.director_feed.append({
                "time": self._world.time,
                "text": "LLM StoryEvent rejected: " + str(rejection.get("reason")),
                "source": "ai_director",
                "status": "rejected",
                "event_id": None,
            })
        forecast = self.director.generate_forecast(self._world) if hasattr(self.director, "generate_forecast") else ""
        ren_npc = self._world.get_npc("npc_2")
        mayor_npc = self._world.get_npc("npc_1")
        evidence_summary = [e.get("type") for e in self._world.evidence[-5:]]
        self.last_director_report = {
            "day": day + 1,
            "event": event,
            "story_proposal": story_proposal,
            "director": dict(self.director.last_log),
            "ren_beliefs": dict(ren_npc.beliefs) if ren_npc else {},
            "mayor_beliefs": dict(mayor_npc.beliefs) if mayor_npc else {},
            "timeline": timeline_summary(self._world),
            "observation": "玩家当前在 " + (self._player_location() or "空地"),
            "evidence": evidence_summary,
            "npc_belief": {
                "Mayor": dict(mayor_npc.beliefs) if mayor_npc else {},
                "Ren": dict(ren_npc.beliefs) if ren_npc else {},
            },
            "reasoning": "市长可能试图夺回证据，Ren 对玩家更信任。",
            "generated_event": event["event_id"] if event else "无新事件",
            "event_source": event_source,
            "fallback_reason": fallback_reason,
            "llm_rejection": llm_rejection,
            "story_proposal_rejected": bool(story_proposal.get("rejection")),
            "narrative": self._daily_narrative(day + 1),
            "forecast": forecast,
        }
        self.director_reports.append(self.last_director_report)

    def _maybe_continue_story(self):
        primary_ids = {
            "ren_discovers_ledger",
            "ren_seeks_evidence",
            "mayor_notices",
            "ren_pursued",
            "ren_lost",
            "external_audit",
            "mayor_flees",
        }
        statuses = {entry["id"]: entry["status"] for entry in self._world.story_timeline}
        primary_done = all(
            entry_id in statuses and statuses[entry_id] not in {"pending", "deferred"}
            for entry_id in primary_ids
        )
        ren_lost_triggered = statuses.get("ren_lost") == "triggered"
        if not primary_done and not ren_lost_triggered:
            return

        if self.story_phase == 0:
            self.story_phase = 1
            ren_dead = not self._world.characters_state.get("npc_2", {}).get("alive", True)
            mayor_fled = self._world.characters_state.get("npc_1", {}).get("fled")
            if ren_dead or ren_lost_triggered:
                self._ensure_story_event("mira_seeks_truth", self._world.time, "Mira 决定追查 Ren 失踪的真相。", "triggered")
                self._record_clue("Ren 失踪后，Mira 决定继续追查村长。")
                self._ensure_story_event("village_manhunt", self._world.time + DAY_SECONDS, "村民开始追捕逃跑的村长。", "pending")
                self._ensure_story_event("new_arrival", self._world.time + 2 * DAY_SECONDS, "一名陌生旅人带着新线索来到村庄。", "pending")
            if mayor_fled:
                self._ensure_story_event("village_manhunt", self._world.time + DAY_SECONDS, "村民开始追捕逃跑的村长。", "pending")
                self._ensure_story_event("new_arrival", self._world.time + 2 * DAY_SECONDS, "一名陌生旅人带着新线索来到村庄。", "pending")
            if not ren_dead and not mayor_fled and not ren_lost_triggered:
                self._ensure_story_event("village_heals", self._world.time + DAY_SECONDS, "村庄开始恢复正常，但账目问题仍未解决。", "pending")
            self._record_clue("旧故事已经结束，新的故事线正在展开。")
        elif self.story_phase == 1:
            second_ids = {"mira_seeks_truth", "village_manhunt", "new_arrival", "village_heals"}
            second_done = all(
                entry_id in statuses and statuses[entry_id] not in {"pending", "deferred"}
                for entry_id in second_ids
                if entry_id in statuses
            )
            if second_done:
                self.story_phase = 2
                self._ensure_story_event("second_chapter", self._world.time, "新的篇章开始了。", "triggered")
                self._record_clue("第二段故事开始了。")

    def _daily_narrative(self, day):
        triggered = [
            entry["id"]
            for entry in self._world.story_timeline
            if entry["status"] == "triggered"
        ]
        evidence = [e.get("label", e.get("type")) for e in self._world.evidence[-4:]]
        mayor = self._world.get_npc("npc_1")
        ren = self._world.get_npc("npc_2")
        parts = [f"第 {day} 天，村子里的事情又向前推进了一步。"]
        if "ren_discovers_ledger" in triggered:
            parts.append("Ren 发现了被改动的账本，开始怀疑村长。")
        if "mayor_notices" in triggered:
            parts.append("村长察觉到 Ren 的调查，气氛变得紧张。")
        if "ren_pursued" in triggered:
            parts.append("Ren 被人追捕，处境越来越危险。")
        if "ren_lost" in triggered and not self._world.characters_state.get("npc_2", {}).get("alive", True):
            parts.append("Ren 最终还是失踪了，村庄失去了最后的目击者。")
        if "external_audit" in triggered:
            parts.append("外部审计员终于进入村庄，真相开始逼近。")
        if "mayor_flees" in triggered:
            parts.append("村长选择逃跑，留下了一堆无法解释的账目。")
        if "ren_lost" in triggered and not self._world.characters_state.get("npc_2", {}).get("alive", True):
            parts.append("Ren 的朋友们开始怀疑村长，并决定继续追查真相。")
        if self._world.get_npc("npc_4") is not None and "demand answers" in self._world.get_npc("npc_4").goals:
            parts.append("审计员要求村长给出解释，村庄的下一段故事由此开始。")
        if self.story_phase >= 1:
            parts.append("接下来，Mira 会追查 Ren 失踪的真相，Ivy 会核对村庄的资金流向，新的线索可能把玩家引向一个陌生旅人。")
        goal_text = {
            "find ledger": "继续追查账本",
            "stop Ren": "阻止 Ren 调查",
            "frame Ren": "寻找机会陷害 Ren",
            "investigate mayor": "核对村长的账目",
            "help Ren": "帮助 Ren",
            "follow player": "跟随玩家行动",
            "demand answers": "要求村庄给出解释",
        }
        for npc in self._visible_npcs(self._world):
            if npc.goals:
                action = goal_text.get(npc.goals[0], npc.goals[0].replace("_", " "))
                parts.append(f"{npc.name} 接下来会{action}。")
        if evidence:
            parts.append("现场留下的证据包括：" + "、".join(evidence) + "。")
        if mayor is not None and mayor.beliefs:
            parts.append("村长似乎对玩家越来越警惕。")
        if ren is not None and "player_helpful" in ren.beliefs:
            parts.append("Ren 开始信任玩家，愿意透露更多线索。")
        return "".join(parts)

    def _describe_tick(self):
        if self.last_story_events:
            return f"Timeline event: {self.last_story_events[0]['event_id']}."
        weather = self._world.environment_state["weather"].get("condition", "Sunny")
        return f"World advanced to Day {int(self._world.time // DAY_SECONDS) + 1} ({weather})."














