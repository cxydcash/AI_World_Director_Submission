"""Step 7 tests: per-NPC continuous conversation memory."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from conversation_memory import (
    ConversationMemoryManager,
    FACT_LEDGER_MISSING_PAGES,
    MAX_MESSAGES,
    QUESTION_WHO_TOOK_PAGES,
)
from demo_flow import run_conversation_demo
from dialogue_system import DialogueSystem
from entities import NPC
from npc_planner import NPCPlanner
from open_world import OpenWorldEngine
from persistence import load_world, save_world


def runtime_engine():
    engine = OpenWorldEngine()
    engine.director.api_key = ""
    engine.director.provider = None
    # Scene setup: put Ren and Mira within talking range.
    engine.world.player.x, engine.world.player.y = 69.0, 70.0
    engine.world.get_npc("npc_2").position_override = [70.0, 70.0]
    engine.world.get_npc("npc_3").position_override = [68.5, 70.0]
    return engine


def ren_memory(engine):
    return engine.world.get_npc("npc_2").conversation_memory["player"]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ConversationMemoryTests(unittest.TestCase):
    def test_two_turns_read_previous_turn(self):
        engine = runtime_engine()
        first = engine.talk("npc_2", "你知道账本的事情吗？")
        second = engine.talk("npc_2", "谁拿走的？")

        self.assertEqual(first["reply"], "知道，最近少了几页。")
        self.assertIn("少了几页", second["reply"])
        messages = ren_memory(engine)["messages"]
        self.assertGreaterEqual(len(messages), 4)
        self.assertEqual(messages[0]["speaker"], "player")
        self.assertEqual(messages[1]["speaker"], "npc")

    def test_three_turns_read_earlier_information(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你知道账本的事情吗？")
        engine.talk("npc_2", "谁拿走的？")
        third = engine.talk("npc_2", "你刚才说少了几页，是哪几页？")

        self.assertIn("账本中间", third["reply"])
        self.assertIn("我之前已经告诉过你", third["reply"])
        self.assertEqual(ren_memory(engine)["known_facts"], [FACT_LEDGER_MISSING_PAGES])

    def test_different_npcs_do_not_share_memory(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你知道账本的事情吗？")
        engine.talk("npc_3", "你好，Mira。")

        ren = engine.world.get_npc("npc_2").conversation_memory
        mira = engine.world.get_npc("npc_3").conversation_memory
        ren_texts = [message["text"] for message in ren["player"]["messages"]]
        mira_texts = [message["text"] for message in mira["player"]["messages"]]
        self.assertIn("你知道账本的事情吗？", ren_texts)
        self.assertNotIn("你知道账本的事情吗？", mira_texts)
        self.assertNotEqual(ren, mira)

    def test_conversation_memory_is_saved(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你知道账本的事情吗？")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.json"
            save_world(engine.world, path)
            raw = path.read_text(encoding="utf-8")
        self.assertIn("conversation_memory", raw)
        self.assertIn("你知道账本的事情吗？", raw)

    def test_conversation_memory_is_loaded_and_still_used(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你知道账本的事情吗？")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.json"
            save_world(engine.world, path)
            loaded = load_world(path)

        loaded_ren = loaded.get_npc("npc_2")
        self.assertIn(FACT_LEDGER_MISSING_PAGES, loaded_ren.conversation_memory["player"]["known_facts"])

        resumed = runtime_engine()
        resumed.adopt_world(loaded)
        result = resumed.talk("npc_2", "我们刚才一直在聊什么？")
        self.assertIn("账本", result["reply"])

    def test_old_save_without_conversation_memory_still_loads(self):
        engine = runtime_engine()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.json"
            save_world(engine.world, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            for npc in data["npcs"]:
                npc.pop("conversation_memory", None)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            loaded = load_world(path)

        self.assertEqual(loaded.get_npc("npc_2").conversation_memory, {})
        resumed = runtime_engine()
        resumed.adopt_world(loaded)
        result = resumed.talk("npc_2", "你知道账本的事情吗？")
        self.assertTrue(result["success"])

    def test_player_claim_is_not_world_truth(self):
        engine = runtime_engine()
        ledger = engine.world.get_item("item_ledger")
        before = (
            ledger.holder,
            len(engine.world.evidence),
            len(engine.world.story_event_history),
            {npc_id: dict(state) for npc_id, state in engine.world.characters_state.items()},
        )

        result = engine.talk("npc_2", "村长偷了账本。")
        self.assertTrue(result["success"])

        claims = ren_memory(engine)["player_claims"]
        self.assertTrue(any("mayor" in claim for claim in claims))
        after = (
            ledger.holder,
            len(engine.world.evidence),
            len(engine.world.story_event_history),
            {npc_id: dict(state) for npc_id, state in engine.world.characters_state.items()},
        )
        self.assertEqual(before, after)

    def test_unresolved_question_is_recognized_next_turn(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你知道账本的事情吗？")
        engine.talk("npc_2", "谁拿走的？")
        self.assertIn(QUESTION_WHO_TOOK_PAGES, ren_memory(engine)["unresolved_questions"])

        again = engine.talk("npc_2", "你查到是谁了吗？")
        self.assertIn("还是", again["reply"])
        self.assertIn("没查到", again["reply"])

    def test_dialogue_pipeline_still_uses_intent_action_validator_executor(self):
        engine = runtime_engine()
        with patch.object(engine.intent_resolver, "resolve", wraps=engine.intent_resolver.resolve) as resolve, \
             patch.object(engine.action_validator, "validate", wraps=engine.action_validator.validate) as validate, \
             patch.object(engine.action_executor, "execute", wraps=engine.action_executor.execute) as execute:
            result = engine.talk("npc_2", "你知道账本的事情吗？")
        self.assertTrue(result["success"])
        self.assertTrue(resolve.called)
        self.assertTrue(validate.called)
        self.assertTrue(execute.called)
        self.assertEqual(engine.last_intent.source, "dialogue")

    def test_intent_type_is_recorded_on_the_turn(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你知道账本的事情吗？")
        player_messages = [
            message for message in ren_memory(engine)["messages"] if message["speaker"] == "player"
        ]
        self.assertEqual(player_messages[-1]["intent_type"], "conversation_only")

    def test_history_is_bounded(self):
        npc = NPC(id="npc_x", name="X", x=0.0, y=0.0)
        manager = ConversationMemoryManager()
        for index in range(MAX_MESSAGES * 3):
            manager.record_turn(npc, "player", f"消息 {index}", index)
        messages = npc.conversation_memory["player"]["messages"]
        self.assertEqual(len(messages), MAX_MESSAGES)
        self.assertEqual(messages[-1]["text"], f"消息 {MAX_MESSAGES * 3 - 1}")

    def test_llm_reply_receives_history(self):
        engine = runtime_engine()
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse({"choices": [{"message": {"content": "我记得，我们聊过账本。"}}]})

        system = DialogueSystem(api_key="test-key")
        system.memory.record_turn(engine.world.get_npc("npc_2"), "player", "我们刚才聊了什么？", 600)
        with patch("dialogue_system.urllib.request.urlopen", fake_urlopen):
            reply = system.generate_reply(engine.world, "npc_2", "你还记得吗？")

        self.assertEqual(reply, "我记得，我们聊过账本。")
        request_messages = captured["body"]["messages"]
        self.assertIn("conversation_history", request_messages[0]["content"])
        user_payload = json.loads(request_messages[1]["content"])
        self.assertIn("conversation_history", user_payload)
        self.assertTrue(
            any(message["text"] == "我们刚才聊了什么？" for message in user_payload["conversation_history"])
        )

    def test_npc_planner_and_story_timeline_still_work(self):
        engine = runtime_engine()
        engine.talk("npc_2", "你知道账本的事情吗？")

        mira = engine.world.get_npc("npc_3")
        mira.position_override = [68.0, 68.0]
        mira.goals = ["reach town"]
        action = NPCPlanner().plan_next_action(engine.world, "npc_3")
        self.assertEqual(action.action_type, "move")
        engine._update_npcs_with_planner()
        self.assertNotEqual(engine.world.npc_position("npc_3"), (68.0, 68.0))

        # Step 7.7: an active dialogue session defers events that touch npc_2,
        # so end the session before expecting the timeline to fire.
        engine.end_dialogue_session()
        engine.world.story_timeline.add_event({
            "event_id": "conversation_story_event",
            "trigger_at": 0,
            "effects": [{"type": "set_belief", "npc_id": "npc_2", "key": "story_ok", "value": 0.5}],
        })
        engine.world.advance_time(1)
        self.assertEqual(
            engine.world.story_timeline.get_event("conversation_story_event")["status"], "triggered"
        )

    def test_conversation_demo_is_deterministic_and_remembers(self):
        buffer_one = io.StringIO()
        with contextlib.redirect_stdout(buffer_one):
            first = run_conversation_demo()
        buffer_two = io.StringIO()
        with contextlib.redirect_stdout(buffer_two):
            second = run_conversation_demo()

        self.assertEqual(first, second)
        self.assertEqual(len(first["transcript"]), 4)
        self.assertEqual(first["transcript"][0]["npc"], "知道，最近少了几页。")
        self.assertIn("少了几页", first["transcript"][1]["npc"])
        self.assertIn("我之前已经告诉过你", first["transcript"][2]["npc"])
        self.assertIn("账本失踪", first["transcript"][3]["npc"])
        self.assertIn(FACT_LEDGER_MISSING_PAGES, first["ren_known_facts"])
        self.assertIn("账本", buffer_one.getvalue())


if __name__ == "__main__":
    unittest.main()
