from __future__ import annotations

from collections import defaultdict
from typing import Dict, Optional


class MessageStats:
    """每日消息发送量统计，线程不安全（asyncio 单线程环境下安全）。"""

    def __init__(self) -> None:
        self._counts: Dict[Optional[int], int] = defaultdict(int)

    def record(self, topic_id: Optional[int]) -> None:
        self._counts[topic_id] += 1

    def get_current(self) -> Dict[Optional[int], int]:
        return dict(self._counts)

    def get_and_reset(self) -> Dict[Optional[int], int]:
        result = dict(self._counts)
        self._counts.clear()
        return result
