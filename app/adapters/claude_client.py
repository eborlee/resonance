from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

from ..services.prompts import CHART_ANALYSIS_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class AnalysisUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int


class ClaudeClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 512):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def analyze_chart(
        self,
        image_bytes: bytes,
        media_type: str = "image/png",
        context: Optional[str] = None,
    ) -> tuple[str, AnalysisUsage]:
        image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

        user_content: list = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data,
                },
            }
        ]
        if context:
            user_content.append({"type": "text", "text": context})

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=[
                {
                    "type": "text",
                    "text": CHART_ANALYSIS_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )

        usage = response.usage
        result_usage = AnalysisUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

        logger.info(
            "Claude分析完成 | input=%d output=%d cache_write=%d cache_read=%d",
            result_usage.input_tokens,
            result_usage.output_tokens,
            result_usage.cache_creation_tokens,
            result_usage.cache_read_tokens,
        )

        return response.content[0].text, result_usage
