from typing import List, Tuple
from ..config import settings
from typing import List, Tuple, Dict
import logging
logger = logging.getLogger(__name__)
from ..domain.models import LevelState, IntervalState

ALLOWED_COMBINATIONS = [
    ("4h", "1h"),
    ("4h", "1h", "15m"),
    ("4h", "15m"),

    ("1h", "15m"),
    ("1h", "15m", "3m"),
    ("1h", "3m"),

    ("15m", "3m"),
    
    ("1D", "4h", "1h"),
    ("1D", "4h"),
    ("1D", "1h"),
]

COMBINATION_ROUTING = {
    ("1D", "4h", "1h"): settings.TG_TOPIC_LONG,
    ("1D", "4h"): settings.TG_TOPIC_LONG,
    ("1D", "1h"): settings.TG_TOPIC_LONG,

    ("4h", "1h"): settings.TG_TOPIC_MID,
    ("4h", "1h", "15m"): settings.TG_TOPIC_MID,
    ("4h", "15m"): settings.TG_TOPIC_MID,

    ("1h", "15m"): settings.TG_TOPIC_SHORT,
    ("1h", "15m", "3m"): settings.TG_TOPIC_SHORT,

    ("15m", "3m"): settings.TG_TOPIC_ULTRA,
    
}

def canonical_combo(combo: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple(sorted(combo, 
                        key=lambda iv: settings.INTERVAL_ORDER.index(iv),
                        reverse=True))

def get_max_interval(combo: Tuple[str, ...]) -> str:
    """
    根据 settings.INTERVAL_ORDER 计算组合中的最大周期
    """
    return max(
        combo,
        key=lambda iv: settings.INTERVAL_ORDER.index(iv)
    )

def match_combinations(
    raw_intervals: List[str],
    pushed: set[tuple],
) -> List[Tuple[tuple, bool]]:
    """
    - result[combo, 是否推送过]： False 首次出现的组合，之前没推送过
        True：升级推送的组合，之前推送过其自己，现在是升级组合。
    """
    result = []
    for combo in ALLOWED_COMBINATIONS:
        if all(iv in raw_intervals for iv in combo):
            if combo not in pushed:
                result.append((combo, False))
            # 升级组合
            elif any(set(prev).issubset(set(combo)) and len(combo) > len(prev) for prev in pushed):
                result.append((combo, True))
    return result


def is_upgrade(combo: Tuple[str, ...], pushed: Dict[Tuple[str, ...], Dict]) -> bool:
    return any(set(prev).issubset(set(combo)) and len(combo) > len(prev) for prev in pushed)


def match_combinations_with_lifecycle(
    raw_intervals: List[str],
    states: Dict[str, IntervalState],  # 当前 symbol 的周期状态集合
    pushed_combos: Dict[Tuple[str, ...], Dict],  # 组合 → {"active": bool, "max_iv": str, ...}
) -> List[Tuple[Tuple[str, ...], bool]]:
    """
    识别当前满足条件的组合，支持以下逻辑：
    - 首次命中组合 → 推送
    - 已推送组合，如果最大周期 max_iv 已 OUT → 标记为可再次推送
    - 新组合是旧组合的升级 → 允许推送升级标记
    """
    from .resonance_combinations import ALLOWED_COMBINATIONS

    result = []
    current_set = set(raw_intervals)

    for combo in ALLOWED_COMBINATIONS:
        if all(iv in current_set for iv in combo):
            max_iv = get_max_interval(combo) # 根据配置文件中的周期顺序判断当前组合的最大周期
            canon = canonical_combo(combo)
            combo_status = pushed_combos.get(canon)


            # 情况 1：首次命中
            if combo_status is None:
                logger.info(f"首次命中组合：{canon}")
                result.append((canon, False))

            # 情况 2：已推送但 max_iv 已退场，允许重推
            elif combo_status.get("active") and states[max_iv].state == LevelState.OUT:
                result.append((canon, False))
                combo_status["active"] = False  # 标记为非活跃，下次满足才再进 active

            # 情况 3：升级组合
            elif is_upgrade(canon, pushed_combos):
                result.append((canon, True))

    return result