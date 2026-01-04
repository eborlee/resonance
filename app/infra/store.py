from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, Optional
import time

from ..domain.models import Side


@dataclass
class IntervalCache:
    # 最新指标值（例如你示例里的 value）
    value: float
    updated_ts: float

    # 余温计数（仅用于展示，不触发推送）
    warm_ttl_oversold: int = 0
    warm_ttl_overbought: int = 0

    # 上一次是否在IN（用于保证：不会出现 OUT->WARM）
    prev_in_oversold: bool = False
    prev_in_overbought: bool = False


@dataclass
class GateRecord:
    last_in_count: int = 0
    last_sent_ts: float = 0.0


class AppState:
    """
    最小可运行状态：
    1) cache：symbol+interval -> 最新 value + warm余温
    2) gate ：symbol+side     -> 上次IN数量 + 上次推送时间
    """
    def __init__(self, cooldown_seconds: int, warm_lookback: int):
        self.cooldown_seconds = float(cooldown_seconds)
        self.warm_lookback = int(warm_lookback)

        self.cache: Dict[Tuple[str, str], IntervalCache] = {}
        self.gate: Dict[Tuple[str, str], GateRecord] = {}

    def update_interval(self, symbol: str, interval: str, value: float,
                        ob_level: float, os_level: float) -> None:
        """
        更新单个 (symbol, interval) 的最新 value，并维护余温 TTL。
        余温规则（符合你说的“不会有 OUT->WARM”）：
        - 只有 prev_in=True 且 本次不再IN 时，才进入 WARM（ttl=warm_lookback）
        - 如果 prev_in=False，本次不IN，则保持 OUT（ttl不被置起）
        """
        now_ts = time.time()
        key = (symbol, interval)
        rec = self.cache.get(key)
        if rec is None:
            rec = IntervalCache(value=float(value), updated_ts=now_ts)
            self.cache[key] = rec
        else:
            rec.value = float(value)
            rec.updated_ts = now_ts

        # oversold 判定
        is_in_os = (value <= os_level)
        if is_in_os:
            rec.prev_in_oversold = True
            rec.warm_ttl_oversold = 0
        else:
            if rec.prev_in_oversold:
                # 刚从IN退出 -> 进入余温
                rec.warm_ttl_oversold = self.warm_lookback if rec.warm_ttl_oversold == 0 else rec.warm_ttl_oversold
                rec.prev_in_oversold = False
            else:
                # 从未IN过，不允许 OUT->WARM
                rec.warm_ttl_oversold = 0

        # overbought 判定
        is_in_ob = (value >= ob_level)
        if is_in_ob:
            rec.prev_in_overbought = True
            rec.warm_ttl_overbought = 0
        else:
            if rec.prev_in_overbought:
                rec.warm_ttl_overbought = self.warm_lookback if rec.warm_ttl_overbought == 0 else rec.warm_ttl_overbought
                rec.prev_in_overbought = False
            else:
                rec.warm_ttl_overbought = 0

    def tick_warm_ttl(self, symbol: str, interval: str, side: Side) -> None:
        """
        每次该 interval 收到新事件、且当前不在IN时，余温 TTL 递减（用于展示）。
        这是“事件驱动”的近似：足够最小可运行。
        """
        rec = self.cache.get((symbol, interval))
        if rec is None:
            return
        if side == Side.OVERSOLD and rec.warm_ttl_oversold > 0:
            rec.warm_ttl_oversold -= 1
        if side == Side.OVERBOUGHT and rec.warm_ttl_overbought > 0:
            rec.warm_ttl_overbought -= 1

    def should_emit_resonance(self, symbol: str, side: Side, in_count: int,
                             min_resonance: int) -> bool:
        """
        推送门控（你要的最简单版本）：
        - 仅当 in_count >= min_resonance 且 in_count 相比上次增加 才推
        - IN->WARM / 降级 不推
        - cooldown 仅用于防抖（可保留）
        """
        key = (symbol, side.value)
        now_ts = time.time()
        rec = self.gate.get(key)
        if rec is None:
            rec = GateRecord()
            self.gate[key] = rec

        # 未达到共振门槛：更新 last_in_count 但不推
        if in_count < min_resonance:
            rec.last_in_count = in_count
            return False

        increased = (in_count > rec.last_in_count)
        in_cooldown = (now_ts - rec.last_sent_ts) < self.cooldown_seconds

        if increased and not in_cooldown:
            rec.last_in_count = in_count
            rec.last_sent_ts = now_ts
            return True

        # 即使没推，也要更新 last_in_count，保证下一次“增加”能正确判断
        rec.last_in_count = in_count
        return False
