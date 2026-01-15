from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple


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
