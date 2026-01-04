from __future__ import annotations

from typing import Dict, List
from .models import IntervalSignal, IntervalState, LevelState, ResonanceSnapshot, Side


def classify_for_side(
    sig: IntervalSignal,
    side: Side,
    ob_level: float,
    os_level: float,
    warm_lookback: int,
) -> LevelState:
    """
    对指定 side（oversold/overbought）判定 IN/WARM/OUT
    - IN: 当前触发
    - WARM: 当前未触发，但最近 warm_lookback 根历史触发
    - OUT: 都不满足
    """
    vals = sig.values
    if not vals:
        return LevelState.OUT

    now = vals[0]
    hist = vals[1 : 1 + warm_lookback]  # 最近N根历史

    if side == Side.OVERSOLD:
        if now <= os_level:
            return LevelState.IN
        if any(v <= os_level for v in hist):
            return LevelState.WARM
        return LevelState.OUT

    # overbought
    if now >= ob_level:
        return LevelState.IN
    if any(v >= ob_level for v in hist):
        return LevelState.WARM
    return LevelState.OUT


def make_signature(side: Side, states: Dict[str, IntervalState]) -> str:
    """
    signature: side|3m:in|15m:out|1h:warm|4h:in
    """
    parts = []
    for iv in sorted(states.keys()):
        parts.append(f"{iv}:{states[iv].state.value}")
    return f"{side.value}|" + "|".join(parts)


def build_snapshot(
    symbol: str,
    ts: float,
    signals: List[IntervalSignal],
    side: Side,
    ob_level: float,
    os_level: float,
    warm_lookback: int,
) -> ResonanceSnapshot:
    states: Dict[str, IntervalState] = {}
    score = 0

    for sig in signals:
        st = classify_for_side(sig, side, ob_level, os_level, warm_lookback)
        iv_state = IntervalState(interval=sig.interval, state=st, value=float(sig.values[0]))
        states[sig.interval] = iv_state
        if st in (LevelState.IN, LevelState.WARM):
            score += 1

    signature = make_signature(side, states)
    return ResonanceSnapshot(
        symbol=symbol,
        side=side,
        ts=ts,
        states=states,
        score=score,
        signature=signature,
    )
