from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class TokenStats:
    analysis_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class MessageStats:
    """每日消息发送量与 Claude 用量统计，asyncio 单线程环境下安全。"""

    def __init__(self) -> None:
        self._counts: Dict[Optional[int], int] = defaultdict(int)
        self._tokens: TokenStats = TokenStats()

    def record(self, topic_id: Optional[int]) -> None:
        self._counts[topic_id] += 1

    def record_tokens(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_creation: int = 0,
        cache_read: int = 0,
    ) -> None:
        self._tokens.analysis_count += 1
        self._tokens.input_tokens += input_tokens
        self._tokens.output_tokens += output_tokens
        self._tokens.cache_creation_tokens += cache_creation
        self._tokens.cache_read_tokens += cache_read

    def get_current(self) -> Dict[Optional[int], int]:
        return dict(self._counts)

    def get_estimated_cost(self) -> float:
        t = self._tokens
        return (
            t.input_tokens * 3.00
            + t.output_tokens * 15.00
            + t.cache_creation_tokens * 3.75
            + t.cache_read_tokens * 0.30
        ) / 1_000_000

    def get_token_stats(self) -> TokenStats:
        return TokenStats(
            analysis_count=self._tokens.analysis_count,
            input_tokens=self._tokens.input_tokens,
            output_tokens=self._tokens.output_tokens,
            cache_creation_tokens=self._tokens.cache_creation_tokens,
            cache_read_tokens=self._tokens.cache_read_tokens,
        )

    def get_and_reset(self) -> tuple[Dict[Optional[int], int], TokenStats]:
        counts = dict(self._counts)
        tokens = self.get_token_stats()
        self._counts.clear()
        self._tokens = TokenStats()
        return counts, tokens
