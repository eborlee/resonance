from __future__ import annotations

import hashlib
from typing import List, Dict
from .resonance_combinations import match_combinations_with_lifecycle, COMBINATION_ROUTING

from ..config import settings, universe, routing_rules
from ..domain.models import (
    Side,
    LevelState,
    TvEvent,
    IntervalState,
    ResonanceSnapshot,
)
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient
from .router import (
    choose_topic_by_max_interval,
    max_interval,
    apply_min_interval_floor,
)


def filter_by_universe(event: TvEvent) -> TvEvent | None:
    """
    universe 过滤：
    - 不在 universe 的 symbol 直接丢弃
    - interval 不在允许列表的直接丢弃
    """
    allowed_intervals = universe.get(event.symbol)
    if not allowed_intervals:
        return None

    filtered = [s for s in event.signals if s.interval in allowed_intervals]
    if not filtered:
        return None

    return TvEvent(symbol=event.symbol, ts=event.ts, signals=filtered)


def format_message(snapshot: ResonanceSnapshot, in_intervals: List[str]) -> str:
    """
    推送内容：只展示“有效参与”的 IN 周期（已经过 floor 过滤）
    """
    lines: List[str] = []
    lines.append(f"{snapshot.symbol}  {snapshot.side.value.upper()}  IN={len(in_intervals)}")

    for iv in in_intervals:
        st = snapshot.states.get(iv)
        if st is None:
            continue
        # 这里理论上都是 IN，但保持防御式写法
        if st.state == LevelState.IN:
            lines.append(f"- {iv}: IN ({st.value:.2f})")

    return "\n".join(lines)


class ResonanceService:
    """
    多周期共振主逻辑服务：
    - 接收单周期信号
    - 更新缓存状态
    - 根据已处于 IN 的周期组合判断是否推送
    - 匹配 combo 白名单并判断升级
    """

    def __init__(self, state: AppState, tg: TelegramClient):
        self.state = state
        self.tg = tg

    async def handle_event(self, event: TvEvent):
        # Step 1️⃣：过滤掉不在 universe 中的 symbol / interval
        event2 = filter_by_universe(event)
        if event2 is None or not event2.signals:
            return

        # Step 2️⃣：更新状态缓存（AppState），记录最新值和 IN 状态转换
        # intervals_updated = set()
        for sig in event2.signals:
            interval = sig.interval
            value = float(sig.values[0])
            self.state.update_interval(
                symbol=event2.symbol,
                interval=interval,
                value=value,
                ob_level=settings.OB_LEVEL,
                os_level=settings.OS_LEVEL,
                now_ts=event2.ts,  # 使用事件时间戳记录退出时间
            )
            # intervals_updated.add(interval)

        allowed_intervals = universe.get(event2.symbol, [])
        if not allowed_intervals:
            return

        # Step 3️⃣：每个方向单独处理（超买/超卖）
        for side in (Side.OVERSOLD, Side.OVERBOUGHT):
            states: Dict[str, IntervalState] = {}

            # Step 3.1：构建所有周期的状态字典（IN / WARM / OUT）
            for iv in allowed_intervals:
                rec = self.state.cache.get((event2.symbol, iv))
                if rec is None:
                    st = LevelState.OUT
                    v = 0.0
                else:
                    v = rec.value
                    if rec.in_oversold if side == Side.OVERSOLD else rec.in_overbought:
                        st = LevelState.IN
                    # 此处event2.ts就是推送时间，即k线收盘时间
                    elif self.state.is_warm(event2.symbol, iv, side, now_ts=event2.ts):
                        st = LevelState.WARM
                    else:
                        st = LevelState.OUT
                states[iv] = IntervalState(interval=iv, state=st, value=v)

            # Step 3.2：提取所有处于 IN/WARM 状态的周期
            raw_in_intervals = [
                iv for iv, st in states.items()
                if st.state in (LevelState.IN, LevelState.WARM)
            ]
            if not raw_in_intervals:
                # 没有任何有效 IN，也要更新共振口径（防止“悬空”）
                self.state.should_emit_resonance(
                    symbol=event2.symbol,
                    side=side,
                    in_count=0,
                    min_resonance=settings.MIN_RESONANCE,
                )
                continue

            # # Step 3.3：基于 IN 周期计算最大周期（anchor）
            # max_iv = max(raw_in_intervals, key=lambda x: settings.INTERVAL_ORDER.index(x))

            # # Step 3.4：应用 floor 筛选规则（剔除过短周期）
            # effective_in_intervals = apply_min_interval_floor(
            #     in_intervals=raw_in_intervals,
            #     max_iv=max_iv,
            #     max_interval_min_allowed=settings.MAX_INTERVAL_MIN_ALLOWED,
            # )

            in_count = len(raw_in_intervals)

            # Step 3.5：检查是否满足“共振门槛 + 增强 + cooldown”
            if not self.state.should_emit_resonance(
                symbol=event2.symbol,
                side=side,
                in_count=in_count,
                min_resonance=settings.MIN_RESONANCE,
            ):
                continue

            # Step 4️⃣：匹配组合（基于组合白名单 + 生命周期 + 升级）
            combo_results = match_combinations_with_lifecycle(
                raw_intervals=raw_in_intervals,
                states=states,
                pushed_combos=self.state.latest_combo_state[(event2.symbol, side)],
            )

            # Step 5️⃣：逐个组合执行推送
            for combo, is_upgrade in combo_results:
                topic_id = COMBINATION_ROUTING[combo]

                # Step 5.1：构建 snapshot + 消息体
                sig_str = f"{side.value}|{'|'.join(combo)}"
                signature = hashlib.md5(sig_str.encode("utf-8")).hexdigest()

                snap = ResonanceSnapshot(
                    symbol=event2.symbol,
                    side=side,
                    ts=event2.ts,
                    states=states,
                    score=len(combo),
                    signature=signature,
                )

                msg_prefix = "[升级]" if is_upgrade else ""
                msg = f"{msg_prefix} {format_message(snap, list(combo))}"

                # Step 5.2：发送推送
                await self.tg.send_message(
                    chat_id=settings.TG_CHAT_ID,
                    text=msg,
                    message_thread_id=topic_id,
                )

                # Step 5.3：记录该组合状态为“活跃”
                self.state.latest_combo_state[(event2.symbol, side)][combo] = {
                    "active": True,
                    "last_pushed_ts": event2.ts,
                    "max_iv": combo[0],
                }