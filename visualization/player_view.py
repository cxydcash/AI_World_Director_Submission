"""玩家视角的只读投影 + 观察记录器。

设计原则：
* 不改世界状态、不改模拟规则，只读取 OpenWorldEngine 的只读快照。
* 所有文本来自真实 WorldState；系统没有记录原因时显示"原因未知"。
* 只补充"世界本身没有保存、但展示确实需要"的历史：NPC 移动轨迹与
  行动动态（通过对比真实快照得到，不生成任何虚构内容）。
"""

import threading

try:  # 作为包导入（visualization.player_view）
    from .zh_labels import (
        PLAYER_ACTION_TEXTS,
        action_name,
        belief_text,
        effect_text,
        evidence_name,
        goal_text,
        history_text,
        item_name,
        location_name,
        memory_text,
        reason_text,
        role_name,
        weather_label,
    )
except ImportError:  # 作为脚本模块导入（visualization/app.py）
    from zh_labels import (
        PLAYER_ACTION_TEXTS,
        action_name,
        belief_text,
        effect_text,
        evidence_name,
        goal_text,
        history_text,
        item_name,
        location_name,
        memory_text,
        reason_text,
        role_name,
        weather_label,
    )

try:  # 真实坐标表（只读）
    from .world import EXPLORE_LOCATIONS as _DEFAULT_LOCATIONS
except ImportError:
    try:
        from world import EXPLORE_LOCATIONS as _DEFAULT_LOCATIONS
    except ImportError:
        _DEFAULT_LOCATIONS = {}


try:
    from .narrative import collect_day_facts
except ImportError:
    from narrative import collect_day_facts

try:
    from .observation import (
        CATEGORY_LABELS,
        CAT_EVIDENCE,
        CAT_NPC_ACTION,
        CAT_NPC_DIALOGUE,
        CAT_PLAYER_ACTION,
        CAT_PLAYER_DWELL,
        CAT_PLAYER_INPUT,
        CAT_SYSTEM,
        CAT_WORLD,
        DAY,
        build_clue_entities,
        clue_key,
        classify_history_entry,
        importance_scores,
        place_of,
        suspect_actor_defaults,
        tier_of,
        time_label,
    )
except ImportError:
    from observation import (
        CATEGORY_LABELS,
        CAT_EVIDENCE,
        CAT_NPC_ACTION,
        CAT_NPC_DIALOGUE,
        CAT_PLAYER_ACTION,
        CAT_PLAYER_DWELL,
        CAT_PLAYER_INPUT,
        CAT_SYSTEM,
        CAT_WORLD,
        DAY,
        build_clue_entities,
        classify_history_entry,
        clue_key,
        importance_scores,
        place_of,
        suspect_actor_defaults,
        tier_of,
        time_label,
    )

DAY = DAY


# 位置变化小于该距离视为"没有移动"（与模拟步长一致）
MOVE_EPSILON = 0.01

# 只有停留超过这个 tick 数才记入"停留轨迹"
DWELL_TICKS = 5
# 判定"还在同一个地方"的半径
DWELL_RADIUS = 3.0

# 玩家能听清对话的距离
EARSHOT = 8.0


class PlayerObserver:
    """把逐帧只读快照整理成"NPC 动态"与"NPC 轨迹"。"""

    def __init__(self, locations=None, max_activity=300, max_track=80):
        self._lock = threading.RLock()
        self.locations = dict(locations or _DEFAULT_LOCATIONS)
        self.max_activity = max_activity
        self.max_track = max_track
        self.reset()

    def reset(self):
        with self._lock:
            self.activity = []
            self.tracks = {}
            self._seen = set()
            self._last = None
            # 停留轨迹（只记录停留超过 DWELL_TICKS 的位置）
            self.samples = 0
            self.dwell = {}
            self.stays = {}
            self.places_visited = {}
            self._last_place = {}
            # 动作 / 对话 / 线索
            self.actions = []
            self.talks = []
            self.player_inputs = []
            self.heard = []
            self._heard_seen = set()
            self.clues = {}
            self._history_seen = set()
            self._memory_seen = set()
            self._dialogue_seen = set()

    # -- 记录 ---------------------------------------------------------
    def observe(self, snapshot):
        if not isinstance(snapshot, dict) or "npcs" not in snapshot:
            return
        with self._lock:
            self.samples += 1
            previous = self._last
            current = self._digest(snapshot)
            self._track_dwell(snapshot)
            self._track_player_memory(snapshot)
            self._track_history(snapshot)
            self._track_clues(snapshot)
            self._track_heard(snapshot)
            if previous is not None and current["time"] > previous["time"]:
                self._record(snapshot, previous, current)
            self._last = current

    # -- 停留轨迹（> DWELL_TICKS 才记录）-------------------------------
    def _subjects(self, snapshot):
        out = [("player", "玩家", (snapshot.get("player") or {}).get("position"))]
        for npc in snapshot.get("npcs") or []:
            out.append((npc["id"], npc.get("name") or npc["id"], npc.get("position")))
        return out

    def _track_dwell(self, snapshot):
        now = float(snapshot.get("time") or 0.0)
        for subject, name, position in self._subjects(snapshot):
            if not position:
                continue
            place = place_of(position, self.locations)
            last_place = self._last_place.get(subject)
            if (last_place is not None and place != last_place
                    and place not in {"空地", "远离村庄的空地"}):
                self.places_visited.setdefault(subject, []).append({
                    "time": now, "time_label": time_label(now), "place": place, "name": name,
                    "kind": "move", "text": f"{name}到了{place}。",
                })
            self._last_place[subject] = place

            state = self.dwell.get(subject)
            if state is None:
                self.dwell[subject] = {"anchor": tuple(position), "since": now, "ticks": 1, "place": place}
                continue
            dx = float(position[0]) - float(state["anchor"][0])
            dy = float(position[1]) - float(state["anchor"][1])
            if (dx * dx + dy * dy) ** 0.5 <= DWELL_RADIUS:
                state["ticks"] += 1
                state["place"] = place
            else:
                self._flush_stay(subject, name, state, now)
                self.dwell[subject] = {"anchor": tuple(position), "since": now, "ticks": 1, "place": place}

    def _flush_stay(self, subject, name, state, end_time):
        ticks = state.get("ticks", 0)
        if ticks < DWELL_TICKS:
            return
        place = state.get("place") or place_of(state["anchor"], self.locations)
        self.stays.setdefault(subject, []).append({
            "start": state["since"], "end": end_time, "ticks": ticks,
            "time_label": time_label(state["since"]),
            "place": place, "position": list(state["anchor"]),
            "text": f"{name}在{place}停留了约 {ticks} 个时间刻。",
        })

    def current_stay(self, subject):
        state = self.dwell.get(subject)
        if not state or state.get("ticks", 0) < DWELL_TICKS:
            return None
        return {
            "time_label": time_label(state["since"]),
            "place": state.get("place") or place_of(state["anchor"], self.locations),
            "ticks": state["ticks"],
        }

    # -- 玩家 / NPC 动作 ----------------------------------------------
    def _track_history(self, snapshot):
        names = {npc["id"]: (npc.get("name") or npc["id"]) for npc in snapshot.get("npcs") or []}
        names["player"] = "玩家"
        places = {key: location_name(key) for key in self.locations}
        for entry in snapshot.get("history") or []:
            key = (entry.get("time"), entry.get("action"), entry.get("target"), str(entry.get("content"))[:60])
            if key in self._history_seen:
                continue
            self._history_seen.add(key)
            action = str(entry.get("action") or "")
            if not action:
                continue
            actor = str(entry.get("actor") or "")
            # 分类只依据真实来源；world.add_history 的 actor 默认值是 "player"，
            # 所以绝不因为 actor == "player" 就把它当成玩家行为。
            category = classify_history_entry(entry)
            if category == CAT_PLAYER_ACTION:
                category = CAT_WORLD
            subject = actor if (actor.startswith("npc") or action.startswith("npc_")) else "world"
            text = history_text(action, entry.get("target"), entry.get("content"), names, places)
            if not text:
                continue
            self.actions.append({
                "time": entry.get("time", 0.0), "time_label": time_label(entry.get("time", 0.0)),
                "subject": subject, "kind": "action", "action": action, "text": text,
                "category": category,
            })
            self.actions = self.actions[-600:]

    def _track_player_memory(self, snapshot):
        memory = (snapshot.get("player") or {}).get("memory") or []
        for entry in memory:
            key = (entry.get("time"), entry.get("type"), str(entry.get("content"))[:60])
            if key in self._memory_seen:
                continue
            self._memory_seen.add(key)
            kind = str(entry.get("type") or "")
            content = str(entry.get("content") or "").strip()
            if not content or kind != "thought":
                # 只有"想法"是未执行的玩家输入；对话/自由行动的真实记录
                # 由 Action 管线日志负责，不在这里重复登记。
                continue
            self.player_inputs.append({
                "time": entry.get("time", 0.0), "time_label": time_label(entry.get("time", 0.0)),
                "subject": "player", "kind": CAT_PLAYER_INPUT,
                "text": content, "category": CAT_PLAYER_INPUT,
            })

    def _track_clues(self, snapshot):
        now = float(snapshot.get("time") or 0.0)
        items = {str(item.get("id")): item for item in snapshot.get("items") or []}
        for entry in snapshot.get("evidence") or []:
            key = self._evidence_key(entry)
            if key not in self.clues:
                placed_at = place_of(entry.get("location") or [0, 0], self.locations)
                recent = [a for a in self.actions
                          if a.get("subject") == "player" and abs((a.get("time") or 0.0) - now) < 1.0]
                if entry.get("owner") == "player":
                    origin = recent[-1]["text"] if recent else "玩家留下了一条线索"
                else:
                    origin = "由世界事件产生"
                self.clues[key] = {
                    "key": key, "type": entry.get("type"), "events": [{
                        "time": entry.get("created_at", now),
                        "time_label": time_label(entry.get("created_at", now)),
                        "text": f"{origin}（出现在{placed_at}）",
                        "stage": "产生",
                    }], "discoverers": [],
                    "created_at": entry.get("created_at", now),
                    "created_label": time_label(entry.get("created_at", now)),
                    "place": placed_at,
                    "owner": entry.get("owner"),
                    "description": entry.get("description") or "",
                    "origin": origin,
                }
            chain = self.clues[key]
            # 发现事件
            discoverers = set(entry.get("discovered_by") or [])
            if entry.get("investigated_by"):
                discoverers.add(entry["investigated_by"])
            for npc_id in entry.get("auto_investigated_by") or []:
                discoverers.add(npc_id)
            for npc_id in sorted(discoverers):
                if npc_id in chain["discoverers"]:
                    continue
                chain["discoverers"].append(npc_id)
                chain["events"].append({
                    "time": now, "time_label": time_label(now),
                    "stage": "发现", "npc_id": npc_id,
                })
            # 当前持有者（同 id 的物品）
            item = items.get(str(entry.get("id")))
            holder = item.get("holder") if item else None
            if holder and chain.get("holder") != holder:
                chain["holder"] = holder
                chain["events"].append({
                    "time": now, "time_label": time_label(now),
                    "stage": "转移", "npc_id": holder,
                })

    # -- 我听到的（对我说 / 我路过听到）--------------------------------
    def _track_heard(self, snapshot):
        player_position = (snapshot.get("player") or {}).get("position")
        npcs = {npc["id"]: npc for npc in snapshot.get("npcs") or []}
        if player_position is None:
            return

        def distance_to(npc_id):
            npc = npcs.get(npc_id)
            if not npc or not npc.get("position"):
                return None
            dx = float(player_position[0]) - float(npc["position"][0])
            dy = float(player_position[1]) - float(npc["position"][1])
            return (dx * dx + dy * dy) ** 0.5

        # 事件驱动的对话
        for entry in snapshot.get("dialogues") or []:
            speaker = entry.get("speaker_id")
            key = ("dlg", entry.get("time"), speaker, str(entry.get("content"))[:40])
            if key in self._heard_seen:
                continue
            self._heard_seen.add(key)
            if not speaker or speaker == "player":
                continue
            listener = entry.get("listener_id") or entry.get("target_id")
            distance = distance_to(speaker)
            if listener == "player":
                self.heard.append({
                    "time": entry.get("time", 0.0),
                    "time_label": time_label(entry.get("time", 0.0)),
                    "kind": "to_player", "speaker": speaker, "listener": "player",
                    "text": str(entry.get("content") or ""),
                })
            elif distance is not None and distance <= EARSHOT:
                self.heard.append({
                    "time": entry.get("time", 0.0),
                    "time_label": time_label(entry.get("time", 0.0)),
                    "kind": "overheard", "speaker": speaker,
                    "listener": listener or "", "text": str(entry.get("content") or ""),
                })

        # 玩家与 NPC 的真实对话（别人对我说 / 我说的）
        for entry in (snapshot.get("open_world") or {}).get("dialogue_log") or []:
            npc_id = entry.get("npc_id")
            npc_name = entry.get("npc") or npc_id
            player_line = str(entry.get("player") or "").strip()
            reply = str(entry.get("reply") or "").strip()
            key = ("plog", entry.get("time"), npc_id, player_line[:30], reply[:30])
            if key in self._heard_seen:
                continue
            self._heard_seen.add(key)
            if player_line:
                self.heard.append({
                    "time": entry.get("time", 0.0),
                    "time_label": time_label(entry.get("time", 0.0)),
                    "kind": "player_said", "speaker": "player",
                    "listener": npc_id or "", "text": player_line,
                })
            if reply:
                self.heard.append({
                    "time": entry.get("time", 0.0),
                    "time_label": time_label(entry.get("time", 0.0)),
                    "kind": "to_player", "speaker": npc_id or npc_name,
                    "listener": "player", "text": reply,
                })

        # NPC 之间的行动式对话（world.history: npc_talked）
        for entry in snapshot.get("history") or []:
            if str(entry.get("action")) != "npc_talked":
                continue
            speaker = str(entry.get("actor") or "")
            key = ("talk", entry.get("time"), speaker, str(entry.get("target")))
            if key in self._heard_seen:
                continue
            self._heard_seen.add(key)
            distance = distance_to(speaker)
            if distance is not None and distance <= EARSHOT:
                self.heard.append({
                    "time": entry.get("time", 0.0),
                    "time_label": time_label(entry.get("time", 0.0)),
                    "kind": "overheard", "speaker": speaker,
                    "listener": str(entry.get("target") or ""),
                    "text": str(entry.get("content") or ""),
                })
        self.heard = self.heard[-120:]

    def heard_log(self, limit=30):
        with self._lock:
            return [dict(item) for item in self.heard][-limit:]

    # -- 读取 ---------------------------------------------------------
    def stays_of(self, subject, limit=30):
        with self._lock:
            return [dict(item) for item in (self.stays.get(subject) or [])[-limit:]]

    def places_of(self, subject, limit=40):
        with self._lock:
            return [dict(item) for item in (self.places_visited.get(subject) or [])[-limit:]]

    def actions_of(self, subject, limit=80):
        with self._lock:
            items = self.actions + self.talks
            return [dict(item) for item in items if item.get("subject") == subject][-limit:]

    def clue_entries(self):
        with self._lock:
            return {key: dict(value) for key, value in self.clues.items()}

    def _digest(self, snapshot):
        npcs = {}
        for npc in snapshot.get("npcs", []):
            npcs[npc["id"]] = {
                "name": npc.get("name") or npc["id"],
                "position": tuple(npc.get("position") or (0.0, 0.0)),
                "goals": list(npc.get("goals") or []),
                "goal_states": {
                    key: (value or {}).get("status")
                    for key, value in (npc.get("goal_states") or {}).items()
                },
                "beliefs": dict(npc.get("beliefs") or {}),
                "memory_len": len(npc.get("memory") or []),
                "relationships": dict(npc.get("relationships") or {}),
                "last_memory": (npc.get("memory") or [{}])[-1] if npc.get("memory") else {},
                "following": bool(npc.get("following_player")),
            }
        evidence = {}
        for item in snapshot.get("evidence", []):
            key = self._evidence_key(item)
            evidence[key] = {
                "type": item.get("type"),
                "investigated_by": item.get("investigated_by"),
                "discovered_by": list(item.get("discovered_by") or []),
                "auto_by": list(item.get("auto_investigated_by") or []),
            }
        dialogues = snapshot.get("dialogues") or []
        open_world = snapshot.get("open_world") or {}
        return {
            "time": float(snapshot.get("time") or 0.0),
            "npcs": npcs,
            "evidence": evidence,
            "dialogues": len(dialogues),
            "dialogue_log": len(open_world.get("dialogue_log") or []),
        }

    @staticmethod
    def _evidence_key(entry):
        # 与观察层共用同一个身份函数，保证"同一个线索"在所有层里是同一个实体。
        return clue_key(entry)

    def _add(self, entry):
        key = entry.pop("_key", None)
        if key is not None:
            if key in self._seen:
                return
            self._seen.add(key)
        self.activity.append(entry)
        if len(self.activity) > self.max_activity:
            self.activity = self.activity[-self.max_activity:]
            self._seen = {e.get("_key") for e in self.activity if e.get("_key")}

    def _record(self, snapshot, previous, current):
        locations = self.locations
        lookup = {npc_id: data["name"] for npc_id, data in current["npcs"].items()}

        def name_of(npc_id):
            return lookup.get(npc_id) or "某个人"

        for npc_id, data in current["npcs"].items():
            old = previous["npcs"].get(npc_id)
            if old is None:
                continue
            # 轨迹 + 到达
            moved = (
                abs(data["position"][0] - old["position"][0]) > MOVE_EPSILON
                or abs(data["position"][1] - old["position"][1]) > MOVE_EPSILON
            )
            if moved:
                self.tracks.setdefault(npc_id, []).append({
                    "time": current["time"],
                    "time_label": time_label(current["time"]),
                    "position": list(data["position"]),
                    "place": place_of(data["position"], locations),
                })
                self.tracks[npc_id] = self.tracks[npc_id][-self.max_track:]
                old_place = place_of(old["position"], locations)
                new_place = place_of(data["position"], locations)
                if old_place != new_place:
                    self._add({
                        "time": current["time"], "time_label": time_label(current["time"]),
                        "npc": name_of(npc_id), "npc_id": npc_id, "kind": "move",
                        "text": f"{name_of(npc_id)} 到达{new_place}。",
                        "_key": f"arrive:{npc_id}:{current['time']}:{new_place}",
                    })
            # 新 Goal
            for goal in data["goals"]:
                if goal in old["goals"]:
                    continue
                self._add({
                    "time": current["time"], "time_label": time_label(current["time"]),
                    "npc": name_of(npc_id), "npc_id": npc_id, "kind": "goal",
                    "text": f"{name_of(npc_id)} 有了新打算：{self._goal_text(goal, lookup, locations)}。",
                    "_key": f"goal:{npc_id}:{goal}:{current['time']}",
                })
            # Goal 完成 / 失败
            for goal, status in data["goal_states"].items():
                if old["goal_states"].get(goal) == status:
                    continue
                if status == "completed":
                    text = f"{name_of(npc_id)} 完成了「{self._goal_text(goal, lookup, locations)}」。"
                elif status == "failed":
                    text = f"{name_of(npc_id)} 没能完成「{self._goal_text(goal, lookup, locations)}」。"
                else:
                    continue
                self._add({
                    "time": current["time"], "time_label": time_label(current["time"]),
                    "npc": name_of(npc_id), "npc_id": npc_id, "kind": "goal_state",
                    "text": text, "_key": f"state:{npc_id}:{goal}:{status}:{current['time']}",
                })
            # belief 变化
            for key, value in data["beliefs"].items():
                if old["beliefs"].get(key) == value:
                    continue
                self._add({
                    "time": current["time"], "time_label": time_label(current["time"]),
                    "npc": name_of(npc_id), "npc_id": npc_id, "kind": "belief",
                    "text": f"{name_of(npc_id)} 现在认为：{belief_text(key, value)}",
                    "_key": f"belief:{npc_id}:{key}:{value}:{current['time']}",
                })
            # 关系变化
            for key, value in data["relationships"].items():
                if old["relationships"].get(key) == value:
                    continue
                who = "玩家" if key == "player" else name_of(key)
                self._add({
                    "time": current["time"], "time_label": time_label(current["time"]),
                    "npc": name_of(npc_id), "npc_id": npc_id, "kind": "relationship",
                    "text": f"{name_of(npc_id)} 对 {who} 的态度发生了变化。",
                    "_key": f"rel:{npc_id}:{key}:{value}:{current['time']}",
                })
            # 新记忆
            if data["memory_len"] > old["memory_len"]:
                summary = memory_text(data.get("last_memory"))
                if summary:
                    self._add({
                        "time": current["time"], "time_label": time_label(current["time"]),
                        "npc": name_of(npc_id), "npc_id": npc_id, "kind": "memory",
                        "text": f"{name_of(npc_id)} 记住了：{summary}",
                        "_key": f"mem:{npc_id}:{summary[:60]}",
                    })

        # 线索被发现
        for key, data in current["evidence"].items():
            old = previous["evidence"].get(key)
            old_by = set()
            if old:
                old_by |= set(old["discovered_by"] or [])
                old_by |= set(old["auto_by"] or [])
                if old.get("investigated_by"):
                    old_by.add(old["investigated_by"])
            new_by = set(data["discovered_by"] or []) | set(data["auto_by"] or [])
            if data.get("investigated_by"):
                new_by.add(data["investigated_by"])
            for npc_id in sorted(new_by - old_by):
                entry = next(
                    (e for e in (snapshot.get("evidence") or [])
                     if self._evidence_key(e) == key), {}
                )
                position = entry.get("location") or [0.0, 0.0]
                label = evidence_name(entry, place_of(position, locations))
                self._add({
                    "time": current["time"], "time_label": time_label(current["time"]),
                    "npc": name_of(npc_id), "npc_id": npc_id, "kind": "clue",
                    "text": f"{name_of(npc_id)} 发现了一条线索：{label}。",
                    "_key": f"clue:{key}:{npc_id}",
                })

        # 真实对话
        if current["dialogues"] > previous["dialogues"]:
            for entry in (snapshot.get("dialogues") or [])[previous["dialogues"]:]:
                speaker = entry.get("speaker_id") or "?"
                self._add({
                    "time": entry.get("time", current["time"]),
                    "time_label": time_label(entry.get("time", current["time"])),
                    "npc": name_of(speaker) if speaker != "player" else "玩家",
                    "npc_id": speaker, "kind": "dialogue",
                    "text": f"{name_of(speaker) if speaker != 'player' else '玩家'}：{entry.get('content', '')}",
                    "_key": f"dlg:{entry.get('time')}:{speaker}:{str(entry.get('content'))[:24]}",
                })
        if current["dialogue_log"] > previous["dialogue_log"]:
            log = (snapshot.get("open_world") or {}).get("dialogue_log") or []
            for entry in log[previous["dialogue_log"]:]:
                npc_name = entry.get("npc") or name_of(entry.get("npc_id", ""))
                self._add({
                    "time": entry.get("time", current["time"]),
                    "time_label": time_label(entry.get("time", current["time"])),
                    "npc": npc_name, "npc_id": entry.get("npc_id"), "kind": "dialogue",
                    "text": f"玩家对 {npc_name} 说：{entry.get('player', '')}",
                    "_key": f"plog:{entry.get('time')}:{npc_name}:{str(entry.get('player'))[:24]}",
                })

        self.activity.sort(key=lambda item: item.get("time") or 0.0)

    @staticmethod
    def _goal_text(goal, names, locations):
        """把内部 goal 翻成中文；解析不出目标时只给泛化说法，绝不回退到内部 ID。"""
        def name_of(value):
            return names.get(value) or "某个人"

        def place_of_value(value):
            return location_name(value, "某个地方")

        def item_of_value(value):
            return item_name(value, "某样东西")

        def evidence_of_value(value):
            text = str(value or "")
            if text in names:
                return f"关于 {names[text]} 的线索"
            return "一条线索"

        return goal_text(
            goal,
            npc_name=name_of,
            place=place_of_value,
            item=item_of_value,
            evidence=evidence_of_value,
        )

    # -- 读取 ---------------------------------------------------------
    def activity_log(self, limit=60):
        with self._lock:
            return [dict(item) for item in self.activity[-limit:]][::-1]

    def track_of(self, npc_id, limit=40):
        with self._lock:
            return [dict(item) for item in (self.tracks.get(npc_id) or [])[-limit:]]


def player_action_text(record):
    """玩家行为的真实来源是 Action 管线；这里只做中文化，不新增事实。"""
    action = str((record or {}).get("action") or "")
    label = PLAYER_ACTION_TEXTS.get(action) or "做了一个动作"
    raw = str((record or {}).get("raw_text") or "").strip()
    if raw and any("\u4e00" <= ch <= "\u9fff" for ch in raw) and raw not in label:
        return f"{label}（{raw}）"
    return label


def world_distance(first, second):
    """两个坐标之间的直线距离（展示层用，不修改世界）。"""
    if not first or not second:
        return 9e9
    dx = float(first[0]) - float(second[0])
    dy = float(first[1]) - float(second[1])
    return (dx * dx + dy * dy) ** 0.5


def build_player_view(engine, observer, locations=None, narrative=None, focus_ids=None):
    """把真实 WorldState 投影成"玩家看得懂"的视图模型（只读）。

    玩家行为只取 Action 管线日志；NPC/世界/AI 编导的记录都有独立分类，
    绝不混进"我做过什么"。线索按稳定身份聚合成实体。
    """
    snapshot = engine.snapshot()
    open_world = snapshot.get("open_world") or {}
    locations = dict(locations or {})
    if observer is not None:
        observer.observe(snapshot)

    npc_by_id = {npc["id"]: npc for npc in snapshot.get("npcs", [])}
    raw_names = {npc_id: (npc.get("name") or npc_id) for npc_id, npc in npc_by_id.items()}
    name_counts = {}
    for value in raw_names.values():
        name_counts[value] = name_counts.get(value, 0) + 1
    names = {}
    for npc_id, base in raw_names.items():
        if name_counts.get(base, 0) > 1:
            names[npc_id] = f"{base}（{role_name(npc_by_id[npc_id].get('role'))}）"
        else:
            names[npc_id] = base

    def name_of(npc_id):
        return names.get(npc_id) or "某个人"

    item_lookup = {}
    for item in snapshot.get("items") or []:
        label = item.get("label") or item.get("type") or "物品"
        item_lookup[str(item.get("id"))] = label
        item_lookup.setdefault(str(item.get("type")), label)

    def item_of(value):
        key = str(value or "")
        if key in item_lookup:
            return item_lookup[key]
        return item_name(key, "某样东西")

    def place_name(value):
        key = str(value or "").strip().lower()
        if key in locations:
            return location_name(key)
        return location_name(key, "某个地方")

    def evidence_of(value):
        text = str(value or "")
        if text.startswith("ev_"):
            return "一条线索"
        if text in names:
            return f"关于 {names[text]} 的线索"
        if text.lower() in locations:
            return f"{location_name(text)} 的线索"
        return "一条线索"

    def goal_zh(goal):
        return goal_text(goal, npc_name=name_of, place=place_name, item=item_of, evidence=evidence_of)

    world_time = float(snapshot.get("time") or 0.0)
    weather = (snapshot.get("environment") or {}).get("weather") or {}
    player = snapshot.get("player") or {}
    player_position = player.get("position") or [0.0, 0.0]
    player_place = place_of(player_position, locations)

    # ---------------------------------------------------------- 玩家行为
    # 唯一可信来源：OpenWorldEngine._finish_action 写入的 Action 管线日志。
    player_actions = []
    for record in open_world.get("player_actions") or []:
        # 逐步移动不进入"我做过的动作"；移动只按"停留 >= 5 个时间刻"记录。
        if str(record.get("action")) == "move":
            continue
        moment = float(record.get("time") or 0.0)
        player_actions.append({
            "time": moment,
            "time_label": time_label(moment),
            "action": record.get("action") or "",
            "text": player_action_text(record),
            "success": bool(record.get("success")),
            "category": CAT_PLAYER_ACTION if record.get("success") else CAT_PLAYER_ACTION,
            "result": "已执行" if record.get("success") else "没有成功",
            "raw_text": str(record.get("raw_text") or ""),
        })

    # 玩家的想法（没有执行，单独分类，绝不进入 player_actions）
    player_inputs = []
    for entry in player.get("memory") or []:
        if str(entry.get("type")) != "thought":
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        player_inputs.append({
            "time": float(entry.get("time") or 0.0),
            "time_label": time_label(entry.get("time") or 0.0),
            "text": content,
            "category": CAT_PLAYER_INPUT,
        })

    # ------------------------------------------------------------- 线索
    clue_entities = build_clue_entities(snapshot, observer, locations, name_of)
    clues = []
    for entity in clue_entities:
        holder = entity.get("holder")
        if holder:
            current = f"在 {name_of(holder)} 身上"
        else:
            current = f"在{entity['place']}"
        clues.append({
            **entity,
            "discoverers": [name_of(npc) for npc in entity["discoverers"]],
            "status_label": "已发现" if entity["discovered"] else "未发现",
            "current": current,
        })

    # 玩家是否"接触过"某个 NPC 的真实依据
    talked_ids = {
        str(entry.get("npc_id"))
        for entry in (open_world.get("dialogue_log") or [])
        if entry.get("npc_id")
    }
    heard_ids = set()
    if observer is not None:
        for item in observer.heard_log(120):
            for key in ("speaker", "listener"):
                value = item.get(key)
                if value and value != "player":
                    heard_ids.add(str(value))

    # --------------------------------------------------- 主要/次要人物
    scores = importance_scores(snapshot, observer, clue_entities, locations)
    people = []
    for npc in snapshot.get("npcs") or []:
        npc_id = npc["id"]
        position = npc.get("position") or [0.0, 0.0]
        goals = list(npc.get("goals") or [])
        states = npc.get("goal_states") or {}
        active = [g for g in goals if (states.get(g) or {}).get("status", "active") == "active"]
        goal = active[0] if active else (goals[0] if goals else None)
        progress = npc.get("action_progress") or {}
        if npc.get("following_player"):
            action = "正在跟着玩家"
        elif progress.get("action"):
            action = f"正在{action_name(progress.get('action'))}"
        elif str(npc.get("current_action") or "").startswith("Moving"):
            action = "正在赶路"
        else:
            action = "原地待着"
        info = scores.get(npc_id) or {"score": 0, "reasons": []}
        tier, tier_label = tier_of(info["score"])
        # "玩家是否接触过"：和玩家说过话 / 出现在玩家听力范围 / 该 NPC 观察过玩家
        nearby = False
        try:
            nearby = world_distance(player_position, position) <= 12.0
        except Exception:
            nearby = False
        met = (
            npc_id in talked_ids
            or npc_id in heard_ids
            or nearby
            or any(str(k).startswith("player") for k in (npc.get("beliefs") or {}))
        )
        people.append({
            "id": npc_id,
            "name": name_of(npc_id),
            "title": role_name(npc.get("role")),
            "place": place_of(position, locations),
            "position": position,
            "tier": tier,
            "tier_label": tier_label,
            "score": info["score"],
            "reasons": list(info["reasons"]),
            "action": action,
            "goal": goal_zh(goal) if goal else "目前没有明确打算",
            "following_player": bool(npc.get("following_player")),
            "met": bool(met),
        })
    people.sort(key=lambda item: -item["score"])

    # --------------------------------------------------------- 最近发生
    category_by_kind = {
        "move": CAT_NPC_ACTION, "goal": CAT_NPC_ACTION, "goal_state": CAT_NPC_ACTION,
        "belief": CAT_NPC_ACTION, "memory": CAT_NPC_ACTION, "relationship": CAT_NPC_ACTION,
        "clue": CAT_EVIDENCE, "dialogue": CAT_NPC_DIALOGUE, "action": CAT_NPC_ACTION,
    }
    recent = []
    for item in (observer.activity_log(60) if observer is not None else []):
        category = category_by_kind.get(str(item.get("kind")), CAT_WORLD)
        recent.append({
            "time": float(item.get("time") or 0.0),
            "time_label": item.get("time_label", ""),
            "text": item.get("text", ""),
            "category": category,
            "category_label": CATEGORY_LABELS.get(category, "世界事件"),
            "npc_id": item.get("npc_id"),
        })
    for action in player_actions[-8:]:
        recent.append({
            "time": action["time"], "time_label": action["time_label"],
            "text": action["text"], "category": CAT_PLAYER_ACTION,
            "category_label": CATEGORY_LABELS[CAT_PLAYER_ACTION], "npc_id": None,
        })
    if observer is not None:
        # 玩家停留（>= 5 个时间刻）才是值得记录的位置
        for stay in observer.stays_of("player")[-3:]:
            recent.append({
                "time": float(stay.get("end") or 0.0),
                "time_label": time_label(stay.get("end") or 0.0),
                "text": f"我在{stay.get('place')}停留了约 {stay.get('ticks')} 个时间刻",
                "category": CAT_PLAYER_DWELL,
                "category_label": CATEGORY_LABELS[CAT_PLAYER_DWELL], "npc_id": None,
            })
        current = observer.current_stay("player")
        if current:
            recent.append({
                "time": world_time,
                "time_label": time_label(world_time),
                "text": f"我一直在{current['place']}停留（已 {current['ticks']} 个时间刻）",
                "category": CAT_PLAYER_DWELL,
                "category_label": CATEGORY_LABELS[CAT_PLAYER_DWELL], "npc_id": None,
            })
    recent.sort(key=lambda item: item["time"])

    # ------------------------------------------------------------- 地图
    # ---- 地图分层：谁进入玩家视野由"接触 / 发现 / 关注"决定 ----
    # 关注列表：玩家显式选择；没有选择时默认关注"主要人物"（由真实状态算出的分级）
    if focus_ids is not None:
        focus_set = {str(x) for x in focus_ids}
    else:
        focus_set = {p["id"] for p in people if p["tier"] == "main"}
        if not focus_set:
            # 还没有"主要人物"时，默认关注玩家已经接触过的人
            focus_set = {p["id"] for p in people if p["met"]}
    focused_people = [p for p in people if p["id"] in focus_set]
    focused_names = {p["name"] for p in focused_people}

    def clue_is_hint(clue):
        """大地图只提示"确实需要玩家关注"的关键线索。"""
        if not clue["discovered"]:
            return False                      # 没被发现的线索绝不上大地图
        if any(name in clue["discoverers"] for name in focused_names):
            return True                       # 关注的人发现了它
        if clue.get("holder") and name_of(clue["holder"]) in focused_names:
            return True                       # 关注的人正拿着它
        return bool(clue.get("has_follow_up"))

    map_npcs = [
        {"id": p["id"], "name": p["name"], "place": p["place"], "position": p["position"],
         "tier": p["tier"], "met": p["met"]}
        for p in focused_people
    ]
    map_clue_hints = [
        {"key": c["key"], "name": c["name"], "position": c["position"], "place": c["place"],
         "discovered": c["discovered"]}
        for c in clues if clue_is_hint(c)
    ]

    # 当地信息：只看"玩家所在位置 + 关注的人物 + 已发现的线索"
    local_npcs = [p for p in focused_people if p["place"] == player_place]
    local_clues = [c for c in clues if c["discovered"] and c["place"] == player_place]
    local_info = {
        "place": player_place,
        "npcs": [
            {"id": p["id"], "name": p["name"], "title": p["title"], "action": p["action"],
             "goal": p["goal"], "place": p["place"]}
            for p in local_npcs
        ],
        "clues": [
            {"key": c["key"], "name": c["name"], "status_label": c["status_label"],
             "current": c["current"], "discoverers": c["discoverers"]}
            for c in local_clues
        ],
        "hidden_here": 0,
    }
    # 关注候选：主要/次要人物，或玩家真正接触过的人（不泄露没接触过的背景人物细节）
    focus_candidates = [
        {"id": p["id"], "name": p["name"], "title": p["title"], "tier": p["tier"],
         "tier_label": p["tier_label"], "met": p["met"], "focused": p["id"] in focus_set}
        for p in people
        if p["tier"] in {"main", "side"} or p["met"]
    ]

    # --------------------------------------------------- 我自己的经历
    my_story = {"place": player_place, "position": player_position,
                "actions": [], "thoughts": [], "stays": [], "places": [], "daily": []}
    if observer is not None:
        my_story["stays"] = [dict(item) for item in observer.stays_of("player")]
        my_story["places"] = [dict(item) for item in observer.places_of("player")]
        my_story["actions"] = player_actions[::-1][:40]
        my_story["thoughts"] = player_inputs[::-1][:20]
        current_stay = observer.current_stay("player")
        my_story["current_stay"] = current_stay
        if current_stay:
            # 还在停留中的位置也列出来（停留 >= 5 个时间刻）
            my_story["stays"] = my_story["stays"] + [{
                "time_label": current_stay["time_label"],
                "place": current_stay["place"],
                "ticks": current_stay["ticks"],
                "ongoing": True,
                "text": f"我在{current_stay['place']}停留了约 {current_stay['ticks']} 个时间刻（仍在继续）",
            }]
        entries = []
        for item in player_actions:
            entries.append({"time": item["time"], "text": item["text"], "kind": "action"})
        for item in player_inputs:
            entries.append({"time": item["time"], "text": f"我想过：{item['text']}", "kind": "memory"})
        for item in my_story["stays"]:
            entries.append({"time": item.get("end", 0.0), "text": item.get("text", ""), "kind": "move"})
        for item in my_story["places"]:
            entries.append({"time": item.get("time", 0.0), "text": item.get("text", ""), "kind": "move"})
        my_story["daily"] = day_paragraphs(entries)

    # ------------------------------------------------------- 每日故事
    # 一天结束时，把当天真实发生的事实交给叙事层，整理成"一幕一幕"。
    stories = []
    if narrative is not None:
        current_day = int(world_time // DAY) + 1
        for offset in (1, 2, 3):
            day = current_day - offset
            if day < 1:
                continue
            facts = collect_day_facts(snapshot, observer, day, names, locations)
            if not facts:
                continue
            scene = narrative.scene_for(day, facts)
            stories.append({
                "day": day,
                "label": f"第{day}天",
                "ready": bool(scene),
                "title": (scene or {}).get("title") or f"第{day}天",
                "source": (scene or {}).get("source") or "",
                "scenes": (scene or {}).get("scenes") or [],
                "fact_count": len(facts),
            })
    stories.sort(key=lambda item: -item["day"])

    # ------------------------------------------------------- 约定 / 我听到的
    registry = getattr(engine, "commitments", None)
    commitments = []
    if registry is not None:
        for item in registry.active(12):
            goal_type = item.get("goal_type")
            target = item.get("target")
            if goal_type:
                phrase = goal_text(
                    f"{goal_type}:{target}" if target else str(goal_type),
                    npc_name=name_of, place=place_name, item=item_of, evidence=evidence_of,
                )
            else:
                phrase = item.get("action") or "去做一件事"
            commitments.append({
                "npc": name_of(item.get("npc_id")),
                "action": phrase,
                "status": item.get("status"),
                "status_label": item.get("status_label"),
                "commitment": item.get("commitment"),
                "reason": item.get("reason", ""),
            })
    notices = registry.recent_notices(10) if registry is not None else []

    heard = []
    if observer is not None:
        for item in observer.heard_log(40):
            speaker = item.get("speaker")
            listener = item.get("listener")
            heard.append({
                "time_label": item.get("time_label"),
                "kind": item.get("kind"),
                "kind_label": {"to_player": "对我说", "player_said": "我说的", "overheard": "我路过听到"}.get(item.get("kind"), "对话"),
                "speaker": "玩家" if speaker == "player" else name_of(speaker),
                "listener": "玩家" if listener == "player" else (name_of(listener) if listener else ""),
                "text": item.get("text", ""),
            })
    heard.sort(key=lambda item: item["time_label"])

    # ------------------------------------------------------- Debug 数据
    timeline_by_id = {e.get("event_id"): e for e in snapshot.get("timeline") or []}
    resolved = {e.get("event_id"): e for e in snapshot.get("story_event_history") or []}
    feed_source = {
        entry.get("event_id"): entry.get("source")
        for entry in (open_world.get("director_feed") or [])
        if entry.get("event_id")
    }

    def directives_of(effects):
        out = []
        for effect in effects or []:
            text = effect_text(effect, npc_name=name_of,
                               place_of_xy=lambda xy: place_of(xy, locations),
                               item=item_of, evidence=evidence_of)
            if text:
                out.append(text)
        return out

    debug_events = []
    for entry in snapshot.get("timeline") or []:
        source = str(entry.get("source") or "scenario")
        directives = directives_of(entry.get("effects"))
        if not directives:
            continue
        status = str(entry.get("status") or "")
        if source != "ai_director" and status in {"pending", "deferred"}:
            continue
        debug_events.append({
            "event_id": entry.get("event_id"),
            "time": float(entry.get("trigger_at") or 0.0),
            "time_label": time_label(entry.get("trigger_at") or 0.0),
            "author": "AI 编导" if source == "ai_director" else "世界自身发展",
            "is_ai": source == "ai_director",
            "directives": directives,
            "npcs": sorted({name_of(e.get("npc_id")) for e in entry.get("effects") or [] if e.get("npc_id")}),
            "status": {
                "pending": "还没有发生（在等条件）", "deferred": "暂时搁置",
                "triggered": "已经发生", "completed": "已经发生",
                "cancelled": "被取消", "failed": "执行失败",
            }.get(status, status or "已排期"),
            "reason": reason_text((resolved.get(entry.get("event_id")) or {}).get("reason")),
            "raw_title": entry.get("title") or "",
            "raw_effects": entry.get("effects") or [],
        })
    for payload in snapshot.get("event_history") or []:
        eid = payload.get("event_id")
        if not eid or eid in timeline_by_id:
            continue
        source = feed_source.get(eid)
        if source != "llm":
            continue
        directives = directives_of(payload.get("effects"))
        if not directives:
            continue
        debug_events.append({
            "event_id": eid, "time": float(payload.get("time") or 0.0),
            "time_label": time_label(payload.get("time") or 0.0),
            "author": "AI 编导", "is_ai": True, "directives": directives,
            "npcs": sorted({name_of(e.get("npc_id")) for e in payload.get("effects") or [] if e.get("npc_id")}),
            "status": "已经发生", "reason": "",
            "raw_title": "", "raw_effects": payload.get("effects") or [],
        })
    debug_events.sort(key=lambda e: e["time"])

    dialogue_llm = bool(getattr(getattr(engine, "dialogue_system", None), "api_key", ""))
    director_llm = bool(getattr(getattr(engine, "director", None), "api_key", ""))
    goal_provider = getattr(engine, "npc_goal_provider", None)

    debug = {
        "llm": {
            "dialogue_enabled": dialogue_llm,
            "director_enabled": director_llm,
            "npc_goal_provider": type(goal_provider).__name__ if goal_provider else None,
        },
        "events": debug_events[::-1],
        "timeline": [
            {"event_id": e.get("event_id"), "status": e.get("status"),
             "trigger_at": e.get("trigger_at"), "source": e.get("source")}
            for e in snapshot.get("timeline") or []
        ],
        "player_action_log": [dict(item) for item in open_world.get("player_actions") or []],
        "suspect_actor_defaults": [
            {"time": h.get("time"), "action": h.get("action"), "actor": h.get("actor")}
            for h in suspect_actor_defaults(snapshot)
        ],
        "director_feed": [dict(item) for item in open_world.get("director_feed") or []],
        "recent_raw": recent[-40:][::-1],
    }

    dialogues = snapshot.get("dialogues") or []
    player_log = open_world.get("dialogue_log") or []
    npcs_detail = {}
    for p in people:
        talks = []
        for d in dialogues:
            speaker_id = d.get("speaker_id")
            if speaker_id != p["id"]:
                continue
            talks.append({
                "time": float(d.get("time") or 0.0),
                "time_label": time_label(d.get("time") or 0.0),
                "text": f"{p['name']}：{d.get('content', '')}",
                "category": CAT_NPC_DIALOGUE,
            })
        for d in player_log:
            if d.get("npc_id") != p["id"]:
                continue
            talks.append({
                "time": float(d.get("time") or 0.0),
                "time_label": time_label(d.get("time") or 0.0),
                "text": f"玩家：{d.get('player', '')}",
                "category": CAT_PLAYER_ACTION,
            })
            if d.get("reply"):
                talks.append({
                    "time": float(d.get("time") or 0.0),
                    "time_label": time_label(d.get("time") or 0.0),
                    "text": f"{p['name']}：{d.get('reply')}",
                    "category": CAT_NPC_DIALOGUE,
                })
        talks.sort(key=lambda item: item["time"])
        npcs_detail[p["id"]] = {
            **p,
            "talks": talks[-10:],
            "discoveries": [c["name"] for c in clues if p["name"] in c["discoverers"]],
        }

    return {
        "time": world_time,
        "time_label": time_label(world_time),
        "day": int(world_time // DAY) + 1,
        "weather": {"label": weather_label(weather), "rain": bool(weather.get("rain")),
                    "temperature": weather.get("temperature")},
        "paused": not bool(open_world.get("running")),
        "world_overview": {
            "time_label": time_label(world_time),
            "weather": weather_label(weather),
            "place": player_place,
            "people_total": len(people),
            "people_main": sum(1 for p in people if p["tier"] == "main"),
            "clue_total": len(clues),
            "clue_discovered": sum(1 for c in clues if c["discovered"]),
            "recent_total": len(recent),
        },
        "recent": recent[-14:][::-1],
        "stories": stories,
        "commitments": commitments,
        "notices": notices,
        "heard": heard,
        "people": people,
        "npcs_detail": npcs_detail,
        "clues": clues,
        "map": {
            "locations": [{"id": k, "name": location_name(k), "position": list(v)}
                          for k, v in locations.items()],
            "npcs": map_npcs,
            "clues": map_clue_hints,
            "player": {"position": player_position, "place": player_place},
            "focus_ids": sorted(focus_set),
            "focus_is_auto": focus_ids is None,
        },
        "local_info": local_info,
        "focus_candidates": focus_candidates,
        "my_story": my_story,
        "player": {"position": player_position, "place": player_place,
                   "inventory": list(player.get("inventory") or [])},
        "npcs": map_npcs,
        "tracks": {t["id"]: t for t in [
            {"id": p["id"], "name": p["name"], "points": observer.track_of(p["id"]) if observer else []}
            for p in people
        ] if t["points"]},
        "debug": debug,
    }


def day_paragraphs(entries, limit_per_day=6):
    """把真实记录按天合成段落（一天一段，而不是一行一条）。"""
    def entry_time(entry):
        return float(entry.get("time", entry.get("start", entry.get("end", 0.0))) or 0.0)

    def day_of(seconds):
        return int(float(seconds or 0.0) // DAY) + 1

    def period(seconds):
        hour = (float(seconds or 0.0) % DAY) / 3600.0
        if hour < 6:
            return "凌晨"
        if hour < 12:
            return "上午"
        if hour < 18:
            return "下午"
        return "晚上"

    priority = {"clue": 0, "dialogue": 0, "move": 1, "goal_state": 1,
                "action": 1, "goal": 2, "belief": 3, "memory": 4}
    buckets = {}
    for entry in entries:
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        buckets.setdefault(day_of(entry_time(entry)), []).append(entry)
    out = []
    for day in sorted(buckets):
        items = sorted(buckets[day], key=lambda e: (priority.get(e.get("kind"), 5), entry_time(e)))
        chosen, seen = [], set()
        for entry in items:
            text = str(entry.get("text") or "").strip().rstrip("。")
            if not text or text[:14] in seen:
                continue
            seen.add(text[:14])
            chosen.append(entry)
            if len(chosen) >= limit_per_day:
                break
        if not chosen:
            continue
        chosen.sort(key=entry_time)
        body = "；".join(str(e.get("text") or "").strip().rstrip("。") for e in chosen)
        stamp = time_label(entry_time(chosen[0]))
        out.append({"day": day, "label": f"第{day}天",
                    "text": f"{stamp}{period(entry_time(chosen[0]))}：{body}。"})
    return out
