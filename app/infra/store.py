from __future__ import annotations

import time
from typing import Dict, Tuple, Optional
from dataclasses import dataclass
from typing import Any, Dict, Tuple, List, Optional
from collections import defaultdict

from ..domain.models import Side


# =========================
# 每个 interval 的缓存结构
# =========================
@dataclass
class IntervalCache:
    value: float                            # 最新值
    updated_ts: float                       # 最新更新时间戳

    in_oversold: bool = False               # 当前是否在超卖区（用于判断是否刚刚退出）
    in_overbought: bool = False             # 当前是否在超买区

    last_exit_ts_oversold: Optional[float] = None  # 最近一次离开超卖的时间
    last_exit_ts_overbought: Optional[float] = None  # 最近一次离开超买的时间


# =========================
# 推送门控记录
# =========================
@dataclass
class GateRecord:
    last_in_count: int = 0
    last_sent_ts: float = 0.0


# =========================
# AppState 主体
# =========================
class AppState:
    """
    全局运行状态（timestamp-based warm 版本）

    职责：
    - 追踪每个周期是否处于 IN / WARM / OUT
    - 提供事件驱动式的推送门控
    """

    def __init__(
        self,
        cooldown_seconds: int,
        warm_k_map: Dict[str, int],
        interval_seconds: Dict[str, int],
    ):
        self.cooldown_seconds = float(cooldown_seconds)
        self.warm_k_map = warm_k_map  # 每个周期允许几根K线 warm
        self.interval_seconds = interval_seconds  # 每个周期一根K线多少秒

        self.cache: Dict[Tuple[str, str], IntervalCache] = {}
        self.gate: Dict[Tuple[str, str], GateRecord] = {}

        self.latest_combo_state: Dict[
            Tuple[str, Side],
            Dict[Tuple[str, ...], Dict[str, Any]]
        ] = defaultdict(dict)
        """
        self.latest_combo_state[(symbol, side)][("4h", "1h")] = {
            "active": True,
            "last_pushed_ts": 1700001234.0,
            "max_iv": "4h",
        }
        """

    # =========================================================
    # 更新某个周期的状态，同时记录“是否刚离开 IN”
    # =========================================================
    def update_interval(
        self,
        symbol: str,
        interval: str,
        value: float,
        ob_level: float,
        os_level: float,
        now_ts: Optional[float] = None,
    ) -> None:
        """
        主要职责：
        - 更新最新值
        - 判断是否刚刚离开 IN，若是，则记录 exit_ts
        """
        if now_ts is None:
            now_ts = time.time()

        key = (symbol, interval)
        rec = self.cache.get(key)

        if rec is None:
            rec = IntervalCache(value=value, updated_ts=now_ts)
            self.cache[key] = rec
        else:
            rec.value = value
            rec.updated_ts = now_ts

        # ========= 超卖处理 =========
        was_in_os = rec.in_oversold
        is_in_os = (value <= os_level)

        # 如果之前在区，现在不在超卖区，则用现在的ts设为退出超卖的ts
        if was_in_os and not is_in_os:
            rec.last_exit_ts_oversold = now_ts

        rec.in_oversold = is_in_os

        # ========= 超买处理 =========
        was_in_ob = rec.in_overbought
        is_in_ob = (value >= ob_level)

        if was_in_ob and not is_in_ob:
            rec.last_exit_ts_overbought = now_ts

        rec.in_overbought = is_in_ob

    # =========================================================
    # 判断 warm 状态（基于时间差）
    # =========================================================
    def is_warm(
        self,
        symbol: str,
        interval: str,
        side: Side,
        now_ts: Optional[float] = None,
    ) -> bool:
        """
        判断某个周期是否仍处于 warm 状态

        判断逻辑：
        - 必须曾进入过 IN
        - 必须记录了退出 IN 的时间
        - 退出时间在 warm 窗口内
        """
        if now_ts is None:
            now_ts = time.time()

        rec = self.cache.get((symbol, interval))
        if rec is None:
            return False

        if side == Side.OVERSOLD:
            exit_ts = rec.last_exit_ts_oversold
        else:
            exit_ts = rec.last_exit_ts_overbought

        if exit_ts is None:
            return False

        candle_sec = self.interval_seconds.get(interval)
        warm_k = self.warm_k_map.get(interval, 2) # 获取这个周期允许 warm 的 K线根数（默认是2根）

        # 防御性代码：如果没有配好周期秒数（极少发生），就直接认为 不在 warm 状态
        if candle_sec is None:
            return False

        return (now_ts - exit_ts) <= warm_k * candle_sec

    # =========================================================
    # 推送门控（与 warm 无关，无需改）
    # =========================================================
    def should_emit_resonance(
        self,
        symbol: str,
        side: Side,
        in_count: int,
        min_resonance: int,
    ) -> bool:
        """
        推送策略：
        - 当前有效 IN 数 >= 门槛
        - 比上一次更多（只推增强）
        - 不在 cooldown 内
        """
        key = (symbol, side.value)
        now_ts = time.time()

        rec = self.gate.get(key)
        if rec is None:
            rec = GateRecord()
            self.gate[key] = rec

        if in_count < min_resonance:
            rec.last_in_count = in_count
            return False

        increased = (in_count > rec.last_in_count)
        in_cooldown = (now_ts - rec.last_sent_ts) < self.cooldown_seconds

        if increased and not in_cooldown:
            rec.last_in_count = in_count
            rec.last_sent_ts = now_ts
            return True

        rec.last_in_count = in_count
        return False
