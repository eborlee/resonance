from __future__ import annotations

import logging
from typing import List, Tuple

from ..config import settings, get_universe, get_main_topic_symbols
from ..domain.models import Ema200Event, Side, LevelState
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient
from ..infra.utils import ts_to_utc_str
from ..infra.chart import send_with_chart
from .zone_service import _get_obos_state
from .zone_rules import EMA200_RULES, EMA200_INTERVAL_TO_TOPIC_ATTR

logger = logging.getLogger(__name__)

_EMA200_COMBO_COOLDOWN = 4 * 3600  # 秒


def _format_ema200_message(event: Ema200Event, matched: List[Tuple[str, str, Side, LevelState]]) -> str:
    lines = [
        f"〽️ {event.symbol} EMA200 触及",
        ts_to_utc_str(event.ts),
        f"EMA200: {event.ema200} ({event.role}) | {event.interval}",
        "配合:",
    ]
    for _, obos_iv, side, _ in matched:
        side_label = "超买" if side == Side.OVERBOUGHT else "超卖"
        dot = "🔴" if side == Side.OVERBOUGHT else "🟢"
        lines.append(f"{dot} {obos_iv} {side_label} IN")
    return "\n".join(lines)


class Ema200Service:
    def __init__(self, state: AppState, tg: TelegramClient):
        self.state = state
        self.tg = tg

    async def handle_event(self, event: Ema200Event) -> None:
        logger.info(f"收到EMA200事件: {event}")

        allowed_intervals = get_universe().get(event.symbol)
        if not allowed_intervals:
            logger.warning(f"EMA200事件的symbol不在universe: {event.symbol}")
            return

        now_ts = event.ts

        # 匹配规则
        matched: List[Tuple[str, str, Side, LevelState]] = []
        for ema200_iv, obos_iv in EMA200_RULES:
            if ema200_iv != event.interval:
                continue
            if obos_iv not in allowed_intervals:
                continue

            for side in (Side.OVERBOUGHT, Side.OVERSOLD):
                obos_state = _get_obos_state(self.state, event.symbol, obos_iv, side, now_ts)
                logger.info(f"[EMA200匹配] {event.symbol} rule=({ema200_iv},{obos_iv}) side={side.value} state={obos_state.value}")
                if obos_state == LevelState.IN:
                    matched.append((ema200_iv, obos_iv, side, obos_state))

        if not matched:
            logger.info(f"EMA200事件无匹配: {event.symbol} {event.interval}")
            return

        # 冷冻过滤
        active_matched = []
        for ema200_iv, obos_iv, side, obos_state in matched:
            if self.state.is_ema200_combo_in_cooldown(
                symbol=event.symbol,
                ema200_iv=ema200_iv,
                obos_iv=obos_iv,
                side=side,
                now_ts=now_ts,
                cooldown_seconds=_EMA200_COMBO_COOLDOWN,
            ):
                logger.info(f"[EMA200冷冻] {event.symbol} ({ema200_iv}+{obos_iv} {side.value}) 在冷冻期内，跳过")
            else:
                active_matched.append((ema200_iv, obos_iv, side, obos_state))

        if not active_matched:
            logger.info(f"[EMA200冷冻] {event.symbol} {event.interval} 所有匹配组合均在冷冻期内，不推送")
            return

        # 确定推送 topic
        topic_attr = EMA200_INTERVAL_TO_TOPIC_ATTR.get(event.interval)
        if topic_attr is None:
            logger.warning(f"EMA200 interval 无对应topic配置: {event.interval}")
            return
        topic_id = getattr(settings, topic_attr)

        # 推送
        msg = _format_ema200_message(event, active_matched)
        logger.warning(f"[EMA200推送] {event.symbol} {event.interval} {event.role} matched={[(r[1], r[2].value) for r in active_matched]}")

        actual_topic = settings.TG_TOPIC_MAIN if event.symbol in get_main_topic_symbols() else topic_id
        await send_with_chart(
            tg=self.tg,
            msg=msg,
            chat_id=settings.TG_CHAT_ID,
            topic_id=actual_topic,
            symbol=event.symbol,
            max_iv=event.interval,
        )

        for ema200_iv, obos_iv, side, _ in active_matched:
            self.state.record_ema200_combo_push(
                symbol=event.symbol,
                ema200_iv=ema200_iv,
                obos_iv=obos_iv,
                side=side,
                now_ts=now_ts,
            )
