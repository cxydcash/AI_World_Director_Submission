"""语言行动的"约定"登记与兑现跟踪（Speech-act commitments）。

对话里产生的意图不是一句话就结束：NPC 可能立刻去做、稍后再去（甚至忘了）、
或者只是口头答应而根本不打算动身。本模块只负责：

* 把一次"约定"登记下来（目标本身仍由 Event DSL / NPCPlanner 执行）
* 逐 tick 观察这个约定有没有被真正兑现
* 产生**玩家可见的真实提示**（谁等了一会儿先走了、谁答应过却没动身…）

它不修改任何模拟规则，也不替 NPC 编造行动；只根据真实 WorldState 判断状态。
"""

import threading

from event import execute_event

WAIT_TICKS = 5        # NPC 先走，最多等玩家 5 个 tick
WAIT_RADIUS = 6.0     # 玩家进入这个范围算"跟上了"
GRACE_TICKS = 2       # 目标被清掉后，给 2 个 tick 观察是否重新出现

STATUS_AGREED = "agreed"
STATUS_ACTING = "acting"
STATUS_WAITING = "waiting_player"
STATUS_KEPT = "kept"
STATUS_ABANDONED = "abandoned"
STATUS_FORGOTTEN = "forgotten"
STATUS_DECEIVED = "deceived"
STATUS_CANCELLED = "cancelled"
STATUS_NO_ACTION = "no_action"

TERMINAL = {STATUS_KEPT, STATUS_ABANDONED, STATUS_FORGOTTEN, STATUS_DECEIVED, STATUS_CANCELLED, STATUS_NO_ACTION}

STATUS_LABELS = {
    STATUS_AGREED: "刚说定",
    STATUS_ACTING: "正在兑现",
    STATUS_WAITING: "在等你",
    STATUS_KEPT: "已经一起走了",
    STATUS_ABANDONED: "不等了 / 自己走了",
    STATUS_FORGOTTEN: "说过要去，但一直没去",
    STATUS_DECEIVED: "只是嘴上答应，没有动身",
    STATUS_CANCELLED: "已经取消",
    STATUS_NO_ACTION: "说过要去，但没有动身",
}


def _action_phrase(goal_type, target):
    """通用中文说法（不含内部 ID）；更具体的说法由玩家视图层负责。"""
    if goal_type == "lead_player_to":
        return "带你走一趟"
    if goal_type == "follow_player":
        return "跟着你走"
    if goal_type == "go_to":
        return "去一个地方"
    if goal_type == "talk_to":
        return "去找人说话"
    if goal_type == "investigate":
        return "去调查"
    if goal_type == "wait":
        return "在原地等"
    return "去做一件事"


class CommitmentRegistry:
    """登记对话里产生的约定，并跟踪它有没有被真正兑现。"""

    def __init__(self, wait_ticks=WAIT_TICKS, wait_radius=WAIT_RADIUS, goal_router=None):
        self.wait_ticks = int(wait_ticks)
        self.wait_radius = float(wait_radius)
        # Step 12.2: commitments track promises but never decide goals. When a
        # promise needs a goal change, this router sends a DirectorInput to the
        # AI Director and GoalManager writes the result.
        self.goal_router = goal_router
        self._lock = threading.RLock()
        self.items = []
        self.notices = []

    def reset(self):
        with self._lock:
            self.items = []
            self.notices = []

    # -- 登记 ---------------------------------------------------------
    def register(self, npc_id, npc_name, goal, goal_string, commitment, tick_index, reason="", place_name=None):
        """登记一次约定。goal_string 为空表示"没有落地任何目标"（骗玩家/拒绝）。

        幂等：同一个 NPC 对同一个**仍然有效**（非终态）的目标只保留一条约定；
        已经结束（kept / abandoned / forgotten / deceived / cancelled / no_action）
        的旧约定不会阻止之后产生的新约定。
        """
        goal = goal or {}
        with self._lock:
            key = self._commitment_key(
                npc_id, goal_string, goal.get("goal_type"), goal.get("target"), commitment
            )
            for existing in self.items:
                if existing.get("status") in TERMINAL:
                    continue
                if self._commitment_key(
                    existing.get("npc_id"),
                    existing.get("goal_string"),
                    existing.get("goal_type"),
                    existing.get("target"),
                    existing.get("commitment"),
                ) == key:
                    # The same still-valid promise is already registered.
                    return existing
            record = {
                "npc_id": npc_id,
                "npc_name": npc_name,
                "goal_type": goal.get("goal_type"),
                "target": goal.get("target"),
                "goal_string": goal_string or "",
                "commitment": commitment or "immediate",
                "status": STATUS_AGREED,
                "created_tick": int(tick_index),
                "wait_started_tick": None,
                "reason": str(reason or ""),
                "action": _action_phrase(goal.get("goal_type"), place_name or goal.get("target")),
                "notice": "",
            }
            self.items.append(record)
            self.items = self.items[-60:]
            return record

    @staticmethod
    def _commitment_key(npc_id, goal_string, goal_type, target, commitment):
        """Identity of one promise: the NPC plus the actual goal.

        When a concrete goal exists the goal string is the identity (a repeated
        request with a different commitment verb is still the same promise).
        No-goal promises (deceive / decline) fall back to type + target + verb.
        """
        goal_string = str(goal_string or "").strip()
        if goal_string:
            return (str(npc_id or ""), goal_string)
        return (
            str(npc_id or ""),
            "",
            str(goal_type or ""),
            str(target or ""),
            str(commitment or ""),
        )

    def cancel_for_npc(self, npc_id, reason="", by_player=True):
        """玩家说"不用跟着我了"之类的取消。"""
        with self._lock:
            for record in self.items:
                if record["npc_id"] != npc_id or record["status"] in TERMINAL:
                    continue
                record["status"] = STATUS_CANCELLED
                record["reason"] = str(reason or "")
                record["notice"] = "你让TA不用再跟着了。" if by_player else ""

    # -- 兑现跟踪 -----------------------------------------------------
    def tick(self, world, tick_index):
        """每个 tick 观察一次：约定有没有被真正执行。"""
        with self._lock:
            records = list(self.items)
        for record in records:
            if record["status"] in TERMINAL:
                continue
            try:
                self._advance(world, record, int(tick_index))
            except Exception:
                continue

    def _advance(self, world, record, tick_index):
        npc = world.get_npc(record["npc_id"])
        if npc is None:
            record["status"] = STATUS_ABANDONED
            return
        goals = list(getattr(npc, "goals", []) or [])
        has_goal = bool(record["goal_string"]) and record["goal_string"] in goals
        elapsed = tick_index - record["created_tick"]
        name = record["npc_name"]

        if record["commitment"] == "decline":
            record["status"] = STATUS_CANCELLED
            return

        if record["commitment"] == "deceive":
            # 只是口头答应，没有落地任何目标
            if elapsed >= self.wait_ticks:
                record["status"] = STATUS_DECEIVED
                self._notify(f"{name} 只是嘴上答应要{record['action']}，但一直没有动身。")
            return

        if not record["goal_string"]:
            # 没有落地目标：稍后兑现（可能忘）或者干脆没去做
            if record["commitment"] == "later":
                if elapsed >= self.wait_ticks * 2:
                    record["status"] = STATUS_FORGOTTEN
                    self._notify(f"{name} 说过要{record['action']}，但一直没去。")
            elif elapsed >= self.wait_ticks:
                record["status"] = STATUS_NO_ACTION
                self._notify(f"{name} 答应要{record['action']}，但没有去做。")
            return

        if record["goal_type"] == "lead_player_to":
            target = self._location_of(world, record)
            if target is not None:
                distance = world.distance_between(world.npc_position(npc.id), target)
                if distance <= 1.5:
                    if record["status"] != STATUS_WAITING:
                        record["status"] = STATUS_WAITING
                        record["wait_started_tick"] = tick_index
                        # 真的停下来等：写一个 wait 目标，Planner 会留在原地
                        self._add_wait_goal(world, npc)
                        self._notify(f"{name} 先到了，正在等玩家过去。")
                    player_distance = world.distance_between(
                        (world.player.x, world.player.y), world.npc_position(npc.id)
                    )
                    if player_distance <= self.wait_radius:
                        record["status"] = STATUS_KEPT
                        self._remove_wait_goal(world, npc)
                        self._notify(f"{name} 等到了玩家，两个人一起行动。")
                        return
                    started = record.get("wait_started_tick")
                    if started is None:          # 注意：0 是合法 tick，不能用 or
                        started = tick_index
                    waited = tick_index - started
                    if waited >= self.wait_ticks:
                        record["status"] = STATUS_ABANDONED
                        self._remove_wait_goal(world, npc)
                        self._notify(f"{name} 等了玩家一会儿（{waited} 个时间刻）没等到，先走了。")
                        self._drop_goal(world, npc, record)
                        return
                    return  # 还在等，保持 waiting 状态，不要被后面的通用逻辑覆盖
                else:
                    record["status"] = STATUS_ACTING
                    if not has_goal and elapsed >= GRACE_TICKS:
                        record["status"] = STATUS_ABANDONED
                        self._notify(f"{name} 本来要{record['action']}，但没有继续动身。")
                    return
            else:
                # 目标地点在世界里没有对应坐标：NPC 不会动身
                if elapsed >= self.wait_ticks:
                    record["status"] = STATUS_NO_ACTION
                    self._notify(f"{name} 说过要{record['action']}，但一直没有动身。")
                return

        if record["commitment"] == "later" and not has_goal:
            if elapsed >= self.wait_ticks * 2:
                record["status"] = STATUS_FORGOTTEN
                self._notify(f"{name} 说过要{record['action']}，但一直没去。")
            return

        if record["goal_type"] == "follow_player":
            if has_goal:
                record["status"] = STATUS_ACTING
                return
            # 目标不在了：NPC 主动结束跟随（可能告知，也可能悄悄离开）
            others = [g for g in goals if g != record["goal_string"]]
            if others:
                record["status"] = STATUS_ABANDONED
                self._notify(f"{name} 想起别的事情，不跟着玩家了。")
            else:
                record["status"] = STATUS_ABANDONED
                self._notify(f"{name} 悄悄离开了，不再跟着玩家。")
            return

        # 其它独立目标
        if has_goal:
            record["status"] = STATUS_ACTING
        elif elapsed >= GRACE_TICKS:
            record["status"] = STATUS_NO_ACTION
            self._notify(f"{name} 答应要{record['action']}，但没有去做。")

    def _route_goal_event(self, world, npc, payload):
        """Send commitment goal information to the Director; never decide here."""
        router = getattr(self, "goal_router", None)
        if router is None:
            return None
        try:
            from director_input import DirectorInput, SOURCE_WORLD_EVENT
            director_input = DirectorInput(
                source=SOURCE_WORLD_EVENT,
                actor_id=npc.id,
                target_id=npc.id,
                payload=dict(payload, npc_id=npc.id),
                context={"time": world.time},
                time=world.time,
            )
            return router(director_input)
        except Exception:
            return None

    def _add_wait_goal(self, world, npc):
        """让 NPC 留在原地等：由 Director 决定，GoalManager 写入。"""
        if "wait" in (npc.goals or []):
            return
        self._route_goal_event(world, npc, {"kind": "commitment_wait"})

    def _remove_wait_goal(self, world, npc):
        if "wait" not in (npc.goals or []):
            return
        self._route_goal_event(world, npc, {
            "kind": "commitment_release",
            "goal_string": "wait",
        })

    def _drop_goal(self, world, npc, record):
        goal_string = record.get("goal_string")
        if not goal_string:
            return
        self._route_goal_event(world, npc, {
            "kind": "commitment_release",
            "goal_string": goal_string,
        })

    @staticmethod
    def _location_of(world, record):
        from world import EXPLORE_LOCATIONS, LOCATION_ALIASES

        target = str(record.get("target") or "").strip().lower()
        if not target:
            return None
        target = LOCATION_ALIASES.get(target, target)
        return EXPLORE_LOCATIONS.get(target)

    def _notify(self, text):
        with self._lock:
            self.notices.append({"text": text})
            self.notices = self.notices[-40:]

    # -- 读取（供玩家界面）--------------------------------------------
    def active(self, limit=10):
        with self._lock:
            rows = [dict(item) for item in self.items][-limit:]
        for row in rows:
            row["status_label"] = STATUS_LABELS.get(row["status"], "进行中")
        return rows

    def recent_notices(self, limit=10):
        with self._lock:
            return [dict(item) for item in self.notices[-limit:]][::-1]
