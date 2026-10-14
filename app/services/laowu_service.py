from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ..config import settings, get_universe
from ..domain.models import LaoWuSignal, Side
from ..infra.store import AppState
from ..infra.chart import send_with_chart

if TYPE_CHECKING:
    from ..adapters.tg_client import TelegramClient

logger = logging.getLogger(__name__)

_SNAPSHOT_INTERVALS = ("1D", "4h", "1h", "15m")

_LEVEL_DISPLAY = {
    "1级": "⭐", "2级": "⭐⭐", "3级": "⭐⭐⭐", "4级": "⭐⭐⭐⭐", "5级": "⭐⭐⭐⭐⭐",
    "S级": "🏆S", "A级": "🥇A", "B级": "🥈B", "C级": "🥉C", "D级": "D",
}


class LaoWuService:
    def __init__(self, state: AppState, tg: "TelegramClient"):
        self.state = state
        self.tg = tg

    async def handle_signal(self, signal: LaoWuSignal) -> None:
        now_ts = signal.ts or time.time()

        if not self.state.is_in_focus(signal.symbol, now_ts):
            logger.info(f"[老五] {signal.symbol} 不在重点关注列表，跳过")
            return

        dot = "🟢" if signal.direction == "上涨机会" else "🔴"
        level_str = _LEVEL_DISPLAY.get(signal.level, signal.level)

        lines = [
            f"{dot} 【重点关注】{signal.symbol} 老五信号",
            f"体系: {signal.system} | 方向: {signal.direction} | 级别: {level_str}",
        ]
        if signal.interval:
            lines.append(f"周期: {signal.interval}")
        if signal.price:
            lines.append(f"价格: {signal.price}")
        if signal.description:
            lines.append(f"描述: {signal.description}")
        if signal.suggestion:
            lines.append(f"建议: {signal.suggestion}")

        snapshot = self._build_obos_snapshot(signal.symbol, now_ts)
        if snapshot:
            lines.append("")
            lines.append("📊 当前系统状态:")
            lines.extend(snapshot)

        msg = "\n".join(lines)
        chart_iv = signal.interval or "1h"
        chart_title = f"{signal.symbol}  {chart_iv}【老五{signal.system}】{signal.direction} {signal.level}"

        logger.warning(f"[老五推送] {signal.symbol} {signal.system} {signal.level} {signal.direction}")
        await send_with_chart(
            tg=self.tg,
            msg=msg,
            chat_id=settings.TG_CHAT_ID,
            topic_id=settings.TG_TOPIC_FOCUS,
            symbol=signal.symbol,
            max_iv=chart_iv,
            chart_title=chart_title,
            trend_annotations_provider=lambda iv, sym=signal.symbol: self.state.get_recent_trend_labels(sym, iv),
        )

    def _build_obos_snapshot(self, symbol: str, now_ts: float) -> list[str]:
        allowed = get_universe().get(symbol)
        if not allowed:
            return []

        lines = []
        for iv in _SNAPSHOT_INTERVALS:
            if iv not in allowed:
                continue
            rec = self.state.cache.get((symbol, iv))
            if rec is None:
                continue

            parts = []
            for side, is_in_attr in [(Side.OVERBOUGHT, "in_overbought"), (Side.OVERSOLD, "in_oversold")]:
                if getattr(rec, is_in_attr):
                    parts.append("超买🔴 IN" if side == Side.OVERBOUGHT else "超卖🟢 IN")
                elif self.state.is_warm(symbol, iv, side, now_ts):
                    parts.append("超买🔴 WARM" if side == Side.OVERBOUGHT else "超卖🟢 WARM")

            if parts:
                lines.append(f"  {iv}: {' | '.join(parts)}")

        return lines
