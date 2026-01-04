from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple
import time


@dataclass
class GateRecord:
    """
    记录某个 (symbol, side) 的推送门控状态
    - last_in_count：上一次观测到的 IN 数量
    - last_sent_ts：上一次真正推送的时间戳（用于 cooldown 防抖）
    """
    last_in_count: int = 0
    last_sent_ts: float = 0.0


class ResonanceGateFsm:
    """
    极简 FSM（门控器）：
    只做一件事：决定“要不要推送”。

    触发规则（与你确认的一致）：
    - 只有当 in_count >= min_resonance 且 in_count 相比上次增加 才推送
    - 降级（in_count 下降）不推
    - 不变（in_count 相同）不推
    - cooldown 仅用于防抖（防止短时间多次增加导致刷屏）
    """

    def __init__(self, cooldown_seconds: int):
        self.cooldown_seconds = float(cooldown_seconds)
        self._gate: Dict[Tuple[str, str], GateRecord] = {}

    def should_emit(
        self,
        symbol: str,
        side: str,
        in_count: int,
        min_resonance: int,
        now_ts: float | None = None,
    ) -> bool:
        if now_ts is None:
            now_ts = time.time()

        key = (symbol, side)
        rec = self._gate.get(key)
        if rec is None:
            rec = GateRecord()
            self._gate[key] = rec

        # 未达到共振门槛：更新 last_in_count，但不推送
        if in_count < min_resonance:
            rec.last_in_count = in_count
            return False

        increased = in_count > rec.last_in_count
        in_cooldown = (now_ts - rec.last_sent_ts) < self.cooldown_seconds

        if increased and not in_cooldown:
            rec.last_in_count = in_count
            rec.last_sent_ts = now_ts
            return True

        # 不推送也要更新 last_in_count，保证后续“增加”判断正确
        rec.last_in_count = in_count
        return False
