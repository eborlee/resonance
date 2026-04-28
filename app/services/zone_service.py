from __future__ import annotations

import asyncio
import logging
from typing import List, Tuple

from ..config import settings, get_universe
from ..domain.models import ZoneEvent, Side, LevelState
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient
from ..infra.utils import ts_to_utc_str
from .zone_rules import ZONE_RULES, ZONE_INTERVAL_TO_TOPIC_ATTR

logger = logging.getLogger(__name__)


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

        # Step 5：冷冻过滤（4h 内相同 zone+obos 组合不重复推送）
        _ZONE_COMBO_COOLDOWN = 4 * 3600  # 秒
        active_matched = []
        for zone_iv, obos_iv, side, obos_state in matched:
            if self.state.is_zone_combo_in_cooldown(
                symbol=event.symbol,
                zone_iv=zone_iv,
                obos_iv=obos_iv,
                side=side,
                now_ts=now_ts,
                cooldown_seconds=_ZONE_COMBO_COOLDOWN,
            ):
                logger.info(
                    f"[Zone冷冻] {event.symbol} ({zone_iv}+{obos_iv} {side.value}) 在冷冻期内，跳过"
                )
            else:
                active_matched.append((zone_iv, obos_iv, side, obos_state))

        if not active_matched:
            logger.info(f"[Zone冷冻] {event.symbol} {event.interval} 所有匹配组合均在冷冻期内，不推送")
            return

        # Step 6：确定推送 topic
        topic_attr = ZONE_INTERVAL_TO_TOPIC_ATTR.get(event.interval)
        if topic_attr is None:
            logger.warning(f"Zone interval 无对应topic配置: {event.interval}")
            return
        topic_id = getattr(settings, topic_attr)

        # Step 7：推送，并记录冷冻时间戳
        msg = _format_zone_message(event, active_matched)
        logger.warning(f"[Zone推送] {event.symbol} {event.interval} {event.role} matched={[(r[1], r[2].value) for r in active_matched]}")
        await self.tg.send_message(
            chat_id=settings.TG_CHAT_ID,
            text=msg,
            message_thread_id=topic_id,
        )
        for zone_iv, obos_iv, side, _ in active_matched:
            self.state.record_zone_combo_push(
                symbol=event.symbol,
                zone_iv=zone_iv,
                obos_iv=obos_iv,
                side=side,
                now_ts=now_ts,
            )
