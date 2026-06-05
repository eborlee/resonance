from __future__ import annotations

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from ..config import settings
from ..services.prompts import MARKET_BRIEFING_PROMPT_TEMPLATE

if TYPE_CHECKING:
    from ..adapters.claude_client import ClaudeClient
    from ..adapters.tg_client import TelegramClient

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")

_briefing_enabled: bool = True


def set_briefing_enabled(value: bool) -> None:
    global _briefing_enabled
    _briefing_enabled = value


def is_briefing_enabled() -> bool:
    return _briefing_enabled

# ── 固定监控标的 ──────────────────────────────────────────────────────────────
_INDICES = ["SPY", "QQQ", "^DJI", "^GSPC", "^IXIC"]
_FUTURES = ["ES=F", "NQ=F"]
_SENTIMENT = ["^VIX"]
_SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "ARKK"]
_CORE_STOCKS = ["NVDA", "AAPL", "TSLA", "META", "MSFT", "GOOGL", "AMD"]

# ── 自选股：可直接在这里修改，也可通过 .env BRIEFING_CUSTOM_WATCHLIST=PLTR,COIN 覆盖
_DEFAULT_CUSTOM: list[str] = []


def _get_custom_watchlist() -> list[str]:
    raw = settings.BRIEFING_CUSTOM_WATCHLIST.strip()
    if not raw:
        return _DEFAULT_CUSTOM
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _fetch_market_data(extra_symbols: list[str]) -> str:
    """用 yfinance 批量拉取收盘价和涨跌幅，返回格式化字符串。"""
    all_syms = list(dict.fromkeys(
        _INDICES + _FUTURES + _SENTIMENT + _SECTOR_ETFS + _CORE_STOCKS + extra_symbols
    ))

    try:
        raw = yf.download(
            tickers=all_syms,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.warning("yfinance 批量下载失败: %s", e)
        return "（市场数据获取失败）"

    # 兼容单/多 ticker 返回结构
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"]
    else:
        closes = raw[["Close"]].rename(columns={"Close": all_syms[0]})

    def fmt_group(label: str, symbols: list[str]) -> list[str]:
        lines = [f"\n[{label}]"]
        for sym in symbols:
            if sym not in closes.columns:
                lines.append(f"  {sym}: 数据不可用")
                continue
            col = closes[sym].dropna()
            if col.empty:
                lines.append(f"  {sym}: 数据不可用")
                continue
            price = float(col.iloc[-1])
            if len(col) >= 2:
                prev = float(col.iloc[-2])
                pct = (price - prev) / prev * 100
                lines.append(f"  {sym}: {price:.2f} ({pct:+.2f}%)")
            else:
                lines.append(f"  {sym}: {price:.2f}")
        return lines

    rows: list[str] = []
    rows += fmt_group("大盘指数 ETF", _INDICES)
    rows += fmt_group("期货", _FUTURES)
    rows += fmt_group("情绪 (VIX)", _SENTIMENT)
    rows += fmt_group("板块 ETF", _SECTOR_ETFS)
    rows += fmt_group("核心股票", _CORE_STOCKS)
    if extra_symbols:
        rows += fmt_group("自选股", extra_symbols)

    return "\n".join(rows)


class MarketBriefingService:
    def __init__(self, claude: "ClaudeClient", tg: "TelegramClient") -> None:
        self._claude = claude
        self._tg = tg

    async def generate_and_send(self, *, force: bool = False) -> None:
        if not force and not _briefing_enabled:
            logger.info("市场简报已关闭，跳过本次推送")
            return
        logger.info("开始生成市场简报")
        custom = _get_custom_watchlist()

        try:
            loop = asyncio.get_running_loop()
            market_data = await loop.run_in_executor(None, _fetch_market_data, custom)
        except Exception:
            logger.warning("市场数据获取异常，继续生成简报", exc_info=True)
            market_data = "（数据获取失败，请以搜索结果为准）"

        date_str = datetime.datetime.now(EASTERN).strftime("%Y-%m-%d %A")

        custom_section = (
            f"自选股：{' / '.join(custom)}"
            if custom
            else "（未配置自选股，可在 .env 中设置 BRIEFING_CUSTOM_WATCHLIST=TICKER1,TICKER2）"
        )

        prompt = MARKET_BRIEFING_PROMPT_TEMPLATE.format(
            date=date_str,
            market_data=market_data,
            custom_watchlist_section=custom_section,
        )

        try:
            text, usage = await self._claude.generate_market_briefing(prompt)
        except Exception:
            logger.error("Claude 生成简报失败", exc_info=True)
            return

        try:
            await self._tg.send_message(
                chat_id=settings.TG_CHAT_ID,
                text=text,
                message_thread_id=settings.TG_TOPIC_BRIEF,
            )
            logger.info("市场简报推送完成")
        except Exception:
            logger.error("市场简报 TG 发送失败", exc_info=True)
            return

        # token 用量推送到 summary topic
        cost = (
            usage.input_tokens * 3.00
            + usage.output_tokens * 15.00
            + usage.cache_creation_tokens * 3.75
            + usage.cache_read_tokens * 0.30
        ) / 1_000_000
        summary_lines = [
            f"📰 市场简报 Token 用量（{date_str}）",
            f"  输入: {usage.input_tokens:,}",
            f"  输出: {usage.output_tokens:,}",
        ]
        if usage.cache_read_tokens:
            summary_lines.append(f"  缓存命中: {usage.cache_read_tokens:,}")
        if usage.cache_creation_tokens:
            summary_lines.append(f"  缓存写入: {usage.cache_creation_tokens:,}")
        summary_lines.append(f"  估算成本: ${cost:.4f}")
        try:
            await self._tg.send_message(
                chat_id=settings.TG_CHAT_ID,
                text="\n".join(summary_lines),
                message_thread_id=settings.TG_TOPIC_SUMMARY,
            )
        except Exception:
            logger.error("简报 token 用量推送失败", exc_info=True)

    async def run_daily_loop(self) -> None:
        """每天美东时间 08:00 触发一次简报推送。"""
        while True:
            try:
                now = datetime.datetime.now(EASTERN)
                target = now.replace(hour=8, minute=0, second=0, microsecond=0)
                if now >= target:
                    target += datetime.timedelta(days=1)
                delay = (target - now).total_seconds()
                logger.info("市场简报下次推送: %s（%.0f 秒后）", target.strftime("%Y-%m-%d %H:%M %Z"), delay)
                await asyncio.sleep(delay)
                await self.generate_and_send()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("市场简报 loop 异常，下一个 8AM 重试", exc_info=True)
