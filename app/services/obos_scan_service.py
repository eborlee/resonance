from __future__ import annotations

import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo
from typing import TYPE_CHECKING

from ..config import settings, get_universe

if TYPE_CHECKING:
    from ..infra.store import AppState
    from ..adapters.tg_client import TelegramClient

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_INTERVALS = ["1D", "4h", "1h", "15m"]


def _next_et_4h_boundary() -> datetime.datetime:
    """返回下一个美东 0/4/8/12/16/20 整点的 aware datetime。"""
    now_et = datetime.datetime.now(_ET)
    next_hour = ((now_et.hour // 4) + 1) * 4
    if next_hour >= 24:
        return (now_et + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    return now_et.replace(hour=next_hour, minute=0, second=0, microsecond=0)


def build_scan_text(state: "AppState") -> str:
    uni = get_universe()
    lines = ["📊 超买/超卖快照（1D-15m）"]
    any_result = False
    for iv in _INTERVALS:
        ob_in, os_in = [], []
        for symbol, allowed_ivs in sorted(uni.items()):
            if iv not in allowed_ivs:
                continue
            rec = state.cache.get((symbol, iv))
            if rec is None:
                continue
            if rec.in_overbought:
                ob_in.append(symbol.replace("USDT", ""))
            if rec.in_oversold:
                os_in.append(symbol.replace("USDT", ""))
        if not (ob_in or os_in):
            continue
        any_result = True
        lines.append(f"\n【{iv}】")
        if ob_in:
            lines.append(f"  🔴 超买: {' '.join(ob_in)}")
        if os_in:
            lines.append(f"  🟢 超卖: {' '.join(os_in)}")
    if not any_result:
        lines.append("  暂无标的处于超买/超卖区域")
    return "\n".join(lines)


class ObosScanService:
    def __init__(self, state: "AppState", tg: "TelegramClient") -> None:
        self.state = state
        self.tg = tg

    async def run_loop(self) -> None:
        """每隔4h（美东 0/4/8/12/16/20 整点）向 summary 频道发超买超卖快照。"""
        while True:
            next_dt = _next_et_4h_boundary()
            sleep_secs = (next_dt - datetime.datetime.now(_ET)).total_seconds()
            await asyncio.sleep(sleep_secs)
            try:
                text = build_scan_text(self.state)
                await self.tg.send_message(
                    chat_id=settings.TG_CHAT_ID,
                    text=text,
                    message_thread_id=settings.TG_TOPIC_SUMMARY,
                )
            except Exception:
                logger.error("ObosScanService 发送失败", exc_info=True)
