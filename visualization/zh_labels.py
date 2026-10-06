"""玩家可见的中文显示层（纯展示映射，不修改任何世界状态）。

这里只做「内部标识 -> 中文可读文本」的翻译。所有翻译都基于真实
WorldState 中已经存在的字段；不做任何推测，也不生成新内容。
"""

LOCATION_NAMES = {
    "home": "家",
    "forest": "森林",
    "town": "村庄",
    "stone_road": "石板路",
    "village": "村庄",
    "mayor_house": "村长家",
    "home_office": "村长家",
    "mayor_office": "村长办公室",
    "town_office": "村办公室",
    "open_ground": "空地",
    "mill": "磨坊",
    "the_mill": "磨坊",
    "old_mill": "旧磨坊",
    "town_square": "村广场",
    "square": "村广场",
    "office": "办公室",
    "tavern": "酒馆",
    "shop": "店铺",
    "market": "集市",
    "farm": "农田",
    "field": "农田",
    "river": "河边",
    "well": "水井",
}

# 未知地点名的关键词兜底（只做中文改名，不改变含义）
LOCATION_KEYWORDS = (
    ("mill", "磨坊"),
    ("forest", "森林"),
    ("wood", "森林"),
    ("town", "村庄"),
    ("village", "村庄"),
    ("home", "家"),
    ("house", "家"),
    ("road", "路"),
    ("square", "广场"),
    ("office", "办公室"),
    ("farm", "农田"),
    ("field", "农田"),
    ("river", "河边"),
    ("tavern", "酒馆"),
    ("shop", "店铺"),
)

ROLE_NAMES = {
    "mayor": "村长",
    "accountant": "会计",
    "villager": "村民",
    "external_auditor": "外部审计员",
    "auditor": "审计员",
    "merchant": "商人",
    "guard": "守卫",
    "watchman": "守夜人",
    "bounty_hunter": "赏金猎人",
    "blacksmith": "铁匠",
    "farmer": "农夫",
}

ACTION_NAMES = {
    "move": "移动",
    "search": "搜查",
    "investigate": "调查",
    "inspect": "查看",
    "take": "拿走",
    "talk": "交谈",
    "ask": "询问",
    "wait": "等待",
    "follow": "跟随",
    "stop_follow": "停止跟随",
    "give": "交给",
    "drop": "放下",
    "open": "打开",
    "work": "干活",
    "escape": "离开",
    "attack": "动手",
    "rescue": "救助",
    "hide": "藏起来",
    "create_rumor": "散播传闻",
    "create_evidence": "留下线索",
    "buy": "购买",
    "explore": "探索",
    "conversation_only": "交谈",
}

ITEM_NAMES = {
    "ledger_copy": "账本复印件",
    "ledger": "账本",
    "coins": "钱袋",
    "funds": "公款",
    "money": "钱",
    "oil": "油",
    "key": "钥匙",
    "food": "食物",
    "bread": "干粮",
    "tool": "工具",
    "hammer": "锤子",
    "note": "字条",
    "letter": "信件",
    "page": "纸页",
    "report": "记录",
    "receipt": "收据",
    "badge": "证件",
    "cloth": "布包",
}

EVIDENCE_NAMES = {
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
    "ren_hiding": "躲藏的痕迹",
    "ledger_copy": "账本复印件",
    "torn_ledger_page": "撕破的账本页",
    "burnt_ledger_page": "烧焦的账本页",
    "false_ledger_note": "伪造的账本字条",
    "auditor_badge": "审计员证件",
    "mill_key_report": "磨坊钥匙记录",
    "watchman_note": "守夜人的字条",
    "courier_letter": "信差的信件",
}

# 只翻译"确实存在的 belief 键"。未知键不做猜测，统一显示为泛化判断。
BELIEF_NAMES = {
    "mayor_is_corrupt": "村长可能与账目问题有关",
    "mayor_corrupt": "村长可能与账目问题有关",
    "ledger_missing": "账本可能已经不见了",
    "ledger_damaged": "账本可能被损坏过",
    "ledger_hidden_in_mill": "账本可能藏在磨坊",
    "ledger_copy_is_real": "那份账本复印件可能是真的",
    "ren_is_threat": "Ren 可能是个威胁",
    "ren_is_investigating": "Ren 可能在调查什么",
    "ren_is_suspicious": "Ren 有些可疑",
    "ren_went_wrong_direction": "追查的方向可能不对",
    "ren_in_danger": "Ren 可能有危险",
    "player_is_threat": "玩家可能是个威胁",
    "player_is_ally": "玩家可能是盟友",
    "player_could_help": "玩家也许能帮上忙",
    "player_has_item_ledger": "玩家拿走了账本",
    "auditor_present": "审计员已经出现",
    "auditor_coming": "审计员正在赶来",
    "auditor_is_threat": "审计员是个威胁",
    "auditor_can_help": "审计员也许能帮上忙",
    "auditor_is_here": "审计员已经到了",
    "auditor_is_serious": "审计员在认真查账",
    "village_has_corruption": "村里可能有贪腐问题",
    "village_has_secret": "村里藏着秘密",
    "village_has_secrets": "村里藏着秘密",
    "village_has_embezzlement": "村里可能有挪用公款的事",
    "someone_manipulated_evidence": "有人动过现场痕迹",
    "suspect_false_evidence": "这些痕迹可能是假的",
    "danger_near_mill": "磨坊附近有危险",
    "mill_is_dangerous": "磨坊那边不安全",
}

WEATHER_NAMES = {
    "Sunny": "晴",
    "Cloudy": "多云",
    "Rain": "雨",
}

# 记忆来源 -> 中文前缀（用于"记忆"列表）
MEMORY_SOURCE_NAMES = {
    "evidence": "发现",
    "event": "经历",
    "rumor": "听说",
    "planner": "行动",
    "npc_action": "行动",
    "observation": "观察",
    "player": "玩家",
}


def _has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


_INTERNAL_ID = None
_ASCII_PAREN = None


def sanitize_visible(text):
    """去掉玩家不该看到的内部 ID 与英文技术前缀，保留真实中文内容。"""
    global _INTERNAL_ID, _ASCII_PAREN
    if _INTERNAL_ID is None:
        import re
        _INTERNAL_ID = re.compile(r"\b(?:ev|item|npc|NPC)_?[A-Za-z0-9_]*\b")
        _ASCII_PAREN = re.compile(r"[（(][A-Za-z_][A-Za-z0-9_ ]*[)）]")
    value = str(text or "")
    value = _INTERNAL_ID.sub("", value)
    value = _ASCII_PAREN.sub("", value)
    import re as _re
    match = _re.search(r"[\u4e00-\u9fff]", value)
    if match and match.start() > 0:
        prefix = value[:match.start()]
        if _re.search(r"[A-Za-z]{2,}", prefix):
            value = value[match.start():]
    value = value.replace("（）", "").replace("()", "")
    value = _re.sub(r"\s+([：:，,。；;])", r"\1", value)
    value = _re.sub(r"\s{2,}", " ", value)
    return value.strip(" :：")


def location_name(value, default="空地"):
    if not value:
        return default
    key = str(value).strip().lower()
    if key in LOCATION_NAMES:
        return LOCATION_NAMES[key]
    for hint, name in LOCATION_KEYWORDS:
        if hint in key:
            return name
    return default


def role_name(value, default="村民"):
    if not value:
        return default
    return ROLE_NAMES.get(str(value).strip().lower(), default)


def action_name(value, default="行动"):
    if not value:
        return default
    key = str(value).strip().lower()
    return ACTION_NAMES.get(key, default)


def item_name(value, default="物品"):
    if not value:
        return default
    key = str(value).strip().lower()
    if key in ITEM_NAMES:
        return ITEM_NAMES[key]
    for hint, name in ITEM_NAMES.items():
        if hint in key:
            return name
    return default


def weather_label(weather):
    condition = str((weather or {}).get("condition", "") or "")
    if condition in WEATHER_NAMES:
        return WEATHER_NAMES[condition]
    return "多云" if not condition else condition


EVIDENCE_KEYWORDS = (
    ("footprint", "脚印"),
    ("water", "水迹"),
    ("ledger", "账本"),
    ("account", "账目"),
    ("letter", "信件"),
    ("note", "字条"),
    ("page", "纸页"),
    ("receipt", "收据"),
    ("badge", "证件"),
    ("key", "钥匙"),
    ("banana", "香蕉皮"),
    ("peel", "果皮"),
    ("soot", "烟灰"),
    ("ash", "灰烬"),
    ("burn", "烧痕"),
    ("blood", "血迹"),
    ("branch", "断枝"),
    ("bone", "骨头"),
    ("cloth", "布片"),
    ("coin", "钱币"),
)

# 通用线索词表（用于把未知类型 / 英文描述提炼成中文线索名）。
# 只做"关键词 -> 中文名词"的映射，不改变事实。
EVIDENCE_NOUNS = (
    ("testimony", "证词"), ("witness", "证词"), ("claim", "说法"), ("statement", "说法"),
    ("rumor", "传闻"), ("rumour", "传闻"),
    ("ledger", "账本"), ("account book", "账本"), ("account", "账目"), ("bookkeeping", "账目"),
    ("copy", "复印件"), ("page", "纸页"), ("note", "字条"), ("letter", "信件"),
    ("document", "文件"), ("record", "记录"), ("file", "档案"), ("receipt", "收据"),
    ("list", "名单"), ("report", "报告"), ("map", "地图"), ("photo", "照片"),
    ("picture", "图像"), ("drawing", "图画"),
    ("footprint", "脚印"), ("footprints", "脚印"), ("track", "痕迹"), ("trace", "痕迹"),
    ("print", "印记"), ("mark", "痕迹"),
    ("lock", "锁"), ("key", "钥匙"), ("weapon", "凶器"), ("knife", "刀"),
    ("coin", "钱币"), ("money", "钱款"), ("fund", "公款"), ("gold", "金子"),
    ("seal", "印章"), ("stamp", "印章"), ("badge", "证件"), ("token", "信物"),
    ("cloth", "布片"), ("fabric", "布料"), ("rag", "破布"), ("rope", "绳子"),
    ("bottle", "瓶子"), ("box", "盒子"), ("bundle", "包裹"), ("bag", "口袋"),
    ("bone", "骨头"), ("body", "尸体"), ("grave", "坟墓"),
    ("fire", "火情"), ("ash", "灰烬"), ("soot", "烟灰"), ("blood", "血迹"),
    ("herb", "草药"), ("medicine", "药"), ("bread", "食物"), ("oil", "油"),
    ("mill", "磨坊"), ("forest", "森林"), ("village", "村庄"), ("office", "办公室"),
    ("river", "河"), ("road", "道路"), ("well", "水井"), ("farm", "农田"),
)

EVIDENCE_MODIFIERS = (
    ("scribbled", "潦草的"), ("torn", "撕破的"), ("burnt", "烧焦的"), ("burned", "烧焦的"),
    ("water-stained", "被水浸的"), ("stained", "有污渍的"), ("bloody", "带血的"),
    ("broken", "破损的"), ("hidden", "隐藏的"), ("secret", "隐秘的"), ("old", "陈旧的"),
    ("false", "伪造的"), ("fake", "伪造的"), ("forged", "伪造的"), ("stolen", "被偷的"),
    ("crumpled", "揉皱的"), ("faded", "褪色的"), ("sealed", "封口的"), ("unsigned", "无署名的"),
)


def evidence_type_label(evidence_type):
    """线索类型的中文名；未知类型给出通用说法，绝不直接显示内部字符串。"""
    key = str(evidence_type or "").strip().lower()
    if not key:
        return "未分类线索"
    if key in EVIDENCE_NAMES:
        return EVIDENCE_NAMES[key]
    for hint, name in EVIDENCE_NAMES.items():
        if hint and hint in key:
            return name
    for hint, name in EVIDENCE_KEYWORDS:
        if hint in key:
            return name
    for hint, name in EVIDENCE_NOUNS:
        if hint in key:
            return name
    return "未分类线索"


def _extract_from_description(description):
    """从英文描述里提炼中文关键词（只做词表映射，不改写事实）。"""
    text = str(description or "").lower()
    noun = ""
    for hint, name in EVIDENCE_NOUNS:
        if hint in text:
            noun = name
            break
    modifier = ""
    for hint, name in EVIDENCE_MODIFIERS:
        if hint in text:
            modifier = name
            break
    if modifier and noun:
        return f"{modifier}{noun}"
    if noun:
        return noun
    return ""


def description_summary(description):
    """把真实描述整理成中文摘要（中文直接用，英文走关键词词表，无法识别返回空）。"""
    description = str(description or "").strip()
    if not description:
        return ""
    if _has_cjk(description):
        cleaned = description.rstrip("。").replace("\n", " ")
        return cleaned[:40] + ("…" if len(cleaned) > 40 else "")
    return _extract_from_description(description)


def evidence_name(entry, place=None):
    """线索名（尽量有区分度，全部来自真实字段，不编造）。

    顺序：
      1) 已知类型 / 类型关键词
      2) 未知类型但能从词表得到名词 -> 再用描述里的修饰词补充（如"潦草的说法"）
      3) 中文描述摘要
      4) 英文描述关键词提炼
      5) 实在没有信息 -> "来源不明的线索"，不假装成"某地的线索"
    """
    entry = entry or {}
    key = str(entry.get("type", "") or "").strip().lower()
    description = str(entry.get("description", "") or "").strip()

    # 1) 已知类型
    if key in EVIDENCE_NAMES:
        return EVIDENCE_NAMES[key]
    for hint, name in EVIDENCE_NAMES.items():
        if hint and hint in key:
            return name
    for hint, name in EVIDENCE_KEYWORDS:
        if hint in key:
            return name

    # 2) 未知类型：先从类型得到名词，再尝试用描述里的修饰词补足区分度
    noun = ""
    for hint, name in EVIDENCE_NOUNS:
        if hint in key:
            noun = name
            break
    modifier = ""
    for hint, name in EVIDENCE_MODIFIERS:
        if hint in description.lower():
            modifier = name
            break
    if noun and modifier:
        return f"{modifier}{noun}"
    if noun:
        # 描述里也许还有更具体的信息
        if description and _has_cjk(description):
            cleaned = description.rstrip("。").replace("\n", " ")
            return cleaned[:18] + ("…" if len(cleaned) > 18 else "")
        return noun

    # 3) 中文描述
    if description and _has_cjk(description):
        cleaned = description.rstrip("。").replace("\n", " ")
        return cleaned[:18] + ("…" if len(cleaned) > 18 else "")

    # 4) 英文描述关键词
    extracted = _extract_from_description(description)
    if extracted:
        return extracted

    # 5) 诚实退化
    if place:
        return f"来源不明的线索（{place}）"
    return "来源不明的线索"


def goal_text(goal, *, npc_name=None, place=None, item=None, evidence=None):
    """把内部 goal 字符串翻译成玩家能看懂的一句中文。"""
    raw = str(goal or "").strip()
    lowered = raw.lower()

    def target_of(prefix):
        if lowered.startswith(prefix + ":"):
            return raw.split(":", 1)[1].strip()
        return None

    if lowered in {"follow player", "follow_npc:player"}:
        return "正在跟着玩家"
    value = target_of("follow_npc")
    if value:
        return f"正在跟随 {npc_name(value)}"
    value = target_of("lead_player_to")
    if value:
        return f"正在带路去{place(value)}"
    value = target_of("reach_location") or (raw[6:].strip() if lowered.startswith("reach ") else None)
    if value:
        return f"正在前往{place(value)}"
    value = target_of("talk_to")
    if value:
        return f"正在找 {npc_name(value)} 说话"
    value = target_of("help_npc") or (raw[5:].strip() if lowered.startswith("help ") else None)
    if value:
        return f"正在帮 {npc_name(value)}"
    value = target_of("find_npc")
    if value:
        return f"正在寻找 {npc_name(value)}"
    value = target_of("investigate")
    if value:
        return f"正在调查{evidence(value)}"
    value = target_of("find_item") or (raw[5:].strip() if lowered.startswith("find ") else None)
    if value:
        return f"正在寻找{item(value)}"
    if lowered in {"escape", "escape:"}:
        return "正在离开危险的地方"
    if lowered in {"work", "work:"}:
        return "正在干活"
    if lowered in {"wait", "wait:"}:
        return "正在原地等待"
    return "正在处理自己的事"


def belief_text(key, value=None):
    """把真实 belief 键翻译成中文推测句；未知键不猜。"""
    key = str(key or "").strip()
    label = BELIEF_NAMES.get(key)
    if label is None:
        for hint, name in BELIEF_NAMES.items():
            if hint and hint in key:
                label = name
                break
    if label is None:
        return "对身边的人和事有了新的判断"
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = None
    if confidence is None:
        return label
    if confidence >= 0.75:
        strength = "很确信"
    elif confidence >= 0.5:
        strength = "有些怀疑"
    else:
        strength = "只是隐约觉得"
    return f"{strength}：{label}"


# 玩家 / NPC 动作历史 -> 中文（只用真实记录，不补充因果）
HISTORY_TEXTS = {
    "player_created_rumor": "散播了一条传闻",
    "player_created_evidence": "留下了一条线索",
    "player_faked_trace": "伪造了一处痕迹",
    "player_talked_to_npc": "和{npc}说了话",
    "player_gave_item": "把一件物品交给了{npc}",
    "player_hid_item": "把一件物品藏了起来",
    "player_took_item": "拿走了一件物品",
    "player_dropped_item": "丢下了一件物品",
    "player_rescued_npc": "救下了{npc}",
    "player_attacked_npc": "对{npc}动了手",
    "player_knocked_out_npc": "打晕了{npc}",
    "player_trained": "锻炼了身体",
    "player_worked": "干了一段时间的活",
    "player_slept": "睡了一觉",
    "player_bought_item": "买了一件东西",
    "player_explored": "探索了{place}",
    "player_found_item": "发现了一件东西",
    "player_met_npc": "遇见了{npc}",
    "player_helped_npc": "帮了{npc}",
    "player_ignored_npc": "没有理会{npc}",
    "player_made_claim": "提出了一个说法",
    "player_started_quest": "接下了一件委托",
    "player_failed_quest": "没能完成委托",
    "npc_talked": "和{npc}说了话",
    "npc_investigated_evidence": "调查了一条线索",
    "npc_action_rejected": "有一次行动没有成功",
    "rumor_spread": "一个传闻在村里传开了",
    "night_search": "在夜里搜查过",
    "weather_changed": "天气发生了变化",
}


def effect_text(effect, npc_name=None, place_of_xy=None, item=None, evidence=None):
    """把一条 Event DSL effect 翻成"AI 编导做了什么"的一句话。"""
    effect = effect or {}
    kind = str(effect.get("type") or "")

    def who(value):
        if not value:
            return "某个村民"
        resolver = npc_name or (lambda v: None)
        return resolver(value) or "某个村民"

    def where(value):
        return (place_of_xy or (lambda v: "某处"))(value)

    def thing(value):
        return (item or (lambda v: "一件物品"))(value)

    def clue(value):
        return (evidence or (lambda v: "线索"))(value)

    if kind == "add_goal":
        return f"给 {who(effect.get('npc_id'))} 安排了新目标：{goal_text(effect.get('goal'), npc_name=npc_name, place=lambda v: '某个地方', item=thing, evidence=clue)}"
    if kind == "remove_goal":
        return f"让 {who(effect.get('npc_id'))} 放弃原来的打算"
    if kind == "set_belief":
        return f"让 {who(effect.get('npc_id'))} 开始认为：{belief_text(effect.get('key'), effect.get('value'))}"
    if kind == "add_memory":
        return f"让 {who(effect.get('npc_id'))} 记住了一件事"
    if kind == "change_relationship":
        return f"调整了 {who(effect.get('npc_id'))} 对 {who(effect.get('target_id'))} 的态度"
    if kind == "move_npc":
        return f"把 {who(effect.get('npc_id'))} 移动到{where((effect.get('x'), effect.get('y')))}"
    if kind == "spawn_item":
        return f"让{thing(effect.get('item_type') or effect.get('item_id'))}出现在{where((effect.get('x'), effect.get('y')))}"
    if kind == "add_evidence":
        return f"在{where((effect.get('x'), effect.get('y')))}留下了一条线索《{clue(effect.get('evidence_type') or effect.get('evidence_id'))}》"
    if kind == "dialogue":
        return f"{who(effect.get('speaker_id'))}说了一句话"
    if kind == "set_follow_target":
        target = effect.get("follow_target")
        return f"{who(effect.get('npc_id'))}开始跟随{who(target) if target else '自由行动'}"
    if kind == "create_npc":
        return f"引入了一个新角色：{effect.get('name') or '新角色'}"
    if kind in {"set_character_state", "change_character_state"}:
        return f"改变了 {who(effect.get('npc_id'))} 的状态"
    if kind == "fail_quest":
        return "让一件委托失败了"
    return ""

PLAYER_ACTION_TEXTS = {
    "move": "移动了位置",
    "talk": "和村民说了话",
    "create_rumor": "散播了一条传闻",
    "create_evidence": "留下了一条线索",
    "give_item": "把物品交给了别人",
    "hide_item": "把物品藏了起来",
    "fake_trace": "伪造了痕迹",
    "search_object": "搜查了一个物件",
    "inspect_object": "查看了一个物件",
    "break_object": "砸开了上锁的物件",
    "open_drawer": "打开了抽屉",
    "take_object": "拿走了一个物件",
    "take_ledger": "拿走了账本",
    "follow_object": "循着痕迹追踪",
    "follow_npc": "让某人跟着自己",
    "protect_ren": "保护了一个村民",
    "wait": "等了一段时间",
    "work": "干了一段时间的活",
    "sleep": "睡了一觉",
    "explore": "探索了一个地点",
    "buy": "买了一件东西",
}


def history_text(action, target=None, content=None, npc_names=None, places=None):
    """把历史记录翻译成中文；翻不出具体含义时按动作类别给泛化说法。"""
    action = str(action or "")
    npc_names = npc_names or {}
    places = places or {}
    template = HISTORY_TEXTS.get(action)
    if template is None:
        return ""
    npc_name = npc_names.get(str(target), "村里的人") if target else "村里的人"
    place = places.get(str(target), "某处") if target else "某处"
    try:
        return template.format(npc=npc_name, place=place)
    except Exception:
        return template


REASON_TEXTS = {
    "conditions satisfied": "触发条件满足，按计划发生",
    "cancel condition met": "取消条件满足，这件事没有发生",
    "dialogue session": "玩家正在对话，暂缓处理",
    "execution failed": "执行失败",
}


def reason_text(reason):
    reason = str(reason or "").strip()
    if not reason:
        return ""
    if reason in REASON_TEXTS:
        return REASON_TEXTS[reason]
    if reason.startswith("condition not met"):
        return "触发条件还没有满足"
    if reason.startswith("execution failed"):
        return "执行失败"
    if "condition" in reason:
        return "条件相关，细节未提供"
    return ""


def memory_text(record):
    """记忆条目的中文摘要（只读取真实记录，不生成新事实）。"""
    record = record or {}
    content = str(record.get("content", "") or "").strip()
    if content and _has_cjk(content):
        cleaned = sanitize_visible(content)
        cjk = sum(1 for ch in cleaned if "\u4e00" <= ch <= "\u9fff")
        ascii_letters = sum(1 for ch in cleaned if ch.isascii() and ch.isalpha())
        if cjk >= 4 and ascii_letters <= cjk * 2:
            return cleaned
        content = ""  # 英文原文：不直接展示，按来源给出中文归类
    if content:
        # 引擎 / LLM 写下的英文记录：只在能对应到已知模式时改写，否则按来源归类。
        lowered = content.lower()
        if lowered.startswith("found ") and "object" in lowered:
            import re as _re
            match = _re.search(r"(\d+)", content)
            count = int(match.group(1)) if match else 0
            if count > 0:
                return f"搜查了一遍周围，找到 {count} 个物件。"
            return "搜查了一遍周围，什么也没找到。"
        if "moved toward" in lowered or lowered.startswith("moved"):
            return "朝目标移动了一段路。"
        if "reached the target" in lowered:
            return "到达了目的地。"
        if "took " in lowered:
            return "拿走了一件东西。"
        source = str(record.get("source", "") or "")
        return {
            "evidence": "记下了一条线索。",
            "planner": "记下了刚才那次行动的结果。",
            "event": "经历了一件事。",
            "npc_action": "和其他人说过话。",
            "rumor": "听到了一个传闻。",
            "observation": "观察到了周围的情况。",
        }.get(source, "记住了一件事。")
    observed = record.get("observed")
    if isinstance(observed, list) and observed:
        kinds = {str(item.get("type")) for item in observed if isinstance(item, dict)}
        if "player" in kinds:
            return "看见了玩家。"
        if "npc" in kinds:
            return "看见了其他村民。"
        if "item" in kinds:
            return "看见了物品。"
    return ""


def relationship_text(name, value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return f"与 {name} 的关系发生了变化"
    if value >= 0.75:
        tone = "非常信任"
    elif value >= 0.55:
        tone = "比较信任"
    elif value >= 0.4:
        tone = "保持中立"
    elif value >= 0.25:
        tone = "有些戒备"
    else:
        tone = "非常不信任"
    return f"对 {name} {tone}"










