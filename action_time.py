"""Centralized action time costs."""

import math


# 玩家移动速度（格/小时）。以"玩家体验"而不是现实时间校准：
#   60 格/小时 => 1 格 = 1 分钟。
#     · 一次短移动（5 格）= 5 分钟
#     · 连续移动 50 格   = 50 分钟
#     · 跨越较远区域（60 格）= 约 1 小时
# 旧值 20.0 会让一次 5 格按键消耗 15 分钟，按住 WASD 几十次就跨越半天。
MOVE_SPEED_PER_HOUR = 60.0


ACTION_TIME_SECONDS = {
    "talk": 10 * 60,
    "observe": 10 * 60,
    "investigate": 10 * 60,
    "pick": 1 * 60,
    "drop": 1 * 60,
    "buy": 1 * 60,
    "give_item": 1 * 60,
    "hide_item": 5 * 60,
    "create_rumor": 10 * 60,
    "create_evidence": 30 * 60,
    "create_evidence_water": 60 * 60,
    "fake_trace": 30 * 60,
    "rescue": 30 * 60,
    "attack": 5 * 60,
    "knock_out": 5 * 60,
    "escape": 10 * 60,
    "train": 30 * 60,
    "explore": 60 * 60,
    "work": 8 * 3600,
    "sleep": 8 * 3600,
}


def move_time_cost(distance):
    """Return seconds for moving a straight-line distance."""
    distance = max(0.0, float(distance))
    return distance / MOVE_SPEED_PER_HOUR * 3600.0


def action_time_cost(action, distance=0.0, water_applied=False):
    """Return the time cost in seconds for a player action."""
    if action == "move":
        return move_time_cost(distance)
    if action == "create_evidence" and water_applied:
        return ACTION_TIME_SECONDS["create_evidence_water"]
    return ACTION_TIME_SECONDS.get(action, 0)