"""Terminal demos for the Open World Dynamic Story."""

import sys
import tempfile
from pathlib import Path

from action_layer import Action
from director import FakeDirectorProvider
from open_world import OpenWorldEngine
from persistence import load_world, save_world
from story_engine import timeline_summary


def _format_day(seconds):
    return int(seconds // 86400) + 1


def _print_timeline(title, timeline):
    print(title)
    for entry in timeline:
        day = entry.get("day") or _format_day(entry["trigger_time"])
        print(f"  Day {day}  {entry['id']:<24} {entry['status']:<10} {entry['description']}")
    print()


def _advance(engine, days):
    engine.tick_seconds = 8 * 3600
    for _ in range(days * 3):
        engine.tick()


def run_no_intervention():
    print("\n================ FLOW A: NO INTERVENTION ================")
    engine = OpenWorldEngine()
    print("Reset: Day 1, time 0, weather Sunny")
    print("Original Timeline:")
    _print_timeline("", timeline_summary(engine.world))
    _advance(engine, 7)
    print("Final NPC state:")
    print("  Ren   alive =", engine.world.characters_state["npc_2"]["alive"])
    print("  Mayor fled =", engine.world.characters_state["npc_1"]["fled"])
    print("Current Timeline:")
    _print_timeline("", timeline_summary(engine.world))
    print("Daily AI Director Report (latest):")
    report = engine.snapshot()["open_world"]["daily_report"]
    if report:
        print("  day =", report["day"])
        print("  decision =", report["director"].get("decision"))
        print("  mayor beliefs =", report["mayor_beliefs"])
        print("  ren beliefs =", report["ren_beliefs"])
    print()


def run_intervention():
    print("\n================ FLOW B: PLAYER INTERVENTION ================")
    engine = OpenWorldEngine()
    # Move near Ren and protect him.
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    print("Player observes Ren acting strangely near the office.")
    print("Player Action: protect Ren")
    result = engine.protect_ren()
    print("  success =", result["success"], "|", result["message"])

    # Then take the ledger.
    engine.world.player.x, engine.world.player.y = 71.0, 72.0
    print("Player Action: take hidden ledger")
    result = engine.take_ledger()
    print("  success =", result["success"], "|", result["message"])

    print("\nOriginal Timeline:")
    original_ids = {"ren_lost": "pending", "player_saved_Ren": None, "village_investigation": None}
    for key, value in original_ids.items():
        if value:
            print(f"  {key:<24} {value}")
    print("  player_saved_Ren             (new branch, not yet created)")
    print("  village_investigation        (new branch, not yet created)")

    _advance(engine, 7)

    print("Final NPC state:")
    print("  Ren   alive =", engine.world.characters_state["npc_2"]["alive"])
    print("  Ren   rescued =", engine.world.characters_state["npc_2"]["rescued"])
    print("  Mayor ledger_missing belief =", engine.world.get_npc("npc_1").beliefs.get("ledger_missing"))

    print("Current Timeline:")
    timeline = timeline_summary(engine.world)
    for entry in timeline:
        if entry["id"] in {"ren_lost", "player_saved_Ren", "village_investigation"}:
            print(f"  {entry['id']:<24} {entry['status']:<10} {entry['description']}")

    print("\nDaily AI Director Report (latest):")
    report = engine.snapshot()["open_world"]["daily_report"]
    if report:
        print("  day =", report["day"])
        print("  decision =", report["director"].get("decision"))
        print("  ren beliefs =", report["ren_beliefs"])
        print("  mayor beliefs =", report["mayor_beliefs"])
    print()


def _print_runtime_state(engine, label):
    snapshot = engine.snapshot()
    print(f"  -- {label}: world time = {engine.world.time:g} (Day {snapshot['open_world']['day']})")
    for npc in snapshot["npcs"]:
        print(
            f"     {npc['name']:<14} pos={npc['position']} goals={npc['goals']} action={npc['current_action']}"
        )


def run_open_world_runtime_demo():
    """Deterministic Step 6 demo: full player/NPC/story/director causal chain."""
    print("=" * 66)
    print("OPEN WORLD RUNTIME DEMO - deterministic, offline (FakeDirectorProvider)")
    print("=" * 66)

    # ---- Stage 1: world initialization ----
    print("\n[Stage 1] World initialization")
    engine = OpenWorldEngine()
    engine.tick_seconds = 3600.0
    engine.director.api_key = ""
    engine.director.provider = None
    snapshot = engine.snapshot()
    print(f"  world time = {engine.world.time:g} (Day {snapshot['open_world']['day']})")
    print(f"  player     = {snapshot['player']['position']}")
    print("  npcs       = " + ", ".join(npc["name"] for npc in snapshot["npcs"]))
    print("  items      = " + ", ".join(item["id"] for item in snapshot["items"]))
    print("  timeline   = " + ", ".join(entry["id"] for entry in snapshot["timeline"][:5]) + ", ...")

    # ---- Stage 2: player actions ----
    print("\n[Stage 2] Player actions (Intent -> Action -> Validator -> Executor)")
    result = engine.move("d")
    print(f"  move d        success={result['success']} player={engine.snapshot()['player']['position']}")
    # Scene setup (not a player action): put the player next to Ren for dialogue.
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    result = engine.talk("npc_2", "我在调查账本的事，你知道什么吗？")
    print(f"  talk npc_2    success={result['success']} reply={result.get('reply')}")
    engine.analyze_thought("我想检查附近的桌子")
    intent = engine.last_intent.intent_type if engine.last_intent else "unknown"
    print(f"  thought       resolved intent={intent} candidates={len(engine.last_candidates)}")
    result = engine.execute_via_action_layer(
        Action("create_rumor", target_id="npc_2", parameters={"content": "账本可能藏在森林里。"})
    )
    print(f"  create_rumor  success={result['success']} ren beliefs={engine.world.get_npc('npc_2').beliefs}")

    # ---- Stage 3: NPC autonomous action ----
    print("\n[Stage 3] NPC autonomous action (Goal -> Planner -> Action -> Executor)")
    mira = engine.world.get_npc("npc_3")
    mira.position_override = [68.0, 68.0]
    mira.goals = ["reach town"]
    for step in range(2):
        engine.tick()
        print(f"  tick {step + 1}: Mira pos={engine.world.npc_position('npc_3')} action={engine._npc_action_label(engine.world, mira)}")

    # ---- Stage 4: AI Director creates a future StoryEvent ----
    print("\n[Stage 4] AI Director proposes a future StoryEvent (FakeDirectorProvider)")
    ai_event_id = "ai_mira_followup"
    proposal = {
        "event_id": ai_event_id,
        "title": "Mira follows the rumor",
        "description": "When Mira has reached town, the rumor pushes her to search the forest.",
        "trigger_at": engine.world.time + engine.tick_seconds,
        "conditions": [{"type": "npc_goal_exists", "npc_id": "npc_3", "goal": "reach town"}],
        "cancel_conditions": [],
        "effects": [
            {"type": "add_goal", "npc_id": "npc_3", "goal": "reach forest"},
            {"type": "set_belief", "npc_id": "npc_3", "key": "heard_rumor", "value": 0.8},
        ],
        "priority": 1.0,
        "metadata": {"generated_by": "ai_director"},
    }
    engine.director.provider = FakeDirectorProvider(proposal)
    proposal_result = engine.run_director_cycle()
    print(f"  added={proposal_result['added']} event_id={ai_event_id}")
    print(f"  event status={engine.world.story_timeline.get_event(ai_event_id)['status']}")

    # ---- Stage 5: save ----
    print("\n[Stage 5] Save WorldState")
    save_directory = tempfile.mkdtemp(prefix="ai_world_runtime_")
    state_path = Path(save_directory) / "runtime_state.json"
    save_world(engine.world, state_path)
    print(f"  saved to {state_path}")

    # ---- Stage 6: reload ----
    print("\n[Stage 6] Reload WorldState")
    loaded_world = load_world(state_path)
    engine = OpenWorldEngine()
    engine.tick_seconds = 3600.0
    engine.director.api_key = ""
    engine.director.provider = FakeDirectorProvider(proposal)
    engine.adopt_world(loaded_world)
    reloaded = engine.world.story_timeline.get_event(ai_event_id)
    print(f"  reloaded AI event: {ai_event_id} status={reloaded['status']} trigger_at={reloaded['trigger_at']:g}")

    # ---- Stage 7: future event triggers ----
    # Step 12.2: 对话期间世界时间会放慢。要让时间正常推进，先结束对话。
    engine.end_dialogue_session()
    print("\n[Stage 7] Advance time until the AI event triggers")
    engine.tick()
    triggered = engine.world.story_timeline.get_event(ai_event_id)
    print(f"  event status={triggered['status']} execution={triggered['metadata'].get('execution_status')}")
    print(f"  Mira goals={engine.world.get_npc('npc_3').goals}")

    # ---- Stage 8: NPC reacts through the Planner ----
    print("\n[Stage 8] NPC reacts in later ticks (Planner -> Action -> Validator -> Executor)")
    for step in range(2):
        engine.tick()
        print(f"  tick {step + 1}: Mira pos={engine.world.npc_position('npc_3')} action={engine._npc_action_label(engine.world, engine.world.get_npc('npc_3'))}")

    # ---- Stage 9: final state ----
    print("\n[Stage 9] Final state")
    _print_runtime_state(engine, "final")
    print("  StoryTimeline (AI + recent):")
    for entry in timeline_summary(engine.world):
        if entry["id"] == ai_event_id or entry["status"] != "pending":
            print(f"     {entry['id']:<24} {entry['status']}")
    print("  Recent EventHistory:")
    for entry in engine.world.event_history[-3:]:
        print(f"     {entry.get('event_id')} effects={len(entry.get('effects', []))}")
    print("  AI generated StoryEvent:")
    final_event = engine.world.story_timeline.get_event(ai_event_id)
    print(f"     id={final_event['event_id']} status={final_event['status']} source={final_event['source']}")
    print("\nDemo complete.")
    return {
        "time": engine.world.time,
        "ai_event_id": ai_event_id,
        "ai_event_status": final_event["status"],
        "mira_goals": list(engine.world.get_npc("npc_3").goals),
        "mira_position": list(engine.world.npc_position("npc_3")),
        "event_history_ids": [entry.get("event_id") for entry in engine.world.event_history],
    }


def run_conversation_demo():
    """Deterministic 4-turn conversation demo (offline, no API key)."""
    print("=" * 60)
    print("NPC CONTINUOUS CONVERSATION DEMO - offline, deterministic")
    print("=" * 60)
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    # Scene setup (not a player action): bring Ren and Mira within talking range.
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    engine.world.get_npc("npc_3").position_override = [68.5, 70.0]
    ren = engine.world.get_npc("npc_2")

    turns = [
        "你知道账本的事情吗？",
        "谁拿走的？",
        "你刚才说少了几页，是哪几页？",
        "我们刚才一直在聊什么？",
    ]
    transcript = []
    for index, message in enumerate(turns, start=1):
        result = engine.talk("npc_2", message)
        reply = result.get("reply")
        print(f"[Turn {index}] Player: {message}")
        print(f"          Ren: {reply}")
        transcript.append({"player": message, "npc": reply})

    mira_result = engine.talk("npc_3", "你好，Mira。")
    print(f"[Mira]    Player: 你好，Mira。")
    print(f"          Mira: {mira_result.get('reply')}")

    print("\nRen memory facts:", ren.conversation_memory["player"]["known_facts"])
    print("Ren unresolved  :", ren.conversation_memory["player"]["unresolved_questions"])
    print("Demo complete.")
    return {
        "transcript": transcript,
        "ren_known_facts": list(ren.conversation_memory["player"]["known_facts"]),
        "ren_unresolved": list(ren.conversation_memory["player"]["unresolved_questions"]),
        "ren_topics": list(ren.conversation_memory["player"]["recent_topics"]),
        "ren_messages": len(ren.conversation_memory["player"]["messages"]),
    }


def main():
    run_open_world_runtime_demo()


def main_comparison():
    run_no_intervention()
    run_intervention()


if __name__ == "__main__":
    if "--conversation" in sys.argv:
        run_conversation_demo()
    elif "--compare" in sys.argv:
        main_comparison()
    else:
        main()
