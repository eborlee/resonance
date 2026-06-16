from __future__ import annotations

import asyncio
import datetime
import logging
from typing import TYPE_CHECKING

from ..config import settings

if TYPE_CHECKING:
    from ..infra.stats import MessageStats
    from ..adapters.tg_client import TelegramClient

logger = logging.getLogger(__name__)


class DailySummaryService:
    def __init__(self, stats: "MessageStats", tg: "TelegramClient") -> None:
        self.stats = stats
        self.tg = tg

    async def run_loop(self) -> None:
        """每天 UTC 00:00 向 summary 频道发推送量汇总。"""
        while True:
            now = datetime.datetime.now(datetime.timezone.utc)
            next_midnight = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            await asyncio.sleep((next_midnight - now).total_seconds())

            counts, tokens = self.stats.get_and_reset()
            if not counts and tokens.analysis_count == 0:
                continue

            topic_names = settings.topic_name_map()
            date_str = now.strftime("%Y-%m-%d")
            lines = [f"📊 {date_str} 推送汇总（UTC）"]
            total = 0
            for topic_id, count in sorted(counts.items(), key=lambda x: -(x[1])):
                name = topic_names.get(topic_id, f"Topic#{topic_id}")
                lines.append(f"  {name}: {count} 条")
                total += count
            lines.append(f"  ————")
            lines.append(f"  合计: {total} 条")

            if tokens.analysis_count > 0:
                cost = (
                    tokens.input_tokens * 3.00
                    + tokens.output_tokens * 15.00
                    + tokens.cache_creation_tokens * 3.75
                    + tokens.cache_read_tokens * 0.30
                ) / 1_000_000
                lines.append("")
                lines.append("🤖 AI分析用量")
                lines.append(f"  分析次数: {tokens.analysis_count}")
                lines.append(f"  输入tokens: {tokens.input_tokens:,}")
                lines.append(f"  输出tokens: {tokens.output_tokens:,}")
                if tokens.cache_read_tokens:
                    lines.append(f"  缓存命中: {tokens.cache_read_tokens:,}")
                if tokens.cache_creation_tokens:
                    lines.append(f"  缓存写入: {tokens.cache_creation_tokens:,}")
                lines.append(f"  估算成本: ${cost:.4f}")

            try:
                await self.tg.send_message(
                    chat_id=settings.TG_CHAT_ID,
                    text="\n".join(lines),
                    message_thread_id=settings.TG_TOPIC_SUMMARY,
                )
            except Exception:
                logger.error("DailySummaryService 发送失败", exc_info=True)
