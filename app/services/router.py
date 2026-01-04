from __future__ import annotations

from typing import Dict, List, Optional


# 周期大小排序：rank 越大，周期越大（越慢）
# 你可以按需扩展
INTERVAL_RANK: Dict[str, int] = {
    "30s": 0,
    "1m": 1,
    "3m": 2,
    "5m": 3,
    "15m": 4,
    "30m": 5,
    "45m": 6,
    "1h": 7,
    "2h": 8,
    "4h": 9,
    "6h": 10,
    "8h": 11,
    "12h": 12,
    "1d": 13,
    "1w": 14,
}


def rank_of(iv: str) -> int:
    """
    返回周期 rank，未知周期返回 -1
    """
    return INTERVAL_RANK.get(iv, -1)


def max_interval(intervals_present: List[str]) -> Optional[str]:
    """
    在给定 intervals 中找出“最大周期”（最慢周期）
    """
    best_iv = None
    best_rank = -1
    for iv in intervals_present:
        r = rank_of(iv)
        if r > best_rank:
            best_rank = r
            best_iv = iv
    return best_iv


def apply_min_interval_floor(
    in_intervals: List[str],
    max_iv: str,
    max_interval_min_allowed: Dict[str, str],
) -> List[str]:
    """
    根据 max_iv 对应的最小允许周期 floor，把“更快的周期”从 in_intervals 中过滤掉。

    例：max=1w, floor=1h，则 3m/15m 这类更快周期即使 IN 也不算入共振。
    """
    floor_iv = max_interval_min_allowed.get(max_iv)
    if not floor_iv:
        return in_intervals  # 没配置就不限制

    floor_rank = rank_of(floor_iv)
    if floor_rank < 0:
        return in_intervals  # floor 不认识就不限制

    # 保留 rank >= floor_rank 的周期（即：周期不快于 floor）
    return [iv for iv in in_intervals if rank_of(iv) >= floor_rank]


def choose_topic_by_max_interval(
    intervals_present: List[str],
    max_interval_to_topic: Dict[str, str],  # max_interval -> topic_name
    topic_ids: Dict[str, int],              # topic_name -> topic_id
) -> int:
    """
    根据组合中最大周期选择 topic。

    - intervals_present：本次“有效参与”的周期（建议传过滤后的 IN 周期列表）
    - max_interval_to_topic：例如 {"1w":"long","1d":"mid","4h":"short","1h":"ultra"}
    - topic_ids：例如 {"long":1432,"mid":2001,"short":1325,"ultra":3001}
    """
    iv = max_interval(intervals_present)
    if iv is None:
        # 没有参与周期时不该发生；兜底走 short
        return int(topic_ids.get("short", 0))

    topic_name = max_interval_to_topic.get(iv)
    if topic_name is None:
        # 如果规则没覆盖该最大周期：兜底走 short
        return int(topic_ids.get("short", 0))

    return int(topic_ids.get(topic_name, topic_ids.get("short", 0)))
