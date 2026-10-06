"""Temporary adapters from resolved Intent to the legacy runtime.

These classes are migration bridges only. They are not part of the target V3
Action Layer and should be removed after Step 2/3 replace the legacy paths.
"""


class LegacyDialogueAdapter:
    """Migration bridge: resolved Dialogue Intent -> legacy runtime."""

    def execute_talk(self, engine, npc_id, message, resolved_intent):
        engine._record_player_memory("dialogue", message, {"npc_id": npc_id})
        success, action_message, parsed = engine.controller.talk(engine.world, npc_id, message)
        reply = None
        intent_type = resolved_intent.intent_type
        intent_alias = (
            "request_follow" if intent_type == "follow"
            else "stop_follow" if intent_type == "stop_follow"
            else intent_type
        )
        following = False

        if success:
            if intent_type == "follow":
                accepted = engine._npc_accepts_follow(npc_id)
                following = accepted
                engine._set_follow_state(npc_id, accepted)
                npc = engine.world.get_npc(npc_id)
                if npc is not None:
                    npc.add_memory({
                        "time": engine.world.time,
                        "type": "interaction",
                        "content": f"Player asked {npc.name} to follow",
                        "result": "accepted" if accepted else "declined",
                    })
                reply = "可以，我跟着你。" if accepted else "抱歉，我现在不能离开这里。"
                engine.dialogue_system.last_source = "rule"
            elif intent_type == "stop_follow":
                engine._set_follow_state(npc_id, False)
                npc = engine.world.get_npc(npc_id)
                if npc is not None:
                    npc.add_memory({
                        "time": engine.world.time,
                        "type": "interaction",
                        "content": "Player asked me to stop following",
                        "result": "stopped",
                    })
                reply = "好，我不跟着你了。"
                engine.dialogue_system.last_source = "rule"
            else:
                reply = engine.dialogue_system.generate_reply(engine.world, npc_id, message)

            engine.last_dialogue_reply = reply
            npc = engine.world.get_npc(npc_id)
            engine.dialogue_log.append({
                "time": engine.world.time,
                "player": message,
                "npc_id": npc_id,
                "npc": npc.name if npc is not None else npc_id,
                "reply": reply,
                "intent": intent_alias,
                "following": following,
            })

        engine._finish_action(success, action_message, parsed, f"talk {npc_id}")
        return {
            "success": success,
            "message": action_message,
            "reply": reply,
            "reply_source": getattr(engine.dialogue_system, "last_source", "rule"),
            "intent": intent_alias,
            "resolved_intent": resolved_intent.to_dict(),
            "following": following,
            "step": engine.current(),
        }


class LegacyThoughtAdapter:
    """Migration bridge: resolved Thought Intent -> legacy candidate generator."""

    def analyze(self, engine, text, resolved_intent):
        nearby_objects = engine.object_registry.visible_objects(engine.world)
        nearby_npcs = engine.controller.observe(engine.world)["npcs"]
        engine._update_pending_object_goals(text, nearby_objects)
        candidates = engine.open_intent.parse_candidates(
            text,
            engine.world,
            nearby_objects=nearby_objects,
            nearby_npcs=nearby_npcs,
        )
        candidates = engine._custom_candidates(
            text,
            candidates,
            nearby_objects=nearby_objects,
            nearby_npcs=nearby_npcs,
        )
        return candidates, nearby_objects, nearby_npcs
