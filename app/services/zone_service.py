from __future__ import annotations

import logging
from typing import List, Tuple

from ..config import settings, get_universe, get_main_topic_symbols, get_us_stock_symbols
from ..domain.models import ZoneEvent, TvEvent, Side, LevelState
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient
from ..infra.utils import ts_to_utc_str
from ..infra.chart import send_with_chart
from collections import defaultdict
from .zone_rules import ZONE_RULES, ZONE_INTERVAL_TO_TOPIC_ATTR, ZONE_RULE_OVERRIDES

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN = 4 * 3600


def _get_obos_state(state: AppState, symbol: str, interval: str, side: Side, now_ts: float) -> LevelState:
    """读取 cache，返回某周期在指定方向的 IN/WARM/OUT 状态。"""
    rec = state.cache.get((symbol, interval))
    if rec is None:
        return LevelState.OUT

    if side == Side.OVERSOLD:
        if rec.in_oversold:
            return LevelState.IN
        if state.is_warm(symbol, interval, side, now_ts=now_ts):
            return LevelState.WARM
    else:
        if rec.in_overbought:
            return LevelState.IN
        if state.is_warm(symbol, interval, side, now_ts=now_ts):
            return LevelState.WARM

    return LevelState.OUT


def _format_zone_message(event: ZoneEvent, matched: List[Tuple[str, str, Side, LevelState]]) -> str:
    """
    matched: [(zone_interval, obos_interval, side, obos_state), ...]

    示例输出：
    📍 BTCUSDT 区域触及 (R)
    2026-04-07 08:00 UTC
    区域: 69435.1 - 69478.0 | 4h
    配合:
    - 1h 超买 IN
    - 15m 超卖 WARM
    """
    lines = [
        f"📍 {event.symbol} 关键区域触及",
        ts_to_utc_str(event.ts),
        f"区域: {event.bot} - {event.top} ({event.role}) | {event.interval}",
        "配合:",
    ]
    for _, obos_iv, side, obos_state in matched:
        side_label = "超买" if side == Side.OVERBOUGHT else "超卖"
        dot = "🔴" if side == Side.OVERBOUGHT else "🟢"
        lines.append(f"{dot} {obos_iv} {side_label} {obos_state.value.upper()}")
    return "\n".join(lines)


class ZoneService:
    def __init__(self, state: AppState, tg: TelegramClient):
        self.state = state
        self.tg = tg

    async def handle_event(self, event: ZoneEvent) -> None:
        logger.info(f"收到Zone事件: {event}")

        # Step 1：universe 过滤
        allowed_intervals = get_universe().get(event.symbol)
        if not allowed_intervals:
            logger.warning(f"Zone事件的symbol不在universe: {event.symbol}")
            return

        # Step 2：role 字段仅做校验，不参与方向匹配
        if event.role not in ("R", "S"):
            logger.warning(f"未知role: {event.role}")
            return

        # Step 3：更新 zone 触及缓存
        self.state.update_zone_touch(
            symbol=event.symbol,
            interval=event.interval,
            role=event.role,
            ts=event.ts,
            top=event.top,
            bot=event.bot,
        )

        now_ts = event.ts

        # Step 5：匹配规则——对每条规则同时查超买和超卖两个方向
        matched: List[Tuple[str, str, Side, LevelState]] = []
        for zone_iv, obos_iv in ZONE_RULES:
            if zone_iv != event.interval:
                continue
            if obos_iv not in allowed_intervals:
                continue

            for side in (Side.OVERBOUGHT, Side.OVERSOLD):
                obos_state = _get_obos_state(self.state, event.symbol, obos_iv, side, now_ts)
                logger.warning(f"[Zone匹配] {event.symbol} rule=({zone_iv},{obos_iv}) side={side.value} state={obos_state.value}")
                if obos_state == LevelState.IN:
                    matched.append((zone_iv, obos_iv, side, obos_state))

        if not matched:
            logger.warning(f"Zone事件无匹配规则: {event.symbol} {event.interval} {event.role} cache_keys={list(self.state.cache.keys())}")
            return

        # Step 5：冷冻过滤（per-rule 冷冻时长）
        active_matched = []
        for zone_iv, obos_iv, side, obos_state in matched:
            override = ZONE_RULE_OVERRIDES.get((zone_iv, obos_iv))
            cooldown = override["cooldown"] if override else _DEFAULT_COOLDOWN
            if self.state.is_zone_combo_in_cooldown(
                symbol=event.symbol,
                zone_iv=zone_iv,
                obos_iv=obos_iv,
                side=side,
                now_ts=now_ts,
                cooldown_seconds=cooldown,
            ):
                logger.info(
                    f"[Zone冷冻] {event.symbol} ({zone_iv}+{obos_iv} {side.value}) 在冷冻期内，跳过"
                )
            else:
                active_matched.append((zone_iv, obos_iv, side, obos_state))

        if not active_matched:
            logger.info(f"[Zone冷冻] {event.symbol} {event.interval} 所有匹配组合均在冷冻期内，不推送")
            return

        # Step 6：按目标 topic 分组
        is_main_symbol = event.symbol in get_main_topic_symbols()
        is_us_stock = event.symbol in get_us_stock_symbols()
        topic_groups: dict[tuple, list] = defaultdict(list)
        for item in active_matched:
            zone_iv, obos_iv, side, obos_state = item
            override = ZONE_RULE_OVERRIDES.get((zone_iv, obos_iv))
            if override:
                t_attr = override["topic_attr"]
                skip_main = override["skip_main"]
            else:
                t_attr = ZONE_INTERVAL_TO_TOPIC_ATTR.get(zone_iv)
                if t_attr is None:
                    logger.warning(f"Zone interval 无对应topic配置: {zone_iv}")
                    continue
                skip_main = False
            topic_groups[(t_attr, skip_main)].append(item)

        # Step 7：先统一占坑，再逐组推送
        for zone_iv, obos_iv, side, _ in active_matched:
            self.state.record_zone_combo_push(
                symbol=event.symbol,
                zone_iv=zone_iv,
                obos_iv=obos_iv,
                side=side,
                now_ts=now_ts,
            )

        for (t_attr, skip_main), items in topic_groups.items():
            topic_id = getattr(settings, t_attr)
            actual_topic = topic_id if skip_main else (
                settings.TG_TOPIC_MAIN if is_main_symbol else
                settings.TG_TOPIC_US if is_us_stock else
                topic_id
            )
            obos_str = " | ".join(
                f"{obos_iv}{'超买' if side == Side.OVERBOUGHT else '超卖'}"
                for _, obos_iv, side, _ in items
            )
            msg = _format_zone_message(event, items)
            chart_title = f"{event.symbol}  {event.interval}【关键区域】{obos_str}"
            logger.warning(f"[Zone推送] {event.symbol} {event.interval} {event.role} topic={t_attr} matched={[(r[1], r[2].value) for r in items]}")
            await send_with_chart(
                tg=self.tg,
                msg=msg,
                chat_id=settings.TG_CHAT_ID,
                topic_id=actual_topic,
                symbol=event.symbol,
                max_iv=event.interval,
                zone_bot=event.bot,
                zone_top=event.top,
                zone_role=event.role,
                chart_title=chart_title,
            )

    async def handle_obos_reverse(self, event: TvEvent) -> None:
        """
        反查逻辑：ob/os 收盘确认 IN 时，检查刚收的这根 K 内是否触及过 zone。
        与正向共用 (symbol, zone_iv, obos_iv, side) 冷冻 key，天然防重复推送。
        """
        symbol = event.symbol
        allowed_intervals = get_universe().get(symbol)
        if not allowed_intervals:
            return

        now_ts = event.ts

        for sig in event.signals:
            obos_iv = sig.interval
            if obos_iv not in allowed_intervals:
                continue

            rec = self.state.cache.get((symbol, obos_iv))
            if rec is None:
                continue

            obos_candle_sec = self.state.interval_seconds.get(obos_iv)
            if obos_candle_sec is None:
                continue

            for side in (Side.OVERBOUGHT, Side.OVERSOLD):
                is_in = rec.in_overbought if side == Side.OVERBOUGHT else rec.in_oversold
                if not is_in:
                    continue

                for zone_iv, z_obos_iv in ZONE_RULES:
                    if z_obos_iv != obos_iv:
                        continue
                    if zone_iv not in allowed_intervals:
                        continue

                    for role in ("R", "S"):
                        entry = self.state.zone_touch_cache.get((symbol, zone_iv, role))
                        if entry is None:
                            continue

                        touch_ts, top, bot = entry

                        # 时间窗口：zone 触及在刚收的这根 ob/os K 线内（+300s 容差应对时间戳抖动）
                        if (now_ts - touch_ts) >= obos_candle_sec + 300:
                            continue

                        override = ZONE_RULE_OVERRIDES.get((zone_iv, obos_iv))
                        cooldown = override["cooldown"] if override else _DEFAULT_COOLDOWN

                        if self.state.is_zone_combo_in_cooldown(
                            symbol=symbol,
                            zone_iv=zone_iv,
                            obos_iv=obos_iv,
                            side=side,
                            now_ts=now_ts,
                            cooldown_seconds=cooldown,
                        ):
                            logger.info(
                                f"[Zone反查冷冻] {symbol} ({zone_iv}+{obos_iv} {side.value}) 在冷冻期内，跳过"
                            )
                            continue

                        self.state.record_zone_combo_push(
                            symbol=symbol, zone_iv=zone_iv, obos_iv=obos_iv,
                            side=side, now_ts=now_ts,
                        )

                        side_label = "超买" if side == Side.OVERBOUGHT else "超卖"
                        dot = "🔴" if side == Side.OVERBOUGHT else "🟢"

                        msg = "\n".join([
                            f"📍 {symbol} 关键区域合成",
                            ts_to_utc_str(now_ts),
                            f"区域: {bot} - {top} ({role}) | {zone_iv}",
                            f"触及于: {ts_to_utc_str(touch_ts)}",
                            "配合:",
                            f"{dot} {obos_iv} {side_label} IN",
                        ])

                        if override:
                            t_attr = override["topic_attr"]
                            skip_main = override["skip_main"]
                        else:
                            t_attr = ZONE_INTERVAL_TO_TOPIC_ATTR.get(zone_iv)
                            if t_attr is None:
                                logger.warning(f"[Zone反查] zone interval 无对应topic配置: {zone_iv}")
                                continue
                            skip_main = False

                        topic_id = getattr(settings, t_attr)
                        is_main = symbol in get_main_topic_symbols()
                        is_us_stock_reverse = symbol in get_us_stock_symbols()
                        actual_topic = topic_id if skip_main else (
                            settings.TG_TOPIC_MAIN if is_main else
                            settings.TG_TOPIC_US if is_us_stock_reverse else
                            topic_id
                        )

                        chart_title = f"{symbol}  {zone_iv}【区域合成】{obos_iv}{side_label}"
                        logger.warning(
                            f"[Zone反查推送] {symbol} ({zone_iv}+{obos_iv} {side.value}) "
                            f"zone触及={ts_to_utc_str(touch_ts)}"
                        )

                        await send_with_chart(
                            tg=self.tg,
                            msg=msg,
                            chat_id=settings.TG_CHAT_ID,
                            topic_id=actual_topic,
                            symbol=symbol,
                            max_iv=zone_iv,
                            zone_bot=bot,
                            zone_top=top,
                            zone_role=role,
                            chart_title=chart_title,
                        )
