from __future__ import annotations

import logging
from typing import Optional

from ..adapters.claude_client import ClaudeClient, AnalysisUsage

logger = logging.getLogger(__name__)


class ChartAnalysisService:
    def __init__(self, claude: ClaudeClient):
        self._claude = claude

    async def analyze(
        self,
        image_bytes: bytes,
        symbol: str,
        media_type: str = "image/png",
        side: Optional[str] = None,
        intervals: Optional[list[str]] = None,
        extra_context: Optional[str] = None,
    ) -> tuple[str, AnalysisUsage]:
        if side and intervals:
            side_label = "超卖" if side == "OVERSOLD" else "超买"
            context = f"触发信号：{symbol} {'|'.join(intervals)} {side_label}"
        else:
            context = f"标的：{symbol}"
        if extra_context:
            context += f"\n{extra_context}"

        return await self._claude.analyze_chart(
            image_bytes=image_bytes,
            media_type=media_type,
            context=context,
        )
