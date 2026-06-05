from __future__ import annotations

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

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


def _get_custom_watchlist() -> list[str]:
    raw = settings.BRIEFING_CUSTOM_WATCHLIST.strip()
    if not raw:
        return []
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


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

        now_et = datetime.datetime.now(EASTERN)
        date_str = now_et.strftime("%Y-%m-%d %A")
        hour, minute = now_et.hour, now_et.minute

        if hour < 4:
            prev_day = (now_et - datetime.timedelta(days=1)).strftime("%Y-%m-%d %A")
            briefing_type = "盘后"
            briefing_context = (
                f"当前已过午夜，需要回顾的交易日为 {prev_day}（非今日 {date_str[:10]}）。"
                f"请通过网络搜索获取 {prev_day[:10]} 的三大指数涨跌幅、核心个股表现及盘后异动，"
                f"同时展望 {date_str[:10]} 的风险点。"
            )
            search_focus = f"{prev_day[:10]} 美股收盘行情、三大指数涨跌幅、重要个股盘后异动原因"
            market_review_label = f"{prev_day[:10]} 行情回顾"
            market_review_guide = (
                "- 道琼斯 / 纳斯达克 / 标普 500 当日收盘涨跌幅\n"
                "- 主要个股盘后异动\n"
                "- 美债 / 美元指数动向（如有重要变化）"
            )
        elif hour < 9 or (hour == 9 and minute < 30):
            briefing_type = "开盘前"
            briefing_context = "当前为美股开盘前（04:00-09:30），请重点分析昨日收盘行情与今日开盘风险。"
            search_focus = "昨日美股主要新闻、重要个股异动原因、今日开盘前期货走势"
            market_review_label = "隔夜市场回顾"
            market_review_guide = (
                "- 道琼斯 / 纳斯达克 / 标普 500 昨日收盘涨跌幅\n"
                "- 美股期货当前点位及方向\n"
                "- 美债 / 美元指数动向（如有重要变化）"
            )
        elif hour < 16:
            briefing_type = "盘中"
            briefing_context = "当前为美股交易时段，请整合今日盘中实时行情动态。"
            search_focus = "今日美股盘中主要新闻、个股异动原因"
            market_review_label = "今日盘中回顾"
            market_review_guide = (
                "- 道琼斯 / 纳斯达克 / 标普 500 今日盘中涨跌幅\n"
                "- 盘中主要异动及原因\n"
                "- 美债 / 美元指数动向（如有重要变化）"
            )
        else:
            briefing_type = "盘后"
            briefing_context = "当前为美股收盘后，请整合今日全天行情及盘后异动，同时展望明日风险点。"
            search_focus = "今日美股收盘行情、三大指数涨跌幅、重要个股盘后异动原因"
            market_review_label = "今日行情回顾"
            market_review_guide = (
                "- 道琼斯 / 纳斯达克 / 标普 500 今日收盘涨跌幅\n"
                "- 今日主要个股盘后异动\n"
                "- 美债 / 美元指数动向（如有重要变化）"
            )

        custom_section = (
            f"自选股：{' / '.join(custom)}"
            if custom
            else "（未配置自选股，可在 .env 中设置 BRIEFING_CUSTOM_WATCHLIST=TICKER1,TICKER2）"
        )

        prompt = MARKET_BRIEFING_PROMPT_TEMPLATE.format(
            date=date_str,
            briefing_type=briefing_type,
            briefing_context=briefing_context,
            search_focus=search_focus,
            market_review_label=market_review_label,
            market_review_guide=market_review_guide,
            custom_watchlist_section=custom_section,
        )

        try:
            text, usage = await self._claude.generate_market_briefing(prompt, model=settings.BRIEFING_MODEL)
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

        # Haiku 4.5 定价：输入 $0.80/M，输出 $4.00/M，缓存写 $1.00/M，缓存读 $0.08/M
        cost = (
            usage.input_tokens * 0.80
            + usage.output_tokens * 4.00
            + usage.cache_creation_tokens * 1.00
            + usage.cache_read_tokens * 0.08
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
