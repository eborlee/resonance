from __future__ import annotations

import asyncio
import io
import logging
import math
from typing import TYPE_CHECKING, Optional

import httpx

if TYPE_CHECKING:
    from ..adapters.tg_client import TelegramClient

from ..config import settings

logger = logging.getLogger(__name__)

# 按 topic_id 隔离的话题锁：同一 topic 的文字+图片串行发送，不同 topic 并发
_topic_locks: dict[int, asyncio.Lock] = {}

BINANCE_FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"

# max_iv → (binance接口interval字符串, 每天根数)
_CANDLES_PER_DAY: dict[str, int] = {
    "4h": 6,   # 24h / 4h
    "1h": 24,  # 24h / 1h
}


async def _fetch_klines(symbol: str, interval: str, limit: int) -> Optional[list]:
    """从Binance合约REST接口获取K线。symbol不存在（美股等）返回None，出错也返回None。"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                BINANCE_FUTURES_KLINES,
                params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
            )
            if r.status_code in (400, 404):
                logger.info(f"[Chart] Binance无此合约品种: {symbol}")
                return None
            if r.status_code == 451:
                logger.info(f"[Chart] Binance地区限制(451): {symbol}，跳过画图")
                return None
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list) or len(data) == 0:
                return None
            return data
    except Exception:
        logger.warning(f"[Chart] Binance K线获取失败: {symbol}/{interval}", exc_info=True)
        return None


def _compute_ema(prices: list[float], period: int) -> list[float]:
    """计算EMA序列，前period-1个值填nan。"""
    result = [math.nan] * len(prices)
    if len(prices) < period:
        return result
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period
    result[period - 1] = ema
    for i in range(period, len(prices)):
        ema = prices[i] * k + ema * (1.0 - k)
        result[i] = ema
    return result


def _draw_chart(
    symbol: str,
    interval_label: str,
    klines: list,
    display_n: Optional[int] = None,
    zone_bot: Optional[float] = None,
    zone_top: Optional[float] = None,
    zone_role: Optional[str] = None,
    price_level: Optional[float] = None,
    chart_title: Optional[str] = None,
) -> bytes:
    import pandas as pd
    import mplfinance as mpf
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "DejaVu Sans"]

    df = pd.DataFrame(klines, columns=[
        "Open_time", "Open", "High", "Low", "Close", "Volume",
        "Close_time", "Quote_vol", "Trades", "Taker_base", "Taker_quote", "Ignore",
    ])
    df["Open_time"] = pd.to_datetime(df["Open_time"], unit="ms", utc=True)
    df.set_index("Open_time", inplace=True)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = df[col].astype(float)

    closes = df["Close"].tolist()

    ema_configs = [
        (21,  "#F5C518"),  # 亮黄
        (55,  "#D4920A"),  # 深金黄
        (100, "#A0621A"),  # 土黄褐
        (200, "#6B3A10"),  # 深棕
    ]
    add_plots = []
    for period, color in ema_configs:
        vals = _compute_ema(closes, period)
        # 只取最后 display_n 个值，与 df 对齐
        if display_n is not None:
            vals = vals[-display_n:]
        if any(not math.isnan(v) for v in vals):
            add_plots.append(mpf.make_addplot(vals, color=color, width=1.3, alpha=0.7, label=f"EMA{period}"))

    # 只显示最后 display_n 根 K线
    if display_n is not None:
        df = df.iloc[-display_n:]

    title_str = chart_title if chart_title else f"{symbol}  {interval_label}"
    fig, axes = mpf.plot(
        df,
        type="candle",
        style="classic",
        title=f"\n{title_str}",
        addplot=add_plots,
        figsize=(14, 6),
        returnfig=True,
        warn_too_much_data=9999,
    )

    ax = axes[0]

    # 图例（mplfinance returnfig 模式下需手动触发）
    handles, labels = [], []
    for a in axes:
        h, l = a.get_legend_handles_labels()
        handles.extend(h)
        labels.extend(l)
    if handles:
        ax.legend(handles, labels, loc="upper left", fontsize=8, framealpha=0.6)

    # Zone 半透明水平区域
    if zone_bot is not None and zone_top is not None:
        # R=阻力用橙红，S=支撑用绿
        band_color = "#ef5350" if zone_role == "R" else "#26a69a"
        ax.axhspan(zone_bot, zone_top, alpha=0.18, color=band_color, zorder=2)
        # 上下边界虚线
        for price in (zone_bot, zone_top):
            ax.axhline(price, color=band_color, linewidth=0.8, linestyle="--", alpha=0.7, zorder=3)

    # 价格警报关键价位红色虚线
    if price_level is not None:
        ax.axhline(price_level, color="#ef5350", linewidth=1.2, linestyle="--", alpha=0.9, zorder=4)
        ax.text(
            0.01, price_level, f" {price_level:,.4g}",
            transform=ax.get_yaxis_transform(),
            color="#ef5350", fontsize=7, va="bottom",
        )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


async def generate_chart(
    symbol: str,
    max_iv: str,
    zone_bot: Optional[float] = None,
    zone_top: Optional[float] = None,
    zone_role: Optional[str] = None,
    price_level: Optional[float] = None,
    chart_title: Optional[str] = None,
) -> Optional[bytes]:
    """
    生成带EMA21/55/100/200的K线图（PNG字节）。
    仅对max_iv为"4h"或"1h"生效，其他返回None。
    zone_bot/zone_top/zone_role：可选，传入时在图上叠加半透明价格区域。
    任何异常均返回None，不影响调用方。
    """
    candles_per_day = _CANDLES_PER_DAY.get(max_iv)
    if candles_per_day is None:
        return None

    binance_iv = max_iv  # 4h/1h 与 Binance 接口字符串一致
    days = settings.CHART_4H_DAYS if max_iv == "4h" else settings.CHART_1H_DAYS
    display_n = days * candles_per_day
    # 多取 200 根用于 EMA200 预热，保证所有 EMA 都能画出来
    fetch_limit = display_n + 200
    label = f"{binance_iv.upper()} · {days}d"

    klines = await _fetch_klines(symbol, binance_iv, fetch_limit)
    if not klines:
        return None

    try:
        return _draw_chart(symbol, label, klines, display_n=display_n, zone_bot=zone_bot, zone_top=zone_top, zone_role=zone_role, price_level=price_level, chart_title=chart_title)
    except Exception:
        logger.warning(f"[Chart] 绘图失败: {symbol}/{max_iv}", exc_info=True)
        return None


async def _try_send_chart(
    tg: "TelegramClient",
    symbol: str,
    max_iv: str,
    chat_id: str,
    message_thread_id: Optional[int] = None,
    zone_bot: Optional[float] = None,
    zone_top: Optional[float] = None,
    zone_role: Optional[str] = None,
    price_level: Optional[float] = None,
    chart_title: Optional[str] = None,
) -> None:
    """生成并发送K线图。所有异常均被吞掉，不影响文字消息。"""
    try:
        photo = await generate_chart(
            symbol, max_iv,
            zone_bot=zone_bot, zone_top=zone_top, zone_role=zone_role,
            price_level=price_level, chart_title=chart_title,
        )
        if photo is not None:
            await tg.send_photo(
                chat_id=chat_id,
                photo=photo,
                message_thread_id=message_thread_id,
            )
    except Exception:
        logger.warning(f"[Chart] 发送失败: {symbol}/{max_iv}", exc_info=True)


async def send_with_chart(
    tg: "TelegramClient",
    msg: str,
    chat_id: str,
    topic_id: int,
    symbol: str,
    max_iv: str,
    zone_bot: Optional[float] = None,
    zone_top: Optional[float] = None,
    zone_role: Optional[str] = None,
    price_level: Optional[float] = None,
    chart_title: Optional[str] = None,
) -> None:
    """
    在话题锁保护下，顺序发送文字消息和K线图。
    同一 topic_id 的发送串行执行，保证文字和图片之间不被其他事件插入。
    文字发送失败会抛出异常；图片失败静默忽略。
    """
    lock = _topic_locks.setdefault(topic_id, asyncio.Lock())
    async with lock:
        await tg.send_message(chat_id=chat_id, text=msg, message_thread_id=topic_id)
        await _try_send_chart(
            tg, symbol, max_iv, chat_id,
            message_thread_id=topic_id,
            zone_bot=zone_bot,
            zone_top=zone_top,
            zone_role=zone_role,
            price_level=price_level,
            chart_title=chart_title,
        )
