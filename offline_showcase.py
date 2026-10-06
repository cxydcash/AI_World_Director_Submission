# -*- coding: utf-8 -*-
"""AI World Director - Offline Showcase.

This script is a no-API, no-network terminal showcase for reviewers. It reuses
the real OpenWorldEngine, StoryTimeline, GoalManager, Event DSL and NPC Planner.
Only the nightly Director output is a deterministic offline fixture; no claim is
made that a live LLM generated it.
"""

import os
from unittest.mock import patch

# Make the showcase independent from a local .env or API-key shell setup.
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("LLM_BASE_URL", None)
os.environ.pop("LLM_MODEL", None)

import demo_step12_8
import open_world
import plot_deviation


BANNER = """\
==================================================
AI World Director
Offline Showcase
无需 API Key / 无需网络
==================================================
"""

DISCLAIMER = """\
本展示版使用预先设计的确定性 Director 输出，用于展示 AI World Director 的
世界状态、剧情事件、NPC Goal 与剧情偏离机制。真实 LLM Director 使用
DeepSeek API，在正式版本中负责根据 WorldState 生成新的 StoryEvent / Goal /
Mystery。
"""


class OfflineShowcaseProvider(demo_step12_8.RouteNightProvider):
    """Deterministic Night Director fixture used only by this showcase."""

    def plan_next_day(self, context):
        report = super().plan_next_day(context)
        events = report.get("story_events") or []
        if not events:
            return report

        event = events[0]
        event_id = event.get("event_id")
        if event_id == "ledger_recovery":
            # This is an explicitly offline fixture: it exercises the existing
            # add_evidence Event DSL path and the existing mystery/story-seed
            # path, without pretending to be a live LLM response.
            event.setdefault("effects", []).append({
                "type": "add_evidence",
                "evidence_id": "ev_ledger_copy_showcase",
                "evidence_type": "ledger_copy",
                "x": 22.0,
                "y": 22.0,
                "description": "一本带有缺页痕迹、与村长办公室账本不完全一致的账本副本。",
                "source_event": event_id,
            })
            report.setdefault("new_mysteries", []).append({
                "type": "rumor",
                "summary": "村长夜间会见的陌生人究竟是谁？",
            })
        elif event_id == "quiet_manhunt":
            report.setdefault("new_mysteries", []).append({
                "type": "rumor",
                "summary": "Ren 失踪前最后见过的人是谁？",
            })
        return report


def _new_engine(provider):
    """Create a real OpenWorldEngine with .env loading disabled for this run."""
    with patch.object(open_world, "load_environment", lambda: None):
        engine = open_world.OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = provider
    engine.npc_goal_provider = None
    engine.tick_seconds = 3600.0
    engine._last_director_day = 0
    return engine


def _format_time(seconds):
    seconds = float(seconds)
    day = int(seconds // 86400) + 1
    hour = int((seconds % 86400) // 3600)
    minute = int((seconds % 3600) // 60)
    return f"Day {day} {hour:02d}:{minute:02d}"


def _goal_text(npc):
    labels = []
    for raw in list(getattr(npc, "goals", []) or []):
        state = (getattr(npc, "goal_states", None) or {}).get(raw, {})
        status = state.get("status") if isinstance(state, dict) else None
        labels.append(f"{raw}[{status}]" if status else str(raw))
    return "；".join(labels) if labels else "无"


def _counts(engine):
    world = engine.world
    return {
        "npc": len(world.npcs),
        "evidence": len(world.evidence),
        "goals": sum(len(list(getattr(npc, "goals", []) or [])) for npc in world.npcs),
        "active_goals": sum(1 for npc in world.npcs if getattr(npc, "active_goal", None)),
        "story_events": len(world.story_timeline),
        "triggered_events": sum(
            1 for event in world.story_timeline
            if event.get("status") in {"triggered", "completed"}
        ),
        "mysteries": len(world.story_seeds),
    }


def _print_counter(label, counts):
    print(
        f"{label}: NPC={counts['npc']} | Evidence={counts['evidence']} | "
        f"Goals={counts['goals']} | ActiveGoals={counts['active_goals']} | "
        f"StoryEvents={counts['story_events']} | Triggered={counts['triggered_events']} | "
        f"Mysteries={counts['mysteries']}"
    )


def _print_npc_state(engine, label):
    world = engine.world
    print(f"  {label}：")
    for npc in world.npcs:
        pos = world.npc_position(npc.id)
        action = engine._npc_action_label(world, npc)
        print(
            f"    {npc.name:<14} 位置=({pos[0]:.1f}, {pos[1]:.1f}) "
            f"目标={_goal_text(npc)} 行动={action}"
        )


def _print_evidence(world, label):
    print(f"  {label}：")
    if not world.evidence:
        print("    无")
        return
    for record in world.evidence[-5:]:
        location = record.get("location") or []
        location_text = (
            f"({float(location[0]):.1f}, {float(location[1]):.1f})"
            if len(location) >= 2 else "-"
        )
        print(
            f"    {record.get('id') or record.get('type') or '未命名'} | type={record.get('type')} | "
            f"位置={location_text} | 来源={record.get('source_event') or '-'}"
        )


def _print_timeline(world, label):
    print(f"  {label}：")
    relevant = [
        event for event in world.story_timeline
        if event.get("event_id") in {"ledger_recovery", "quiet_manhunt"}
        or event.get("status") not in {"pending", "deferred", "blocked"}
    ]
    if not relevant:
        relevant = list(world.story_timeline[:4])
    if not relevant:
        print("    无")
        return
    for event in relevant[-8:]:
        print(
            f"    {event.get('event_id')} | status={event.get('status')} | "
            f"source={event.get('source') or '-'}"
        )


def _print_deviations(world, label):
    print(f"  {label}：")
    deviations = plot_deviation.detect_deviations(world)
    if not deviations:
        print("    无")
        return
    for deviation in deviations[:6]:
        print(
            f"    {deviation.kind} | event={deviation.event_id or '-'} | "
            f"npc={deviation.npc_id or '-'} | {deviation.evidence}"
        )


def _print_mysteries(world, label):
    print(f"  {label}：")
    if not world.story_seeds:
        print("    无")
        return
    for seed in world.story_seeds[-5:]:
        print(f"    {seed.get('type')} | {seed.get('content')}")


def _print_director_report(engine, provider):
    report = engine.world.night_director_report or {}
    applied = report.get("applied", {}) if isinstance(report, dict) else {}
    print("【World Director - Offline Deterministic Provider】")
    print(f"  分支判定：{provider.route}")
    print(f"  编排理由：{report.get('reasoning_summary') or '-'}")

    goals = [item for item in applied.get("goals", []) if item.get("applied")]
    events = [item for item in applied.get("story_events", []) if item.get("added")]
    mysteries = [item for item in applied.get("mysteries", []) if item.get("added")]

    if goals:
        for item in goals:
            print(
                f"  新增 Goal：{item.get('goal_string')} "
                f"({item.get('npc_id')})，状态={item.get('status')}"
            )
    else:
        print("  新增 Goal：无")

    if events:
        for item in events:
            print(f"  新增 StoryEvent：{item.get('event_id')}")
    else:
        print("  新增 StoryEvent：无")

    if mysteries:
        for item in mysteries:
            seed = item.get("seed") or {}
            print(f"  新增 Mystery：{seed.get('type')} | {seed.get('content')}")
    else:
        print("  新增 Mystery：无")


def _print_provider_context_deviations(provider):
    context = provider.context or {}
    deviations = (context.get("story") or {}).get("deviations") or []
    print("  夜间 Director 看到的 PlotDeviation：")
    if not deviations:
        print("    无")
        return
    for deviation in deviations[:6]:
        print(
            f"    {deviation.get('kind')} | event={deviation.get('event_id') or '-'} | "
            f"npc={deviation.get('npc_id') or '-'}"
        )


def _run_route(route):
    provider = OfflineShowcaseProvider()
    engine = _new_engine(provider)
    before = _counts(engine)
    player_actions = []

    if route == "A":
        world = engine.world
        world.player.x, world.player.y = 20.0, 20.0
        result = engine.inspect_object("mayor_bookshelf")
        player_actions.append(f"调查村长办公室书架：success={result.get('success')}")

        world.player.x, world.player.y = 70.0, 70.0
        world.get_npc("npc_2").position_override = [70.0, 70.0]
        result = engine.talk("npc_2", "你好，Ren。")
        player_actions.append(f"与 Ren 交谈：success={result.get('success')}")

        result = engine.protect_ren()
        player_actions.append(f"保护 Ren：success={result.get('success')}；{result.get('message')}")
    else:
        moved = 0
        for _ in range(6):
            result = engine.move("w")
            moved += int(bool(result.get("success")))
        player_actions.append(f"连续向西移动：成功 {moved}/6；玩家没有调查账本或帮助 Ren")

    after_player = _counts(engine)
    world = engine.world
    world.time = 22 * 3600
    world.phase = "night_director"
    world.night_director_day = None
    engine.tick()
    return engine, provider, before, after_player, player_actions


def _advance_until_triggered(engine, event_id):
    for _ in range(4):
        event = engine.world.story_timeline.get_event(event_id)
        if event and event.get("status") in {"triggered", "completed"}:
            return event
        engine.tick()
    return engine.world.story_timeline.get_event(event_id)


def _render_route(title, route):
    engine, provider, before, after_player, player_actions = _run_route(route)
    event_id = "ledger_recovery" if route == "A" else "quiet_manhunt"
    print("\n" + "-" * 58)
    print(title)
    print("-" * 58)
    print("【玩家行为】")
    for line in player_actions:
        print(f"  - {line}")

    print("【玩家行为后的 WorldState】")
    _print_counter("  状态摘要", after_player)
    _print_evidence(engine.world, "Evidence")
    _print_npc_state(engine, "NPC 当前状态")

    print("【World Director 离线编排】")
    _print_provider_context_deviations(provider)
    _print_director_report(engine, provider)

    event = _advance_until_triggered(engine, event_id)
    print("【新剧情继续推进】")
    if event is None:
        print(f"  {event_id}: 未找到")
    else:
        execution = (event.get("metadata") or {}).get("execution_status")
        print(
            f"  {event_id}: status={event.get('status')} | "
            f"execution={execution or '-'} | trigger_at={_format_time(event.get('trigger_at', 0))}"
        )

    print("【演化后的 WorldState】")
    after = _counts(engine)
    _print_counter("  最终状态", after)
    _print_npc_state(engine, "NPC 自主行动 / Goal 状态")
    _print_evidence(engine.world, "Evidence")
    _print_timeline(engine.world, "StoryTimeline")
    _print_deviations(engine.world, "PlotDeviation")
    _print_mysteries(engine.world, "Mystery / StorySeed")
    return {
        "route": route,
        "before": before,
        "after_player": after_player,
        "after": after,
        "event": event,
        "provider": provider,
        "engine": engine,
    }


def main():
    print(BANNER)
    print(DISCLAIMER)
    print("\n说明：以下所有世界变化都由 Submission 中现有代码真实执行；")
    print("只有 Night Director 的夜间编排结果使用本展示脚本中的确定性离线输出。")

    initial_engine = _new_engine(OfflineShowcaseProvider())
    initial = _counts(initial_engine)
    print("\n" + "-" * 58)
    print("【初始世界状态】")
    print("-" * 58)
    _print_counter("  状态摘要", initial)
    _print_npc_state(initial_engine, "NPC 与初始 Goal")
    _print_evidence(initial_engine.world, "Evidence")
    _print_timeline(initial_engine.world, "StoryTimeline")

    route_a = _render_route("DAY 1 - A：玩家介入，触发新的调查方向", "A")
    route_b = _render_route("DAY 1 - B：玩家忽略原剧情，世界自然偏航", "B")

    print("\n" + "=" * 58)
    print("FINAL COMPARISON - Before / After")
    print("=" * 58)
    _print_counter("初始世界", route_a["before"])
    _print_counter("介入路线最终状态", route_a["after"])
    _print_counter("忽略路线最终状态", route_b["after"])
    print("\n【两条路线的剧情结果】")
    print(
        f"  A：{route_a['provider'].route} | "
        f"Goal={route_a['engine'].world.get_npc('npc_4').goals} | "
        f"StoryEvent={route_a['event'].get('event_id')}={route_a['event'].get('status')}"
    )
    print(
        f"  B：{route_b['provider'].route} | "
        f"Goal={route_b['engine'].world.get_npc('npc_1').goals} | "
        f"StoryEvent={route_b['event'].get('event_id')}={route_b['event'].get('status')}"
    )
    print("\n同一个初始世界，在玩家行为与世界事件影响下，最终进入了不同的状态。")
    print("Offline Showcase complete.")


if __name__ == "__main__":
    import urllib.request

    # Fail fast if any accidental network path is reached during the showcase.
    with patch.object(
        urllib.request,
        "urlopen",
        side_effect=RuntimeError("Offline Showcase blocks network access."),
    ):
        main()


