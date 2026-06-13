from __future__ import annotations

from typing import List, Tuple

# (zone_interval, obos_interval)
ZONE_RULES: List[Tuple[str, str]] = [
    ("1D", "1D"),
    ("1D", "4h"),
    ("1D", "1h"),
    ("4h", "4h"),
    ("4h", "1h"),
    # ("4h", "15m"),
    # ("1h", "4h"),
    ("1h", "1h"),
    # ("1h", "15m"),
    ("1m", "1m"),   # 调试用，便于快速复现问题
]

# zone 触及周期 → 推送 topic 的 settings 字段名（默认路由）
ZONE_INTERVAL_TO_TOPIC_ATTR: dict[str, str] = {
    "1D": "TG_TOPIC_DAY",
    "4h": "TG_TOPIC_4H",
    "1h": "TG_TOPIC_1H",
    "1m": "TG_TOPIC_4H",  # 调试用
}

# per-rule 覆盖：优先级高于 ZONE_INTERVAL_TO_TOPIC_ATTR
# skip_main=True 表示该规则不走 main topic 转发
ZONE_RULE_OVERRIDES: dict[tuple, dict] = {
    # ("1h", "15m"): {"topic_attr": "TG_TOPIC_15MIN", "cooldown": 2 * 3600, "skip_main": True},
}

# (ema200_interval, obos_interval)
EMA200_RULES: List[Tuple[str, str]] = [
    ("1D", "1D"),
    ("1D", "4h"),
    ("1D", "1h"),
    ("4h", "4h"),
    ("4h", "1h"),
    # ("1h", "4h"),
    ("1h", "1h"),
]

EMA200_INTERVAL_TO_TOPIC_ATTR: dict[str, str] = {
    "1D": "TG_TOPIC_DAY",
    "4h": "TG_TOPIC_4H",
    "1h": "TG_TOPIC_1H",
}
