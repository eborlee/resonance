from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Side(str, Enum):
    OVERBOUGHT = "overbought"
    OVERSOLD = "oversold"

    @property
    def display(self) -> str:
        return {
            Side.OVERBOUGHT: "超买🔴",
            Side.OVERSOLD: "超卖🟢",
        }[self]


class LevelState(str, Enum):
    IN = "in"
    WARM = "warm"
    OUT = "out"


@dataclass(frozen=True)
class IntervalSignal:
    interval: str
    values: Tuple[float, ...]  # values[0]为当前，后续为历史


@dataclass(frozen=True)
class TvEvent:
    symbol: str
    ts: float
    signals: List[IntervalSignal]


@dataclass(frozen=True)
class IntervalState:
    interval: str
    state: LevelState
    value: float  # 当前值（values[0]）


@dataclass(frozen=True)
class ResonanceSnapshot:
    symbol: str
    side: Side
    ts: float
    states: Dict[str, IntervalState]  # interval -> state
    score: int
    signature: str


@dataclass(frozen=True)
class ZoneEvent:
    symbol: str
    interval: str   # normalized: "1h", "4h"
    top: float
    bot: float
    role: str       # "R"=阻力 / "S"=支撑
    close: float
    ts: float


@dataclass(frozen=True)
class EmaEvent:
    symbol: str
    interval: str
    period: int      # EMA 周期，如 21 / 55 / 100 / 200
    ema_value: float # EMA 价格
    role: str        # "R"=阻力 / "S"=支撑
    close: float
    ts: float


@dataclass(frozen=True)
class DivergenceEvent:
    symbol: str
    interval: str   # normalized: "1h", "4h"
    ts: float


@dataclass(frozen=True)
class VolatileEvent:
    symbol: str
    interval: str   # normalized: "1h", "4h"
    ts: float


@dataclass
class TrackingWindow:
    symbol: str
    side: Side
    push_ts: float               # 推送时间（窗口起点）
    topic_id: int                # 衰竭信号发到同一 topic
    reply_to_message_id: Optional[int] = None  # 原推送消息 ID，用于 reply
    alerted: bool = False

    def is_expired(self, now_ts: float) -> bool:
        return now_ts > self.push_ts + 2 * 3600
