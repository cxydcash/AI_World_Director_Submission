"""Step 7.6 tests: conversation context + fact-grounded follow-ups."""

import tempfile
import unittest
from pathlib import Path

from dialogue_system import DialogueSystem
from entities import NPC, Player
from persistence import load_world, save_world
from world import WorldState


def make_world():
    return WorldState(
        time=1000.0,
        player=Player(20.0, 20.0),
        npcs=[
            NPC(id="npc_2", name="Ren", x=20.0, y=20.0, goals=["find ledger"]),
            NPC(id="npc_3", name="Mira", x=20.0, y=20.0, goals=["help Ren"]),
        ],
        characters_state={
            "npc_2": {"alive": True, "location": "village", "rescued": False},
            "npc_3": {"alive": True, "location": "village", "rescued": False},
        },
    )


def seed(ds, world, npc_id, lines):
    npc = world.get_npc(npc_id)
    for line in lines:
        ds._remember_reply(npc, line, world.time)
    return npc


class ConversationContextTests(unittest.TestCase):
    def setUp(self):
        self.world = make_world()
        self.ds = DialogueSystem(api_key="")  # deterministic fallback

    def test_which_entries_follows_up_on_ledger(self):
        seed(self.ds, self.world, "npc_2", ["我最近在查村里的账本。"])
        reply = self.ds.generate_reply(self.world, "npc_2", "哪几笔？")
        self.assertIn("账本", reply)
        self.assertEqual(self.ds.last_source, "rule")

    def test_what_number_follows_up_on_the_previous_line(self):
        seed(self.ds, self.world, "npc_2", ["有几笔支出数字对不上。"])
        reply = self.ds.generate_reply(self.world, "npc_2", "什么数字不对？")
        self.assertIn("支出", reply)
        self.assertIn("数字", reply)

    def test_who_question_does_not_invent_a_person(self):
        seed(self.ds, self.world, "npc_2", ["我发现有几笔被人改过。"])
        reply = self.ds.generate_reply(self.world, "npc_2", "谁改的？")
        self.assertTrue("不知道" in reply or "还不知" in reply)
        self.assertIn("改过", reply)
        for fabricated in ("张三", "李四", "王五", "村长改的", "Ivy", "Mira"):
            self.assertNotIn(fabricated, reply)

    def test_when_question_reuses_the_mentioned_time(self):
        seed(self.ds, self.world, "npc_2", ["我昨天发现账目有问题。"])
        reply = self.ds.generate_reply(self.world, "npc_2", "什么时候发现的？")
        self.assertIn("昨天", reply)

    def test_then_question_continues_the_current_topic(self):
        seed(self.ds, self.world, "npc_2", ["我去村长家查账。"])
        reply = self.ds.generate_reply(self.world, "npc_2", "然后呢？")
        self.assertTrue("村长家" in reply or "查账" in reply)
        self.assertNotIn("我是村里的", reply)

    def test_recap_question_summarizes_the_conversation(self):
        seed(self.ds, self.world, "npc_2", ["我在查账本。"])
        reply = self.ds.generate_reply(self.world, "npc_2", "我们刚才聊什么？")
        self.assertTrue("账本" in reply or "查账" in reply)

    def test_confirmation_question_is_answered_yes_no(self):
        seed(self.ds, self.world, "npc_2", ["我在查账本。"])
        reply = self.ds.generate_reply(self.world, "npc_2", "你刚才说的是账本吗？")
        self.assertIn("是的", reply)
        self.assertIn("账本", reply)

    def test_different_npcs_do_not_share_conversation_context(self):
        seed(self.ds, self.world, "npc_2", ["我在查账本。"])
        reply = self.ds.generate_reply(self.world, "npc_3", "我们刚才聊什么？")
        self.assertNotIn("账本", reply)
        ren = self.world.get_npc("npc_2").conversation_memory
        mira = self.world.get_npc("npc_3").conversation_memory
        ren_texts = [message["text"] for message in ren["player"]["messages"]]
        mira_texts = [message["text"] for message in mira["player"]["messages"]]
        self.assertIn("我在查账本。", ren_texts)
        self.assertNotIn("我在查账本。", mira_texts)

    def test_history_survives_save_and_load(self):
        seed(self.ds, self.world, "npc_2", ["我在查账本。"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation_context.json"
            save_world(self.world, path)
            loaded = load_world(path)

        loaded_ren = loaded.get_npc("npc_2")
        texts = [message["text"] for message in loaded_ren.conversation_memory["player"]["messages"]]
        self.assertIn("我在查账本。", texts)
        resumed = DialogueSystem(api_key="")
        reply = resumed.generate_reply(loaded, "npc_2", "我们刚才聊什么？")
        self.assertTrue("账本" in reply or "查账" in reply)

    def test_fallback_uses_raw_messages_for_consecutive_followups(self):
        seed(self.ds, self.world, "npc_2", [
            "这些账本最近看着有点不对劲。",
            "就是村账本上那几笔支出，数字对不上。有几笔被人改过……",
        ])
        reply = self.ds.generate_reply(self.world, "npc_2", "什么数字不对？")
        self.assertIn("支出", reply)
        self.assertIn("数字", reply)
        self.assertEqual(self.ds.last_source, "rule")

    def test_llm_context_view_is_clean_and_not_duplicated(self):
        history = [
            {"speaker": "player", "text": "你在找什么？", "world_time": 1.0, "intent_type": "conversation_only", "topic": None},
            {"speaker": "npc", "text": "我在查账本。", "world_time": 1.0, "intent_type": None, "topic": "ledger"},
        ]
        from dialogue_system import _dialogue_view, _world_memory
        view = _dialogue_view(history)
        self.assertEqual(view, [
            {"speaker": "player", "text": "你在找什么？"},
            {"speaker": "npc", "text": "我在查账本。"},
        ])
        npc = self.world.get_npc("npc_2")
        npc.memory = [
            {"time": 1.0, "source": "dialogue", "content": "你在找什么？"},
            {"time": 1.0, "source": "observation", "content": "The forest path is muddy."},
        ]
        world_memory = _world_memory(npc, ["你在找什么？", "我在查账本。"])
        self.assertEqual(world_memory, ["The forest path is muddy."])

    def test_dialogue_context_diagnostic(self):
        seed(self.ds, self.world, "npc_2", ["有几笔支出数字对不上。"])
        message = "什么数字不对？"
        self.ds.generate_reply(self.world, "npc_2", message)
        print(
            f"[Dialogue Context] history_count = {self.ds.last_history_count} "
            f"| current_message = {message} | reply_source = {self.ds.last_source}"
        )
        self.assertGreaterEqual(self.ds.last_history_count, 2)
        self.assertEqual(self.ds.last_source, "rule")


if __name__ == "__main__":
    unittest.main()
