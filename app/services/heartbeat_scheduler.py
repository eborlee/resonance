from __future__ import annotations

import asyncio
import time
import logging

from ..infra.store import AppState
from ..infra.utils import HEARTBEAT_INTERVAL_SECONDS, get_last_bar_close_ts, is_crypto_symbol
from ..config import get_universe

logger = logging.getLogger(__name__)

BUFFER_SECONDS = 30


class HeartbeatScheduler:
    """
    精准唤醒式心跳缺席检测器（crypto 专用）。

    每次唤醒恰好在某根 K 线收盘后 BUFFER_SECONDS，检查该周期所有 crypto 资产
    是否收到了心跳。未收到则认为价格已离开 OB/OS 区，翻转 cache 状态。
    """

    def __init__(self, state: AppState):
        self.state = state

    async def run_forever(self) -> None:
        logger.info("[心跳调度器] 启动，BUFFER=%ds", BUFFER_SECONDS)
        while True:
            try:
                self._scan()
            except Exception:
                logger.error("[心跳调度器] 扫描异常", exc_info=True)
            sleep_secs = max(1.0, self._compute_next_wake() - time.time())
            logger.debug("[心跳调度器] 下次唤醒：%.1fs 后", sleep_secs)
            await asyncio.sleep(sleep_secs)

    def _compute_next_wake(self) -> float:
        """返回下一个需要检查的时刻（unix timestamp）。"""
        now_ts = time.time()
        candidates = []
        for interval_sec in HEARTBEAT_INTERVAL_SECONDS.values():
            bar_close = (int(now_ts) // interval_sec) * interval_sec
            check_at = bar_close + BUFFER_SECONDS
            if check_at <= now_ts:
                check_at += interval_sec  # 当前窗口已过，取下一根 K
            candidates.append(check_at)
        return min(candidates)

    def _scan(self) -> None:
        """检查所有 crypto 资产，对心跳缺席的 (symbol, interval) 翻转状态。"""
        now_ts = time.time()
        universe = get_universe()

        for symbol, allowed_intervals in universe.items():
            if not is_crypto_symbol(symbol):
                continue

            for interval, interval_sec in HEARTBEAT_INTERVAL_SECONDS.items():
                if interval not in allowed_intervals:
                    continue

                bar_close_ts = (int(now_ts) // interval_sec) * interval_sec
                check_at = bar_close_ts + BUFFER_SECONDS

                if now_ts < check_at:
                    continue  # 该周期 buffer 还没到

                key = (symbol, interval)
                if self.state.last_checked_bar.get(key) == bar_close_ts:
                    continue  # 本根 K 已处理过

                last_hb = self.state.last_heartbeat_ts.get(key)
                if last_hb is None or last_hb < bar_close_ts:
                    self.state.clear_zone_on_missed_heartbeat(symbol, interval, bar_close_ts)

                self.state.last_checked_bar[key] = bar_close_ts
