from __future__ import annotations

from typing import Dict, List, Tuple

# TREND_LABEL_SIDE 定义在 domain/models.py（chart.py 画标注上色也要用，放在 domain 层供 infra/services 共同引用）
from ..domain.models import TREND_LABEL_SIDE  # noqa: F401  (保留此处导入路径，兼容既有调用方)

# (触发interval, obos_interval)：趋势标签触发时反查哪些 OB/OS 周期
TREND_RULES: List[Tuple[str, str]] = [
    ("1D", "1D"), ("1D", "4h"), ("1D", "1h"),
    ("4h", "4h"), ("4h", "1h"),
    ("1h", "1h"), ("1h", "15m"),
    ("15m", "15m"),
]

# 触发周期 → 反查区域/均线触及的时间窗口（5 根触发周期 K 线）
TREND_TOUCH_WINDOW_SECONDS: Dict[str, float] = {
    "1D": 5 * 86400,
    "4h": 5 * 4 * 3600,
    "1h": 5 * 3600,
    "15m": 5 * 15 * 60,
}

# 推送冷冻：与其他推送类型统一，4 小时
TREND_COOLDOWN_SECONDS = 4 * 3600
