from __future__ import annotations

import logging
from typing import List, Tuple

from ..config import settings, get_universe
from ..domain.models import TrendEvent, Side, LevelState
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient
from ..infra.chart import send_with_chart
from .zone_service import _get_obos_state
from .trend_rules import (
    TREND_LABEL_SIDE,
    TREND_RULES,
    TREND_TOUCH_WINDOW_SECONDS,
    TREND_COOLDOWN_SECONDS,
)

logger = logging.getLogger(__name__)


def _format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 3600:
        return f"{seconds // 60}分钟前"
    if seconds < 86400:
        return f"{seconds // 3600}小时前"
    return f"{seconds // 86400}天前"


def _format_trend_message(
    event: TrendEvent,
    label: str,
    side: Side,
    obos_matches: List[Tuple[str, LevelState]],
    zone_matches: List[Tuple[str, float, float, str, float]],
    ema_matches: List[Tuple[int, str, float, float, float]],
    now_ts: float,
) -> str:
    """
    matched 均为反查命中的配合信息，任一非空即代表本次有配合。

    示意：
    🟢 AAVEUSDT 趋势信号
    潜在底部 | 4h

    配合:
    🟢 4h 超卖 IN
    🟢 1h 超卖 WARM

    近期区域:
    📍 69200 - 69500 (S) | 4h  [3小时前触及]
    """
    dot = "🟢" if side == Side.OVERSOLD else "🔴"
    side_label = "超卖" if side == Side.OVERSOLD else "超买"

    lines = [
        f"{dot} {event.symbol} 趋势信号",
        f"{label} | {event.interval}",
    ]

    if obos_matches:
        lines.append("")
        lines.append("配合:")
        for obos_iv, obos_state in obos_matches:
            lines.append(f"{dot} {obos_iv} {side_label} {obos_state.value.upper()}")

    if zone_matches:
        lines.append("")
        lines.append("近期区域:")
        for role, top, bot, iv, touch_ts in zone_matches:
            lines.append(f"📍 {bot} - {top} ({role}) | {iv}  [{_format_elapsed(now_ts - touch_ts)}触及]")

    if ema_matches:
        lines.append("")
        lines.append("近期均线:")
        for period, role, ema_value, close, touch_ts in ema_matches:
            lines.append(f"📈 EMA{period} {ema_value} ({role}) | {event.interval}  [{_format_elapsed(now_ts - touch_ts)}触及]")

    return "\n".join(lines)


class TrendService:
    def __init__(self, state: AppState, tg: TelegramClient):
        self.state = state
        self.tg = tg

    async def handle_event(self, event: TrendEvent) -> None:
        logger.info(f"收到Trend事件: {event}")

        allowed_intervals = get_universe().get(event.symbol)
        if not allowed_intervals:
            logger.warning(f"Trend事件的symbol不在universe: {event.symbol}")
            return

        now_ts = event.ts

        # 无条件先记录到标签缓存（供 Idea 1 图表标注使用），不管后面是否推送
        for label in event.labels:
            try:
                self.state.record_trend_label(event.symbol, event.interval, now_ts, label)
            except Exception:
                logger.error(f"[Trend标签缓存] 记录失败，不影响后续反查/推送: {event.symbol} {label}", exc_info=True)

        window_sec = TREND_TOUCH_WINDOW_SECONDS.get(event.interval)
        if window_sec is None:
            logger.warning(f"Trend interval 无对应反查窗口配置: {event.interval}")
            return

        for label in event.labels:
            side = TREND_LABEL_SIDE.get(label)
            if side is None:
                logger.warning(f"[Trend] 未知标签，跳过: {label}")
                continue

            # 1. OB/OS 反查（按 TREND_RULES 覆盖的周期，按标签方向只查一侧）
            obos_matches: List[Tuple[str, LevelState]] = []
            for trigger_iv, obos_iv in TREND_RULES:
                if trigger_iv != event.interval:
                    continue
                if obos_iv not in allowed_intervals:
                    continue
                obos_state = _get_obos_state(self.state, event.symbol, obos_iv, side, now_ts)
                if obos_state in (LevelState.IN, LevelState.WARM):
                    obos_matches.append((obos_iv, obos_state))

            # 2. 近期区域触及（只查触发同级别，不分 R/S），只保留最近一条
            zone_matches: List[Tuple[str, float, float, str, float]] = []
            for (sym, iv, role), (touch_ts, top, bot) in self.state.zone_touch_cache.items():
                if sym != event.symbol or iv != event.interval:
                    continue
                if now_ts - touch_ts > window_sec:
                    continue
                zone_matches.append((role, top, bot, iv, touch_ts))
            zone_matches = sorted(zone_matches, key=lambda x: x[4], reverse=True)[:1]

            # 3. 近期均线触及（只查触发同级别），只保留时间最近的一条（不区分 period/role）
            ema_matches: List[Tuple[int, str, float, float, float]] = []
            for (sym, iv, period, role), (touch_ts, ema_value, close) in self.state.ema_touch_cache.items():
                if sym != event.symbol or iv != event.interval:
                    continue
                if now_ts - touch_ts > window_sec:
                    continue
                ema_matches.append((period, role, ema_value, close, touch_ts))
            ema_matches = sorted(ema_matches, key=lambda x: x[4], reverse=True)[:1]

            if not obos_matches and not zone_matches and not ema_matches:
                logger.info(f"[Trend] {event.symbol} {event.interval} {label} 无配合，静默跳过")
                continue

            if self.state.is_trend_label_in_cooldown(
                event.symbol, event.interval, label, now_ts, TREND_COOLDOWN_SECONDS
            ):
                logger.info(f"[Trend冷冻] {event.symbol} {event.interval} {label} 在冷冻期内，跳过")
                continue

            self.state.record_trend_label_push(event.symbol, event.interval, label, now_ts)

            msg = _format_trend_message(event, label, side, obos_matches, zone_matches, ema_matches, now_ts)

            base_symbol = event.symbol.upper().replace("USDT", "")
            suffix_parts: List[str] = []
            if obos_matches:
                suffix_parts.append("超卖" if side == Side.OVERSOLD else "超买")
            if zone_matches:
                suffix_parts.append("关键区域")
            if ema_matches:
                suffix_parts.append("接触均线")
            suffix = (" + " + " + ".join(suffix_parts)) if suffix_parts else ""
            chart_title = f"{base_symbol}  {event.interval} {label}{suffix}"
            chart_title_color = "#26a69a" if side == Side.OVERSOLD else "#ef5350"
            logger.warning(
                f"[Trend推送] {event.symbol} {event.interval} {label} "
                f"obos={obos_matches} zone={len(zone_matches)} ema={len(ema_matches)}"
            )
            try:
                await send_with_chart(
                    tg=self.tg,
                    msg=msg,
                    chat_id=settings.TG_CHAT_ID,
                    topic_id=settings.TG_TOPIC_MAIN,
                    symbol=event.symbol,
                    max_iv=event.interval,
                    trend_annotations_provider=lambda iv, sym=event.symbol: self.state.get_recent_trend_labels(sym, iv),
                    chart_title=chart_title,
                    title_color=chart_title_color,
                )
            except Exception:
                logger.error(f"[Trend推送] 发送失败: {event.symbol} {event.interval} {label}", exc_info=True)
