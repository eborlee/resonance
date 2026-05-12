from __future__ import annotations

import logging
from typing import List, Tuple

from ..config import settings, get_universe, get_main_topic_symbols, get_us_stock_symbols
from ..domain.models import EmaEvent, Side, LevelState
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient
from ..infra.utils import ts_to_utc_str
from ..infra.chart import send_with_chart
from .zone_service import _get_obos_state
from .zone_rules import EMA200_RULES, EMA200_INTERVAL_TO_TOPIC_ATTR

logger = logging.getLogger(__name__)

_EMA200_COOLDOWN = 4 * 3600
_EMA55_COMBO = ("1h", "15m")
_EMA55_COOLDOWN = 4 * 3600


class EmaService:
    def __init__(self, state: AppState, tg: TelegramClient):
        self.state = state
        self.tg = tg

    async def handle_event(self, event: EmaEvent) -> None:
        logger.info(f"收到EMA事件: period={event.period} {event}")
        if event.period == 200:
            await self._handle_ema200(event)
        elif event.period == 55:
            await self._handle_ema55(event)
        else:
            logger.warning(f"[EMA] 暂不支持的period={event.period}，跳过")

    # ────────────────────────────────────────────────
    # EMA200：触及 EMA200 + 同级别 ob/os IN
    # ────────────────────────────────────────────────

    async def _handle_ema200(self, event: EmaEvent) -> None:
        allowed_intervals = get_universe().get(event.symbol)
        if not allowed_intervals:
            logger.warning(f"EMA200事件的symbol不在universe: {event.symbol}")
            return

        now_ts = event.ts
        matched: List[Tuple[str, str, Side, LevelState]] = []

        for ema_iv, obos_iv in EMA200_RULES:
            if ema_iv != event.interval:
                continue
            if obos_iv not in allowed_intervals:
                continue
            for side in (Side.OVERBOUGHT, Side.OVERSOLD):
                obos_state = _get_obos_state(self.state, event.symbol, obos_iv, side, now_ts)
                logger.info(f"[EMA200匹配] {event.symbol} rule=({ema_iv},{obos_iv}) side={side.value} state={obos_state.value}")
                if obos_state == LevelState.IN:
                    matched.append((ema_iv, obos_iv, side, obos_state))

        if not matched:
            logger.info(f"EMA200事件无匹配: {event.symbol} {event.interval}")
            return

        active_matched = [
            m for m in matched
            if not self.state.is_ema200_combo_in_cooldown(
                symbol=event.symbol,
                ema200_iv=m[0], obos_iv=m[1], side=m[2],
                now_ts=now_ts, cooldown_seconds=_EMA200_COOLDOWN,
            )
        ]
        if not active_matched:
            logger.info(f"[EMA200冷冻] {event.symbol} {event.interval} 所有匹配均在冷冻期")
            return

        topic_attr = EMA200_INTERVAL_TO_TOPIC_ATTR.get(event.interval)
        if topic_attr is None:
            logger.warning(f"EMA200 interval 无对应topic配置: {event.interval}")
            return
        topic_id = getattr(settings, topic_attr)
        _is_main = event.symbol in get_main_topic_symbols()
        _is_us = event.symbol in get_us_stock_symbols()
        actual_topic = (
            settings.TG_TOPIC_MAIN if _is_main else
            settings.TG_TOPIC_US if _is_us else
            topic_id
        )

        obos_str = " | ".join(
            f"{obos_iv}{'超买' if side == Side.OVERBOUGHT else '超卖'}"
            for _, obos_iv, side, _ in active_matched
        )
        msg_lines = [
            f"〽️ {event.symbol} EMA200 触及",
            ts_to_utc_str(event.ts),
            f"EMA200: {event.ema_value} ({event.role}) | {event.interval}",
            "配合:",
        ]
        for _, obos_iv, side, _ in active_matched:
            dot = "🔴" if side == Side.OVERBOUGHT else "🟢"
            msg_lines.append(f"{dot} {obos_iv} {'超买' if side == Side.OVERBOUGHT else '超卖'} IN")

        chart_title = f"{event.symbol}  {event.interval}【EMA200触及】{obos_str}"
        logger.warning(f"[EMA200推送] {event.symbol} {event.interval} {event.role}")
        for ema_iv, obos_iv, side, _ in active_matched:
            self.state.record_ema200_combo_push(
                symbol=event.symbol, ema200_iv=ema_iv,
                obos_iv=obos_iv, side=side, now_ts=now_ts,
            )
        await send_with_chart(
            tg=self.tg, msg="\n".join(msg_lines),
            chat_id=settings.TG_CHAT_ID, topic_id=actual_topic,
            symbol=event.symbol, max_iv=event.interval, chart_title=chart_title,
        )

    # ────────────────────────────────────────────────
    # EMA55：触及 EMA55 + 1h15m 共振 active
    # ────────────────────────────────────────────────

    async def _handle_ema55(self, event: EmaEvent) -> None:
        allowed_intervals = get_universe().get(event.symbol)
        if not allowed_intervals:
            logger.warning(f"EMA55事件的symbol不在universe: {event.symbol}")
            return

        now_ts = event.ts
        matched_sides: List[Side] = []

        for side in (Side.OVERBOUGHT, Side.OVERSOLD):
            meta = self.state.latest_combo_state.get((event.symbol, side), {}).get(_EMA55_COMBO)
            if not meta or not meta.get("active"):
                logger.info(f"[EMA55] {event.symbol} {side.value} 1h+15m 不活跃，跳过")
                continue
            # 校验两个分量周期仍在 IN（不接受 WARM），防止分量已退出超买/超卖但 combo 尚未清理的误触
            still_in = True
            for iv in _EMA55_COMBO:
                rec = self.state.cache.get((event.symbol, iv))
                if rec is None:
                    still_in = False
                    break
                currently_in = rec.in_overbought if side == Side.OVERBOUGHT else rec.in_oversold
                if not currently_in:
                    logger.info(f"[EMA55] {event.symbol} {side.value} {iv} 已不在 IN，跳过")
                    still_in = False
                    break
            if not still_in:
                continue
            if self.state.is_ema55_in_cooldown(event.symbol, side, now_ts, _EMA55_COOLDOWN):
                logger.info(f"[EMA55冷冻] {event.symbol} {side.value} 在冷冻期内，跳过")
                continue
            matched_sides.append(side)

        if not matched_sides:
            logger.info(f"[EMA55] {event.symbol} 无匹配共振或均在冷冻期")
            return

        is_main = event.symbol in get_main_topic_symbols()
        is_us = event.symbol in get_us_stock_symbols()
        topic_id = (
            settings.TG_TOPIC_MAIN if is_main else
            settings.TG_TOPIC_US if is_us else
            settings.TG_TOPIC_1H
        )

        for side in matched_sides:
            self.state.record_ema55_push(event.symbol, side, now_ts)

        for side in matched_sides:
            side_label = "超卖" if side == Side.OVERSOLD else "超买"
            dot = "🟢" if side == Side.OVERSOLD else "🔴"
            role_label = "支撑" if event.role == "S" else "阻力"
            msg = "\n".join([
                f"〽️ {event.symbol} EMA55 触及",
                ts_to_utc_str(event.ts),
                f"EMA55: {event.ema_value} ({role_label}) | {event.interval}",
                f"配合: 1h+15m 共振",
                f"{dot} {side_label} IN",
            ])
            chart_title = f"{event.symbol}  {event.interval}【EMA55触及】1h+15m {side_label}"
            logger.warning(f"[EMA55推送] {event.symbol} {side.value} 1h+15m active")
            await send_with_chart(
                tg=self.tg, msg=msg,
                chat_id=settings.TG_CHAT_ID, topic_id=topic_id,
                symbol=event.symbol, max_iv=event.interval, chart_title=chart_title,
            )
