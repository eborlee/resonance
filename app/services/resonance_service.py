from __future__ import annotations

import hashlib
from typing import List, Dict

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
    核心编排层：
    - 接收单周期事件
    - 更新服务端缓存（store）
    - 聚合 universe 中所有周期的状态
    - 过滤无意义快周期（按 max interval 的 floor）
    - 使用有效 IN_count 做门控与路由
    """

    def __init__(self, state: AppState, tg: TelegramClient):
        self.state = state
        self.tg = tg

    async def handle_event(self, event: TvEvent):
        # 1) universe 过滤
        event2 = filter_by_universe(event)
        if event2 is None or not event2.signals:
            return
        print(event)
        print(event2)
        # 当前实现：parser 是单周期单值
        intervals_updated = set()

        for sig in event2.signals:
            interval = sig.interval
            value = float(sig.values[0])
            self.state.update_interval(
                symbol=event2.symbol,
                interval=interval,
                value=value,
                ob_level=settings.OB_LEVEL,
                os_level=settings.OS_LEVEL,
            )
            intervals_updated.add(interval)



        allowed_intervals = universe.get(event2.symbol, [])
        print(event2,"允许的intervals:",allowed_intervals)
        if not allowed_intervals:
            return

        # topic id 映射（topic_name -> id）
        topic_ids = {
            "long": settings.TG_TOPIC_LONG,
            "mid": settings.TG_TOPIC_MID,
            "short": settings.TG_TOPIC_SHORT,
            "ultra": settings.TG_TOPIC_ULTRA,
        }

        # 3) 两个方向分别处理（oversold / overbought）
        for side in (Side.OVERSOLD, Side.OVERBOUGHT):
            states: Dict[str, IntervalState] = {}

            # 3.1 聚合每个 interval 的 IN/WARM/OUT（基于 store cache）
            for iv in allowed_intervals:
                rec = self.state.cache.get((event2.symbol, iv))

                if rec is None:
                    st = LevelState.OUT
                    v = 0.0
                else:
                    v = rec.value
                    if side == Side.OVERSOLD:
                        if v <= settings.OS_LEVEL:
                            st = LevelState.IN
                        elif rec.warm_ttl_oversold > 0:
                            st = LevelState.WARM
                        else:
                            st = LevelState.OUT

                        # 余温递减：只在“本 interval 收到新事件且当前不在IN”时
                        if iv in intervals_updated and st != LevelState.IN:
                            self.state.tick_warm_ttl(event2.symbol, iv, side)
                    else:
                        if v >= settings.OB_LEVEL:
                            st = LevelState.IN
                        elif rec.warm_ttl_overbought > 0:
                            st = LevelState.WARM
                        else:
                            st = LevelState.OUT

                        if iv in intervals_updated and st != LevelState.IN:
                            self.state.tick_warm_ttl(event2.symbol, iv, side)

                states[iv] = IntervalState(interval=iv, state=st, value=v)

            # 3.2 取原始 IN 周期集合（未过滤）
            raw_in_intervals = [iv for iv, st in states.items() if st.state == LevelState.IN]
            print("raw_in_intervals: ",raw_in_intervals)

            # 3.3 如果没有任何 IN，仍然需要让 gate 口径更新为 0（防止“上次值”悬空）
            if not raw_in_intervals:
                self.state.should_emit_resonance(
                    symbol=event2.symbol,
                    side=side,
                    in_count=0,
                    min_resonance=settings.MIN_RESONANCE,
                )
                continue

            # 3.4 确定组合最大周期（按 IN 周期）
            max_iv = max_interval(raw_in_intervals)
            if max_iv is None:
                # 同样更新 gate
                self.state.should_emit_resonance(
                    symbol=event2.symbol,
                    side=side,
                    in_count=0,
                    min_resonance=settings.MIN_RESONANCE,
                )
                continue
            

            # 3.5 应用 floor：过滤掉无意义的快周期（核心：必须在门控前做）
            effective_in_intervals = apply_min_interval_floor(
                in_intervals=raw_in_intervals,
                max_iv=max_iv,
                max_interval_min_allowed=routing_rules["max_interval_min_allowed"],
            )
            
            effective_in_count = len(effective_in_intervals)
            print("有效的周期数：",effective_in_count)
            print("有效的周期：",effective_in_intervals)
            print("-----")
            # 3.6 用“过滤后的 in_count”做门控（共振≥2 且增加才推）
            if not self.state.should_emit_resonance(
                symbol=event2.symbol,
                side=side,
                in_count=effective_in_count,
                min_resonance=settings.MIN_RESONANCE,  # =2
            ):
                continue

            # 3.7 路由：用“过滤后的 intervals”选择 topic
            topic_id = choose_topic_by_max_interval(
                intervals_present=effective_in_intervals,
                max_interval_to_topic=routing_rules["max_interval_to_topic"],
                topic_ids=topic_ids,
            )
            

            # 3.8 生成 signature（仅用于排查/日志，不参与门控）
            sig_str = side.value + "|" + "|".join(
                f"{iv}:{states[iv].state.value}" for iv in allowed_intervals
            )
            signature = hashlib.md5(sig_str.encode("utf-8")).hexdigest()

            snap = ResonanceSnapshot(
                symbol=event2.symbol,
                side=side,
                ts=event2.ts,
                states=states,
                score=effective_in_count,  # score 与有效 IN_count 一致
                signature=signature,
            )

            msg = format_message(snap, effective_in_intervals)
            await self.tg.send_message(
                chat_id=settings.TG_CHAT_ID,
                text=msg,
                message_thread_id=topic_id,
            )
