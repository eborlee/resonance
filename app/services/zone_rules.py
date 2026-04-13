from __future__ import annotations

from typing import List, Tuple

# (zone_interval, obos_interval)
ZONE_RULES: List[Tuple[str, str]] = [
    ("4h", "4h"),
    ("4h", "1h"),
    # ("4h", "15m"),
    ("1h", "4h"),
    ("1h", "1h"),
    # ("1h", "15m"),
    ("1m", "1m"),   # 调试用，便于快速复现问题
]

# zone 触及周期 → 推送 topic 的 settings 字段名
ZONE_INTERVAL_TO_TOPIC_ATTR: dict[str, str] = {
    "4h": "TG_TOPIC_4H",
    "1h": "TG_TOPIC_1H",
    "1m": "TG_TOPIC_4H",  # 调试用
}
