from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import settings, get_universe
from ..domain.models import DivergenceEvent, Side
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient
from ..infra.utils import ts_to_utc_str
from ..infra.chart import send_with_chart

logger = logging.getLogger(__name__)

# 支持的周期 → 推送 topic 的 settings 字段名
DIVERGENCE_INTERVAL_TO_TOPIC_ATTR: dict[str, str] = {
    "4h": "TG_TOPIC_4H",
    "1h": "TG_TOPIC_1H",
}


def _get_in_sides(state: AppState, symbol: str, interval: str) -> list[Side]:
    """返回该周期当前处于 IN 状态的方向列表（可能同时有超买和超卖）。"""
    rec = state.cache.get((symbol, interval))
    if rec is None:
        return []
    sides = []
    if rec.in_overbought:
        sides.append(Side.OVERBOUGHT)
    if rec.in_oversold:
        sides.append(Side.OVERSOLD)
    return sides


def _format_message(event: DivergenceEvent, sides: list[Side]) -> str:
    """
    示例：
    📐 BTCUSDT 顶底背离
    2026-04-15 08:00 UTC | 4h
    当前状态: 超买🔴
    """
    side_labels = " / ".join(s.display for s in sides)
    return "\n".join([
        f"📐 {event.symbol} 顶底背离",
        f"{ts_to_utc_str(event.ts)} | {event.interval}",
        f"当前状态: {side_labels}",
    ])


class DivergenceService:
    def __init__(self, state: AppState, tg: TelegramClient):
        self.state = state
        self.tg = tg

    async def handle_event(self, event: DivergenceEvent) -> None:
        logger.info(f"收到Divergence事件: {event}")

        # Step 1：universe 过滤
        allowed_intervals = get_universe().get(event.symbol)
        if not allowed_intervals:
            logger.warning(f"Divergence事件的symbol不在universe: {event.symbol}")
            return
        if event.interval not in allowed_intervals:
            logger.warning(f"Divergence事件的interval不在universe: {event.symbol} {event.interval}")
            return

        # Step 2：记录本次背离触发（转为人可读时间字符串）
        dt_str = datetime.fromtimestamp(event.ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.state.divergence_cache[(event.symbol, event.interval)] = dt_str

        # Step 3：检查同周期是否处于 IN 状态（超买或超卖任一即可）
        in_sides = _get_in_sides(self.state, event.symbol, event.interval)
        if not in_sides:
            logger.info(f"Divergence无共振: {event.symbol} {event.interval} 同级别不在IN状态")
            return

        # Step 4：确定推送 topic
        topic_attr = DIVERGENCE_INTERVAL_TO_TOPIC_ATTR.get(event.interval)
        if topic_attr is None:
            logger.warning(f"Divergence interval 无对应topic配置: {event.interval}")
            return
        topic_id = getattr(settings, topic_attr)

        # Step 5：推送
        msg = _format_message(event, in_sides)
        logger.warning(f"[Divergence推送] {event.symbol} {event.interval} sides={[s.value for s in in_sides]}")
        obos_str = " | ".join(
            f"{event.interval}{'超买' if s == Side.OVERBOUGHT else '超卖'}"
            for s in in_sides
        )
        chart_title = f"{event.symbol}  {event.interval}【顶底背离】{obos_str}"
        await send_with_chart(
            tg=self.tg,
            msg=msg,
            chat_id=settings.TG_CHAT_ID,
            topic_id=topic_id,
            symbol=event.symbol,
            max_iv=event.interval,
            chart_title=chart_title,
        )
