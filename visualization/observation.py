"""叙事观察层（Narrative Observation Layer）—— 只读。

职责边界：

    WorldState / 只读快照
        ↓
    Observation Layer（本模块：分类 / 线索身份 / 重要度）
        ↓
    Player View Model（player_view.build_player_view）
        ↓
    Frontend

硬性约束：
* 只读：不修改 WorldState，不生成剧情，不调用 LLM。
* 玩家行为只认「玩家 Action 管线」写下的日志（snapshot.open_world.player_actions），
  绝不用字符串去猜"这看起来像玩家做的"。
* 线索用稳定身份聚合，同一个真实线索只出现一次；不合并确实不同的实体。
* 主要人物由真实状态算分，不写人物名单。
"""

try:
    from .zh_labels import (
        belief_text,
        effect_text,
        description_summary,
        evidence_name,
        evidence_type_label,
        goal_text,
        history_text,
        item_name,
        location_name,
        memory_text,
        sanitize_visible,
        reason_text,
        role_name,
        weather_label,
    )
except ImportError:  # 脚本方式运行
    from zh_labels import (
        belief_text,
        effect_text,
        description_summary,
        evidence_name,
        evidence_type_label,
        goal_text,
        history_text,
        item_name,
        location_name,
        memory_text,
        sanitize_visible,
        reason_text,
        role_name,
        weather_label,
    )

DAY = 86400.0

# 记录分类（UI 依据真实来源分类，而不是靠字符串猜测）
CAT_PLAYER_ACTION = "player_action"       # 玩家 Action 管线真实执行
CAT_PLAYER_INPUT = "player_input"         # 玩家的想法（未执行）
CAT_PLAYER_DWELL = "player_dwell"         # 玩家在哪里停留（不是行为）
CAT_NPC_ACTION = "npc_action"             # NPC 自主行动
CAT_NPC_DIALOGUE = "npc_dialogue"         # 真实发生的对话
CAT_EVIDENCE = "evidence_event"           # 线索的产生/发现/流转
CAT_WORLD = "world_event"                 # 世界事件（含 AI 编导 / 预设剧情）
CAT_SYSTEM = "system"                     # 系统/天气等

CATEGORY_LABELS = {
    CAT_PLAYER_ACTION: "玩家行为",
    CAT_PLAYER_INPUT: "玩家的想法",
    CAT_PLAYER_DWELL: "我的停留",
    CAT_NPC_ACTION: "村民行动",
    CAT_NPC_DIALOGUE: "对话",
    CAT_EVIDENCE: "线索变化",
    CAT_WORLD: "世界事件",
    CAT_SYSTEM: "环境变化",
}

# 这些 actor 明确代表"不是玩家"
NPC_ACTOR_PREFIXES = ("npc", "NPC")
WORLD_ACTORS = {"story", "director", "simulation", "environment", "world", "system", "scenario", "ai_director"}


def time_label(seconds):
    seconds = float(seconds or 0.0)
    day = int(seconds // DAY) + 1
    rem = seconds % DAY
    return f"第{day}天 {int(rem // 3600):02d}:{int((rem % 3600) // 60):02d}"


def place_of(position, locations=None):
    """最近的已知地点（真实坐标表）；不编造地点。"""
    if not position:
        return "空地"
    locations = locations or {}
    best_name, best_distance = None, None
    for name, coords in locations.items():
        dx = float(position[0]) - float(coords[0])
        dy = float(position[1]) - float(coords[1])
        distance = (dx * dx + dy * dy) ** 0.5
        if best_distance is None or distance < best_distance:
            best_name, best_distance = name, distance
    if best_name is None or best_distance is None:
        return "空地"
    name = location_name(best_name)
    if best_distance <= 10.0:
        return name
    if best_distance <= 28.0:
        return f"{name}一带"
    return "远离村庄的空地"


# ---------------------------------------------------------------- 分类

def classify_history_entry(entry):
    """给 world.history 的条目分类。

    注意：这里**永远不会**返回 player_action。玩家行为只来自 Action 管线的
    专属日志（engine.player_actions），因为 world.add_history 的 actor
    默认值就是 "player"，直接信任它会误判。
    """
    actor = str((entry or {}).get("actor") or "")
    action = str((entry or {}).get("action") or "")
    if actor.startswith(NPC_ACTOR_PREFIXES) or action.startswith("npc_"):
        return CAT_NPC_ACTION
    if action in {"weather_changed", "environment_changed"} or actor == "environment":
        return CAT_SYSTEM
    if actor in WORLD_ACTORS:
        return CAT_WORLD
    if actor == "player":
        # 默认 actor 造成的疑似误标：归为世界事件，绝不归为玩家行为
        return CAT_WORLD
    return CAT_WORLD


def suspect_actor_defaults(snapshot):
    """审计用：world.history 里"actor 是默认值 player，但不是玩家行为"的条目。"""
    rows = []
    for entry in snapshot.get("history") or []:
        if str(entry.get("actor") or "") != "player":
            continue
        action = str(entry.get("action") or "")
        if not action.startswith("player_"):
            rows.append(entry)
    return rows


# ------------------------------------------------------------ 线索身份

def clue_key(entry):
    """一个真实线索的稳定身份：有 id 用 id，无 id 用类型+位置+创建时刻。"""
    entry = entry or {}
    eid = entry.get("id")
    if eid:
        return f"id:{eid}"
    location = entry.get("location") or [0.0, 0.0]
    created = entry.get("created_at", 0.0)
    return (
        f"auto:{entry.get('type') or 'evidence'}"
        f"@{float(location[0]):.2f},{float(location[1]):.2f}"
        f"@{float(created or 0.0):.0f}"
    )


def build_clue_entities(snapshot, observer, locations, name_of=None):
    """把 evidence / item / 观察记录聚合成"线索实体 + 生命周期"。"""
    items = {str(item.get("id")): item for item in snapshot.get("items") or []}
    records = observer.clue_entries() if observer is not None else {}
    entities = []
    for entry in snapshot.get("evidence") or []:
        key = clue_key(entry)
        record = records.get(key) or {}
        place = place_of(entry.get("location") or [0.0, 0.0], locations)
        discoverers = []
        for npc_id in entry.get("discovered_by") or []:
            discoverers.append(npc_id)
        if entry.get("investigated_by"):
            discoverers.append(entry["investigated_by"])
        for npc_id in entry.get("auto_investigated_by") or []:
            discoverers.append(npc_id)
        discoverers = sorted(set(discoverers))

        # 同一 id 的物品 = 同一实体（真实的携带/转移关系）
        linked_item = items.get(str(entry.get("id"))) if entry.get("id") else None
        holder = linked_item.get("holder") if linked_item else record.get("holder")

        lifecycle = []
        created = entry.get("created_at", record.get("created_at", 0.0))
        origin = record.get("origin") or ("玩家放置" if entry.get("owner") == "player" else "由世界事件产生")
        lifecycle.append({
            "time": created, "time_label": time_label(created),
            "stage": "产生", "text": f"{origin}（出现在{place}）",
        })
        def who(value):
            return name_of(value) if name_of else "有人"

        for event in record.get("events") or []:
            stage = str(event.get("stage") or "记录")
            if stage == "产生":
                continue  # 生命周期里已经有统一的"产生"条目，避免重复
            npc_id = event.get("npc_id")
            if stage == "发现":
                text = f"{who(npc_id)}发现了这条线索。"
            elif stage == "转移":
                text = f"线索被 {who(npc_id)} 收了起来。"
            else:
                text = str(event.get("text") or "")
            lifecycle.append({
                "time": event.get("time", 0.0), "time_label": event.get("time_label", ""),
                "stage": stage, "text": text, "npc_id": npc_id,
            })
        lifecycle.sort(key=lambda item: item.get("time") or 0.0)

        # 区别特征：全部来自真实字段，玩家能据此区分"这个账本"和别的账本
        type_label = evidence_type_label(entry.get("type"))
        summary = description_summary(entry.get("description"))
        attributes = [{"label": "线索类型", "value": type_label}]
        if summary:
            attributes.append({"label": "外观 / 内容", "value": summary})
        attributes.append({"label": "出现地点", "value": f"{place}（{time_label(created)}）"})
        attributes.append({"label": "来源", "value": origin})
        related_npc = entry.get("related_npc")
        if related_npc:
            attributes.append({"label": "关联人物", "value": name_of(related_npc) if name_of else "某个人"})
        related_object = entry.get("related_object")
        if related_object:
            attributes.append({"label": "关联物件", "value": str(related_object)})
        linked = entry.get("linked_belief")
        if linked:
            attributes.append({"label": "关联判断", "value": belief_text(linked)})
        follow_up = entry.get("follow_up_goal")
        if follow_up:
            attributes.append({
                "label": "后续动向",
                "value": goal_text(
                    follow_up,
                    npc_name=name_of or (lambda v: "某个人"),
                    place=lambda v: location_name(v, "某个地方"),
                    item=lambda v: item_name(v, "某样东西"),
                    evidence=lambda v: "一条线索",
                ),
            })
        if holder:
            attributes.append({"label": "当前持有人", "value": name_of(holder) if name_of else "某个人"})
        if discoverers:
            attributes.append({
                "label": "发现者",
                "value": "、".join(name_of(n) if name_of else "某个人" for n in discoverers),
            })

        # 背景记录：真实记忆 / 对话里提到这条线索的内容（越探索越多）
        related_records = []
        clue_id = str(entry.get("id")) if entry.get("id") else ""
        snippet = description_summary(entry.get("description"))[:12] if summary else ""
        raw_snippet = str(entry.get("description") or "").strip()[:16]
        # 只有"证据 id 精确匹配"或"足够长的描述片段匹配"才算相关，避免误关联
        needle = raw_snippet if len(raw_snippet) >= 8 else ""
        for npc in snapshot.get("npcs") or []:
            for memory in npc.get("memory") or []:
                content = str(memory.get("content") or "")
                memory_id = str(memory.get("evidence_id") or "")
                if (clue_id and memory_id == clue_id) or (needle and needle in content):
                    related_records.append({
                        "time_label": time_label(memory.get("time") or 0.0),
                        "who": name_of(npc.get("id")) if name_of else "某个人",
                        "text": memory_text(memory) or sanitize_visible(content),
                    })
        for dialogue in snapshot.get("dialogues") or []:
            content = str(dialogue.get("content") or "")
            if needle and needle in content:
                speaker = dialogue.get("speaker_id")
                related_records.append({
                    "time_label": time_label(dialogue.get("time") or 0.0),
                    "who": "玩家" if speaker == "player" else (name_of(speaker) if name_of else "某个人"),
                    "text": sanitize_visible(content),
                })
        related_records = related_records[-8:]

        substance = sum(1 for item in attributes if item["label"] in {"外观 / 内容", "关联人物", "关联物件", "关联判断", "后续动向"})
        substance += len(related_records)
        if substance >= 5:
            detail_level = "细节较完整"
        elif substance >= 2:
            detail_level = "有一些细节"
        else:
            detail_level = "细节很少（继续调查可以补充）"

        entities.append({
            "key": key,
            "name": evidence_name(entry, place),
            "type_label": type_label,
            "attributes": attributes,
            "detail_level": detail_level,
            "related_records": related_records,
            "type": entry.get("type"),
            "description": entry.get("description") or "",
            "place": place,
            "position": entry.get("location") or [0.0, 0.0],
            "origin": origin,
            "created_at": created,
            "created_label": time_label(created),
            "discoverers": discoverers,
            "discovered": bool(discoverers),
            "holder": holder,
            "has_item": bool(linked_item),
            "has_follow_up": bool(entry.get("follow_up_goal")),
            "lifecycle": lifecycle,
        })
    # 只作为物品存在、没有对应 evidence 的实体不算线索（避免把普通物品当成线索）
    return entities


# ------------------------------------------------------------ 重要度

MAIN_THRESHOLD = 6
SIDE_THRESHOLD = 3


def importance_scores(snapshot, observer, clue_entities, locations):
    """由真实状态算"关注度"，不写人物名单，也不调用 LLM。"""
    now = float(snapshot.get("time") or 0.0)
    activity = observer.activity_log(200) if observer is not None else []
    player_actions = (snapshot.get("open_world") or {}).get("player_actions") or []
    dialogues = snapshot.get("dialogues") or []
    player_log = (snapshot.get("open_world") or {}).get("dialogue_log") or []

    scores = {}
    for npc in snapshot.get("npcs") or []:
        npc_id = npc["id"]
        name = npc.get("name") or npc_id
        score = 0
        reasons = []

        if npc.get("following_player"):
            score += 3
            reasons.append("正在跟着玩家")

        talked_with_player = any(
            npc_id in {d.get("speaker_id"), d.get("npc_id")}
            for d in (dialogues + player_log)
        ) or any(name in str(d.get("npc") or "") for d in player_log)
        if talked_with_player:
            score += 3
            reasons.append("和玩家说过话")

        touched = 0
        for action in player_actions:
            blob = f"{action.get('raw_text')} {action.get('message')}"
            if npc_id in blob or name in blob:
                touched += 1
        if touched:
            score += min(2, touched)
            reasons.append("玩家的行为直接涉及")

        if any(a.get("kind") == "relationship" and a.get("npc_id") == npc_id for a in activity):
            score += 1
            reasons.append("对玩家的态度发生了变化")

        if any(
            clue["discovered"] and (
                name in [n for n in clue["discoverers"]]
                or npc_id in clue["discoverers"]
            )
            for clue in clue_entities
        ):
            score += 2
            reasons.append("发现过线索")

        if any(a.get("kind") == "clue" and a.get("npc_id") == npc_id for a in activity):
            if "发现过线索" not in reasons:
                score += 2
                reasons.append("发现过线索")

        recent = [a for a in activity if a.get("npc_id") == npc_id and now - float(a.get("time") or 0.0) <= DAY]
        if recent:
            score += 1
            reasons.append("最近有行动")

        if npc.get("goals"):
            score += 1
            reasons.append("有正在进行的打算")

        scores[npc_id] = {"score": score, "reasons": reasons}

    # 参与近期 AI 编导事件的 NPC 额外加分（真实事件数据）
    for event in (snapshot.get("timeline") or []):
        if str(event.get("source")) != "ai_director":
            continue
        involved = [e.get("npc_id") for e in event.get("effects") or [] if e.get("npc_id")]
        for npc_id in set(involved):
            if npc_id in scores:
                scores[npc_id]["score"] += 2
                if "出现在 AI 编导的事件里" not in scores[npc_id]["reasons"]:
                    scores[npc_id]["reasons"].append("出现在 AI 编导的事件里")
    return scores


def tier_of(score):
    if score >= MAIN_THRESHOLD:
        return "main", "主要人物"
    if score >= SIDE_THRESHOLD:
        return "side", "次要人物"
    return "background", "背景人物"
