from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

from ..services.prompts import CHART_ANALYSIS_PROMPT, MARKET_BRIEFING_SYSTEM

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

    async def generate_market_briefing(self, prompt: str, model: str | None = None) -> tuple[str, AnalysisUsage]:
        """调用 Claude 生成市场简报，启用 web_search 工具；失败时降级为无搜索推理。"""
        messages: list = [{"role": "user", "content": prompt}]
        total = AnalysisUsage(0, 0, 0, 0)

        _model = model or self._model
        async def _create(use_search: bool):
            kwargs: dict = {
                "model": _model,
                "max_tokens": 2048,
                "system": MARKET_BRIEFING_SYSTEM,
                "messages": messages,
            }
            if use_search:
                kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
                kwargs["extra_headers"] = {"anthropic-beta": "web-search-2025-03-05"}
            return await self._client.messages.create(**kwargs)

        def _accumulate(response) -> None:
            u = response.usage
            total.input_tokens += u.input_tokens
            total.output_tokens += u.output_tokens
            total.cache_creation_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
            total.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0

        try:
            response = await _create(use_search=True)
            _accumulate(response)
        except Exception as e:
            logger.warning("web_search 工具不可用，降级为普通推理: %s", e)
            response = await _create(use_search=False)
            _accumulate(response)
            return self._extract_text(response), total

        # 处理 tool_use 循环（server-side search 通常直接 end_turn，此为保险兜底）
        for _ in range(8):
            if response.stop_reason != "tool_use":
                break
            messages.append({"role": "assistant", "content": response.content})
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Search unavailable.",
                }
                for block in response.content
                if getattr(block, "type", None) == "tool_use"
            ]
            if not tool_results:
                break
            messages.append({"role": "user", "content": tool_results})
            try:
                response = await _create(use_search=True)
                _accumulate(response)
            except Exception:
                break

        return self._extract_text(response), total

    @staticmethod
    def _extract_text(response) -> str:
        parts = [
            block.text
            for block in response.content
            if getattr(block, "type", None) == "text" and block.text
        ]
        return "\n".join(parts) if parts else "简报生成失败，请稍后重试"
