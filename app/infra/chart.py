from __future__ import annotations

import asyncio
import io
import logging
import math
import os
from typing import TYPE_CHECKING, Optional

import httpx

if TYPE_CHECKING:
    from ..adapters.tg_client import TelegramClient

from ..config import settings

logger = logging.getLogger(__name__)

# 按 topic_id 隔离的话题锁：同一 topic 的文字+图片串行发送，不同 topic 并发
_topic_locks: dict[int, asyncio.Lock] = {}

BINANCE_FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"

# max_iv → 每天根数（用于计算 fetch_limit 和 display_n）
_CANDLES_PER_DAY: dict[str, int] = {
    "1D": 1,
    "4h": 6,   # 24h / 4h
    "1h": 24,  # 24h / 1h
}

# TradingView symbol → Binance合约实际symbol（命名不一致时补充）
_TV_TO_BINANCE: dict[str, str] = {
    "RAYUSDT": "RAYSOLUSDT",
}

# TradingView symbol → yfinance symbol（Binance无法覆盖的品种）
_TV_TO_YFINANCE: dict[str, str] = {
    "WTI":    "CL=F",
    "XAUUSD": "GC=F",
    "SILVER": "SI=F",
}

# 项目内部 interval → Binance API interval（不一致时补充）
_INTERNAL_TO_BINANCE_IV: dict[str, str] = {
    "1D": "1d",
}


async def _fetch_klines(symbol: str, interval: str, limit: int) -> Optional[list]:
    """从Binance合约REST接口获取K线。symbol不存在（美股等）返回None，出错也返回None。"""
    binance_symbol = _TV_TO_BINANCE.get(symbol.upper(), symbol.upper())
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                BINANCE_FUTURES_KLINES,
                params={"symbol": binance_symbol, "interval": interval, "limit": limit},
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


def _binance_to_df(klines: list) -> "pd.DataFrame":
    """将 Binance K线列表转为标准 OHLCV DataFrame（index=DatetimeIndex UTC）。"""
    import pandas as pd
    df = pd.DataFrame(klines, columns=[
        "Open_time", "Open", "High", "Low", "Close", "Volume",
        "Close_time", "Quote_vol", "Trades", "Taker_base", "Taker_quote", "Ignore",
    ])
    df["Open_time"] = pd.to_datetime(df["Open_time"], unit="ms", utc=True)
    df.set_index("Open_time", inplace=True)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = df[col].astype(float)
    return df[["Open", "High", "Low", "Close", "Volume"]]


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


def _yfinance_fetch_sync(symbol: str, interval: str, limit: int) -> Optional["pd.DataFrame"]:
    """
    同步版 yfinance 拉取，在 executor 中运行。
    interval: "1h" / "4h"（由 1h resample 合成）/ "1D"。

    days_needed 换算逻辑：
      - 1D：1根=1交易日，交易日≈日历天×5/7，所以 limit×(7/5) 个日历天
      - 1h：美股约每交易日6根1h，换算到交易日后再×(7/5)得日历天
      - 4h：先拉 limit×4 根 1h，再 resample，天数同 1h 逻辑
    """
    import yfinance as yf
    import pandas as pd
    import datetime

    yf_symbol = _TV_TO_YFINANCE.get(symbol.upper(), symbol.upper())

    if interval == "1D":
        days_needed = math.ceil(limit * 7 / 5) + 30
        yf_interval = "1d"
        resample_to = None
    elif interval == "4h":
        days_needed = min(math.ceil(limit * 4 / 6 * 7 / 5) + 30, 728)
        yf_interval = "1h"
        resample_to = "4h"
    elif interval == "15m":
        days_needed = min(limit // 26 + 5, 59)  # yfinance 15m 限60天，美股~26根/交易日
        yf_interval = "15m"
        resample_to = None
    elif interval == "3m":
        return None  # yfinance 不支持 3m
    else:  # 1h
        days_needed = min(math.ceil(limit / 6 * 7 / 5) + 30, 728)
        yf_interval = "1h"
        resample_to = None

    start = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=days_needed)

    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(interval=yf_interval, start=start)
    except Exception:
        return None

    if df is None or df.empty:
        return None

    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df.index.name = "Open_time"

    df = df[["Open", "High", "Low", "Close", "Volume"]].copy().astype(float)

    if resample_to:
        df = df.resample(resample_to).agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }).dropna(subset=["Open", "Close"])

    if df.empty:
        return None

    return df.tail(limit)


async def _fetch_klines_yfinance(symbol: str, interval: str, limit: int) -> Optional["pd.DataFrame"]:
    """yfinance 拉取的异步包装，在线程池中执行同步调用。"""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _yfinance_fetch_sync, symbol, interval, limit)
    except Exception:
        logger.warning(f"[Chart] yfinance 获取失败: {symbol}/{interval}", exc_info=True)
        return None


_CJK_FONT_LOADED = False
_cjk_font_prop = None   # FontProperties(fname=...) 供 _draw_chart 直接设到 Text 对象上

_CJK_KEYWORDS = ("noto", "cjk", "wqy", "simhei", "simsun")

def _ensure_cjk_font() -> None:
    global _CJK_FONT_LOADED, _cjk_font_prop
    if _CJK_FONT_LOADED:
        return
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    cjk_path: Optional[str] = None
    cjk_fallback: Optional[str] = None

    # Step 1：从 matplotlib 系统字体扫描结果中查找，优先选 Regular 权重
    try:
        for f in fm.findSystemFonts():
            fl = f.lower()
            if any(k in fl for k in _CJK_KEYWORDS):
                if "regular" in fl:
                    cjk_path = f
                    break
                elif cjk_fallback is None:
                    cjk_fallback = f  # Bold/其他权重作为备选
    except Exception as e:
        logger.warning(f"[Chart] findSystemFonts 失败: {e}")

    if cjk_path is None:
        cjk_path = cjk_fallback

    # Step 2：系统扫描没找到时，尝试已知路径并手动注册
    if cjk_path is None:
        for p in [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ]:
            if os.path.exists(p):
                fm.fontManager.addfont(p)
                cjk_path = p
                break

    plt.rcParams["axes.unicode_minus"] = False
    _CJK_FONT_LOADED = True

    if cjk_path is None:
        logger.warning("[Chart] 未找到任何CJK字体，中文将显示为方块")
        return

    # 存 FontProperties(fname=...) 供 _draw_chart 直接注入到 Text 对象，
    # 绕过 matplotlib 按名称查找字体时可能回落到 DejaVu Sans 的问题
    _cjk_font_prop = fm.FontProperties(fname=cjk_path)
    logger.info(f"[Chart] CJK字体已加载: {cjk_path}")


def _draw_chart(
    symbol: str,
    interval_label: str,
    df: "pd.DataFrame",
    display_n: Optional[int] = None,
    zone_bot: Optional[float] = None,
    zone_top: Optional[float] = None,
    zone_role: Optional[str] = None,
    price_level: Optional[float] = None,
    chart_title: Optional[str] = None,
    price_label: Optional[str] = None,
) -> bytes:
    import mplfinance as mpf
    import matplotlib.pyplot as plt
    _ensure_cjk_font()

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

    # 直接将 CJK 字体注入 suptitle Text 对象，绕过名称查找回落问题
    if _cjk_font_prop is not None:
        for txt in fig.texts:
            txt.set_fontproperties(_cjk_font_prop)
            txt.set_fontsize(21)

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
            0.01, price_level, f" {price_label if price_label else price_level}",
            transform=ax.get_yaxis_transform(),
            color="#ef5350", fontsize=7, va="bottom",
        )

    # 锁定 y 轴到 K 线价格范围，防止 zone/price 值远离时压扁蜡烛图
    y_min = df["Low"].min()
    y_max = df["High"].max()
    margin = (y_max - y_min) * 0.08
    ax.set_ylim(y_min - margin, y_max + margin)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _vstack_pngs(chart_bytes_list: list[bytes]) -> bytes:
    """将多张 PNG 字节垂直拼接为一张图。"""
    from PIL import Image
    images = [Image.open(io.BytesIO(b)).convert("RGB") for b in chart_bytes_list]
    max_w = max(img.width for img in images)
    total_h = sum(img.height for img in images)
    combined = Image.new("RGB", (max_w, total_h), (255, 255, 255))
    y = 0
    for img in images:
        combined.paste(img, (0, y))
        y += img.height
    buf = io.BytesIO()
    combined.save(buf, format="PNG")
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
    price_label: Optional[str] = None,
) -> Optional[bytes]:
    """
    生成带EMA21/55/100/200的K线图（PNG字节）。
    max_iv 须在 _CANDLES_PER_DAY 中定义，否则返回 None。
    数据源：优先 Binance Futures，失败时 fallback 到 yfinance（覆盖美股/大宗商品）。
    任何异常均返回 None，不影响调用方。
    """
    binance_iv = _INTERNAL_TO_BINANCE_IV.get(max_iv, max_iv)

    if max_iv in ("15m", "3m"):
        display_n = settings.CHART_15M_BARS if max_iv == "15m" else settings.CHART_3M_BARS
        fetch_limit = display_n + 200
        label = max_iv.upper()
    else:
        candles_per_day = _CANDLES_PER_DAY.get(max_iv)
        if candles_per_day is None:
            return None
        days = {"1D": settings.CHART_1D_DAYS, "4h": settings.CHART_4H_DAYS}.get(max_iv, settings.CHART_1H_DAYS)
        display_n = days * candles_per_day
        fetch_limit = display_n + 500
        label = f"{max_iv.upper()} · {days}d"

    # 优先 Binance，失败 fallback yfinance
    df = None
    klines = await _fetch_klines(symbol, binance_iv, fetch_limit)
    if klines:
        df = _binance_to_df(klines)
    else:
        logger.info(f"[Chart] Binance 无数据，尝试 yfinance: {symbol}/{max_iv}")
        df = await _fetch_klines_yfinance(symbol, max_iv, fetch_limit)

    if df is None:
        return None

    try:
        return _draw_chart(symbol, label, df, display_n=display_n, zone_bot=zone_bot, zone_top=zone_top, zone_role=zone_role, price_level=price_level, chart_title=chart_title, price_label=price_label)
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
    price_label: Optional[str] = None,
) -> None:
    """生成并发送K线图。所有异常均被吞掉，不影响文字消息。"""
    try:
        photo = await generate_chart(
            symbol, max_iv,
            zone_bot=zone_bot, zone_top=zone_top, zone_role=zone_role,
            price_level=price_level, chart_title=chart_title, price_label=price_label,
        )
        if photo is not None:
            await tg.send_photo(
                chat_id=chat_id,
                photo=photo,
                message_thread_id=message_thread_id,
            )
    except Exception:
        logger.warning(f"[Chart] 发送失败: {symbol}/{max_iv}", exc_info=True)


async def generate_multi_chart(
    symbol: str,
    intervals: list[str],
    zone_bot: Optional[float] = None,
    zone_top: Optional[float] = None,
    zone_role: Optional[str] = None,
    price_level: Optional[float] = None,
    chart_title: Optional[str] = None,
    price_label: Optional[str] = None,
) -> Optional[bytes]:
    """并发生成多个周期的K线图并垂直拼接为一张图。"""
    tasks = [
        generate_chart(
            symbol, iv,
            zone_bot=zone_bot, zone_top=zone_top, zone_role=zone_role,
            price_level=price_level,
            chart_title=f"{chart_title}  [{iv}]" if chart_title else f"{symbol}  {iv}",
            price_label=price_label,
        )
        for iv in intervals
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    chart_bytes = [r for r in results if isinstance(r, bytes) and r]
    if not chart_bytes:
        return None
    if len(chart_bytes) == 1:
        return chart_bytes[0]
    try:
        return _vstack_pngs(chart_bytes)
    except Exception:
        logger.warning(f"[Chart] 多图合并失败: {symbol}", exc_info=True)
        return chart_bytes[0]


def _chart_intervals_for(max_iv: str) -> list[str]:
    """根据信号最大周期决定要发送的3张图的周期组合。"""
    if max_iv == "1D":
        return ["1D", "4h", "1h"]
    if max_iv in ("15m", "3m"):
        return ["1h", "15m", "3m"]
    return ["4h", "1h", "15m"]


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
    price_label: Optional[str] = None,
    chart_ivs: Optional[list] = None,
) -> None:
    """
    在话题锁保护下，顺序发送文字消息和K线图。
    同一 topic_id 的发送串行执行，保证文字和图片之间不被其他事件插入。
    文字发送失败会抛出异常；图片失败静默忽略。
    """
    lock = _topic_locks.setdefault(topic_id, asyncio.Lock())
    async with lock:
        await tg.send_message(chat_id=chat_id, text=msg, message_thread_id=topic_id)
        try:
            photo = await generate_multi_chart(
                symbol, chart_ivs if chart_ivs is not None else _chart_intervals_for(max_iv),
                zone_bot=zone_bot, zone_top=zone_top, zone_role=zone_role,
                price_level=price_level, chart_title=chart_title, price_label=price_label,
            )
            if photo is not None:
                await tg.send_photo(chat_id=chat_id, photo=photo, message_thread_id=topic_id)
        except Exception:
            logger.warning(f"[Chart] 多图发送失败: {symbol}/{max_iv}", exc_info=True)
