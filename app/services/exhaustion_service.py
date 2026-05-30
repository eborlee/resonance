from __future__ import annotations

import asyncio
import math
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional, TYPE_CHECKING

from ..config import settings
from ..domain.models import Side, TrackingWindow  # Side 也用于 on_push 类型注解
from ..infra.store import AppState
from ..infra.utils import ts_to_utc_str
from ..infra.chart import _fetch_klines, _binance_to_df, _compute_ema, send_with_chart

if TYPE_CHECKING:
    from ..adapters.tg_client import TelegramClient

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 2 * 3600   # 追踪窗口长度
_POLL_INTERVAL  = 180         # 后台轮询间隔（秒）
_3M_CANDLE_SEC  = 180         # 3m K线时长，用于将 open_time 换算为 close_time
_KLINE_LIMIT    = 1500        # EMA200初始化(200) + 稳定期(1260) + 2h窗口(40)，Binance上限


# ─────────────────────────────────────────────────────────────
# 结果对象
# ─────────────────────────────────────────────────────────────

@dataclass
class ExhaustionResult:
    cross_ts: float   # 穿越发生的时间戳（K线收盘时间）
    message: str      # 推送文字
    chart_title: str  # 图表标题


# ─────────────────────────────────────────────────────────────
# 规则抽象基类
# ─────────────────────────────────────────────────────────────

class ExhaustionRule(ABC):
    """
    衰竭检测规则接口。
    实现 check()，返回 ExhaustionResult 表示检测到衰竭，返回 None 表示未触发。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """规则唯一标识，用于注册/注销。"""
        ...

    @abstractmethod
    async def check(self, window: TrackingWindow) -> Optional[ExhaustionResult]:
        """检查窗口是否满足衰竭条件。"""
        ...


# ─────────────────────────────────────────────────────────────
# 内置规则：3m EMA21 穿越 EMA200
# ─────────────────────────────────────────────────────────────

class Ema21CrossEma200Rule(ExhaustionRule):
    """
    超买衰竭：3m EMA21 下穿 EMA200（push_ts 后 2h 内首次出现）
    超卖衰竭：3m EMA21 上穿 EMA200
    """

    @property
    def name(self) -> str:
        return "ema21_cross_ema200_3m"

    async def check(self, window: TrackingWindow) -> Optional[ExhaustionResult]:
        klines = await _fetch_klines(window.symbol, "3m", _KLINE_LIMIT)
        if not klines:
            logger.debug(f"[{self.name}] {window.symbol} 无 3m K线，跳过")
            return None

        # 过滤未收盘的 K 线：Binance kline[6] 是 close_time（毫秒），
        # close_time > now 说明该 K 线尚未收盘，丢弃。
        now_ms = time.time() * 1000
        klines = [k for k in klines if k[6] < now_ms]
        if len(klines) < 401:  # EMA200 初始化(200) + 最低稳定期(200) + 1
            logger.debug(f"[{self.name}] {window.symbol} 已收盘 K 线不足，跳过")
            return None

        df = _binance_to_df(klines)
        closes = df["Close"].tolist()
        open_times = [t.timestamp() for t in df.index]

        ema21  = _compute_ema(closes, 21)
        ema200 = _compute_ema(closes, 200)

        for i in range(1, len(open_times)):
            close_ts = open_times[i] + _3M_CANDLE_SEC  # 该 K 线收盘时间
            if close_ts < window.push_ts:
                continue
            if open_times[i] > window.push_ts + _WINDOW_SECONDS:
                break

            p21, p200 = ema21[i - 1], ema200[i - 1]
            c21, c200 = ema21[i],     ema200[i]

            if any(math.isnan(v) for v in (p21, p200, c21, c200)):
                continue

            if window.side == Side.OVERBOUGHT and p21 >= p200 and c21 < c200:
                return self._make_result(window, close_ts, "下穿", "超买衰竭", "🔴")
            if window.side == Side.OVERSOLD and p21 <= p200 and c21 > c200:
                return self._make_result(window, close_ts, "上穿", "超卖衰竭", "🟢")

        return None

    @staticmethod
    def _make_result(
        window: TrackingWindow,
        cross_ts: float,
        cross_dir: str,
        side_label: str,
        dot: str,
    ) -> ExhaustionResult:
        return ExhaustionResult(
            cross_ts=cross_ts,
            message="\n".join([
                f"{dot} {window.symbol} {side_label}",
                ts_to_utc_str(cross_ts),
                f"3m EMA21 {cross_dir} EMA200",
            ]),
            chart_title=f"{window.symbol}  3m【{side_label}】EMA21{cross_dir}EMA200",
        )


# ─────────────────────────────────────────────────────────────
# 服务主体
# ─────────────────────────────────────────────────────────────

class ExhaustionService:
    """
    后台轮询追踪窗口，依次调用已注册规则，首个命中则推送衰竭信号。
    规则可动态注册/注销，与业务服务解耦。
    """

    def __init__(self, state: AppState, tg: "TelegramClient"):
        self.state = state
        self.tg = tg
        self._rules: list[ExhaustionRule] = []
        self._skip_filters: list[Callable] = []

    def add_skip_filter(self, fn: Callable) -> None:
        """注册跳过函数：fn(**ctx) 返回 True 则不注册追踪窗口。"""
        self._skip_filters.append(fn)

    def on_push(
        self,
        symbol: str,
        side: Side,
        push_ts: float,
        topic_id: int,
        msg_id: Optional[int],
        **ctx,
    ) -> None:
        """由各推送 service 在发送消息后调用，统一决策是否注册追踪窗口。"""
        for f in self._skip_filters:
            if f(**ctx):
                logger.debug(f"[Exhaustion] {symbol} {side} 被 skip_filter 过滤，不追踪")
                return
        self.state.register_tracking_window(symbol, side, push_ts, topic_id, msg_id)

    def register_rule(self, rule: ExhaustionRule) -> None:
        self._rules.append(rule)
        logger.info(f"[Exhaustion] 注册规则: {rule.name}")

    def unregister_rule(self, name: str) -> None:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        if len(self._rules) < before:
            logger.info(f"[Exhaustion] 注销规则: {name}")

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                await self._check_all()
            except Exception:
                logger.warning("[Exhaustion] 轮询异常", exc_info=True)

    async def _check_all(self) -> None:
        now_ts = time.time()
        windows = self.state.get_active_tracking_windows(now_ts)
        if not windows:
            return
        logger.info(f"[Exhaustion] 轮询 {len(windows)} 个追踪窗口")
        await asyncio.gather(
            *[self._check_window(w) for w in windows],
            return_exceptions=True,
        )

    async def _check_window(self, window: TrackingWindow) -> None:
        for rule in self._rules:
            try:
                result = await rule.check(window)
            except Exception:
                logger.warning(f"[Exhaustion] 规则 {rule.name} 异常", exc_info=True)
                continue

            if result is not None:
                self.state.mark_tracking_alerted(window.symbol, window.side)
                logger.warning(
                    f"[Exhaustion] {window.symbol} {window.side.value} "
                    f"规则={rule.name} 穿越@{ts_to_utc_str(result.cross_ts)}"
                )
                await self._send_alert(window, result)
                return  # 首个命中即止

    async def _send_alert(self, window: TrackingWindow, result: ExhaustionResult) -> None:
        await send_with_chart(
            tg=self.tg,
            msg=result.message,
            chat_id=settings.TG_CHAT_ID,
            topic_id=settings.TG_TOPIC_ENTRY,
            symbol=window.symbol,
            max_iv="3m",
            chart_title=result.chart_title,
            reply_to_message_id=window.reply_to_message_id,
        )
