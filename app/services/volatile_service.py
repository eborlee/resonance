from __future__ import annotations

import logging
from typing import List, Tuple

from ..config import settings, get_universe, get_main_topic_symbols, get_us_stock_symbols
from ..domain.models import VolatileEvent, Side, LevelState
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient
from ..infra.utils import ts_to_utc_str
from ..infra.chart import send_with_chart
from .zone_service import _get_obos_state

logger = logging.getLogger(__name__)

_VOLATILE_COOLDOWN = 4 * 3600

# 波动预警检查的 ob/os 周期（固定检查 4h 和 1h）
_OBOS_CHECK_INTERVALS = ("4h", "1h")

# 波动预警 interval → TG topic 属性名
_VOLATILE_TOPIC_MAP = {
    "1D": "TG_TOPIC_DAY",
    "1h": "TG_TOPIC_1H",
    "4h": "TG_TOPIC_4H",
}

_STATE_LABEL = {
    LevelState.IN: "IN",
    LevelState.WARM: "WARM",
}


class VolatileService:
    def __init__(self, state: AppState, tg: TelegramClient):
        self.state = state
        self.tg = tg

    async def handle_event(self, event: VolatileEvent) -> None:
        logger.info(f"[波动预警] 收到事件: {event.symbol} {event.interval}")

        allowed_intervals = get_universe().get(event.symbol)
        if not allowed_intervals:
            logger.warning(f"[波动预警] {event.symbol} 不在 universe，跳过")
            return

        now_ts = event.ts

        # 续期（或首次激活）波动预警状态
        self.state.update_volatile(event.symbol, event.interval, now_ts)
        logger.info(f"[波动预警] {event.symbol} {event.interval} 状态已续期")

        topic_attr = _VOLATILE_TOPIC_MAP.get(event.interval)
        if topic_attr is None:
            logger.warning(f"[波动预警] 不支持的 interval={event.interval}，跳过推送")
            return

        topic_id = getattr(settings, topic_attr)
        is_main = event.symbol in get_main_topic_symbols()
        is_us = event.symbol in get_us_stock_symbols()
        actual_topic = (
            settings.TG_TOPIC_MAIN if is_main else
            settings.TG_TOPIC_US if is_us else
            topic_id
        )

        for side in (Side.OVERBOUGHT, Side.OVERSOLD):
            # 收集处于 IN 或 WARM 的 ob/os 周期
            matched: List[Tuple[str, LevelState]] = []
            for iv in _OBOS_CHECK_INTERVALS:
                if iv not in allowed_intervals:
                    continue
                st = _get_obos_state(self.state, event.symbol, iv, side, now_ts)
                if st in (LevelState.IN, LevelState.WARM):
                    matched.append((iv, st))

            if not matched:
                logger.info(f"[波动预警] {event.symbol} {side.value} 无有效 ob/os，跳过")
                continue

            if self.state.is_volatile_in_cooldown(event.symbol, event.interval, side, now_ts, _VOLATILE_COOLDOWN):
                logger.info(f"[波动预警冷冻] {event.symbol} {event.interval} {side.value} 在冷冻期内，跳过")
                continue

            self.state.record_volatile_push(event.symbol, event.interval, side, now_ts)

            side_label = "超买" if side == Side.OVERBOUGHT else "超卖"
            dot = "🔴" if side == Side.OVERBOUGHT else "🟢"

            obos_lines = []
            for iv, st in matched:
                state_label = _STATE_LABEL[st]
                obos_lines.append(f"{dot} {iv} {side_label} {state_label}")

            msg = "\n".join([
                f"📶 {event.symbol} 波动预警",
                ts_to_utc_str(now_ts),
                f"区间: {event.interval}",
                "配合:",
                *obos_lines,
            ])

            chart_title = f"{event.symbol}  {event.interval}【波动预警】{side_label}"
            logger.warning(f"[波动预警推送] {event.symbol} {event.interval} {side.value}")

            await send_with_chart(
                tg=self.tg,
                msg=msg,
                chat_id=settings.TG_CHAT_ID,
                topic_id=actual_topic,
                symbol=event.symbol,
                max_iv=event.interval,
                chart_title=chart_title,
            )
