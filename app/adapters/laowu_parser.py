from __future__ import annotations

import re
import time
from typing import Optional

from ..domain.models import LaoWuSignal


def parse_laowu_message(text: str, ts: Optional[float] = None) -> Optional[LaoWuSignal]:
    """解析老五 bot 推送的信号消息，返回 LaoWuSignal 或 None（无法识别）。"""
    if ts is None:
        ts = time.time()

    symbol_m = re.search(r'标的[：:]\s*(\w+)', text)
    price_m = re.search(r'价格[：:]\s*([\d.]+)', text)
    interval_m = re.search(r'周期[：:]\s*(\w+)', text)
    system_m = re.search(r'体系[：:]\s*(决策层|分析层)', text)
    direction_m = re.search(r'方向[：:]\s*(上涨机会|下跌机会)', text)
    level_m = re.search(r'级别[：:]\s*([1-5]级|[SABCD]级)', text)
    desc_m = re.search(r'描述[：:]\s*(.+)', text)
    sugg_m = re.search(r'建议[：:]\s*(.+)', text)

    if not all([symbol_m, system_m, direction_m, level_m]):
        return None

    return LaoWuSignal(
        symbol=symbol_m.group(1).upper(),
        price=float(price_m.group(1)) if price_m else None,
        interval=interval_m.group(1) if interval_m else None,
        system=system_m.group(1),
        direction=direction_m.group(1),
        level=level_m.group(1),
        description=desc_m.group(1).strip() if desc_m else None,
        suggestion=sugg_m.group(1).strip() if sugg_m else None,
        ts=ts,
    )
