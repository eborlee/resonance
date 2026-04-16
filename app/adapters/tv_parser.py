from __future__ import annotations

from typing import Any, Dict, List, Tuple, Optional
import time
import re

from ..domain.models import IntervalSignal, TvEvent, ZoneEvent, DivergenceEvent

from datetime import datetime, timezone

def parse_ts(v: Any) -> float:
    if v is None:
        return time.time()

    # 已经是 timestamp
    if isinstance(v, (int, float)):
        return float(v)

    # TradingView / ISO-8601 字符串
    if isinstance(v, str):
        # 处理 Z（UTC）
        if v.endswith("Z"):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(v)
        return dt.timestamp()

    return time.time()


# TradingView interval 常见映射：
# - 你示例里 interval="60" 表示 60分钟 -> 1h
# - 也可能出现 "240" (4h), "15" (15m) 等
# - 有的脚本会用 "D"/"W" 表示日/周
INTERVAL_MAP: Dict[str, str] = {

    # minutes
    "1": "1m",
    "3": "3m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "45": "45m",
    "60": "1h",
    "90": "90m",

    # hours in minutes
    "120": "2h",
    "180": "3h",
    "240": "4h",
    "360": "6h",
    "480": "8h",
    "720": "12h",

    # day/week (常见写法)
    "D": "1D",
    "1D": "1D",
    "W": "1D",
    "1W": "1W",
}

INTERVAL_SECONDS = {
    "30s": 30,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1D": 86400,
    "1W": 604800
}



def normalize_symbol(raw: str) -> str:
    """
    规范化 symbol：
    - 去掉常见后缀，如 ".P"
    - 去掉空白
    - 保持主体不做过度清洗（避免误伤）
    """
    s = (raw or "").strip()

    # 去掉类似 ASTERUSDT.P / BTCUSDT.P 这种后缀
    # 只处理最末尾一个 ".xxx" 结构，且 xxx 全是字母
    s = re.sub(r"\.[A-Za-z]+$", "", s)
    return s


def map_interval(raw: Any) -> Optional[str]:
    """
    将 TradingView 的 interval 字段映射为内部标准字符串：
    - "60" -> "1h"
    - "15" -> "15m"
    - "240" -> "4h"
    - "D" -> "1D"
    """
    if raw is None:
        return None

    s = str(raw).strip()
    if not s:
        return None

    # 直接命中映射表
    if s in INTERVAL_MAP:
        return INTERVAL_MAP[s]

    # 兼容：如果 TV 直接传了 "1h"/"4h"/"15m" 这种
    # 我们允许透传（最小可用）
    if re.fullmatch(r"\d+(m|h|d|w)", s):
        return s

    # 兜底：无法识别则返回 None（上层将丢弃该事件）
    return None


def parse_value(raw: Any) -> Optional[float]:
    """
    解析 value 字段为 float
    """
    if raw is None:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def parse_tv_payload(payload: Dict[str, Any]) -> TvEvent:
    """
    兼容你当前推送示例（单周期单值）：

    {
      "symbol": "ASTERUSDT.P",
      "interval": "60",
      "event": "...",
      "indicator": "...",
      "value": 49.9999,
      "desc": "最新价: 0.7456"
    }

    输出：
    TvEvent(symbol="ASTERUSDT", ts=..., signals=[IntervalSignal(interval="1h", values=(49.99,))])

    注意：
    - 这里 values 只包含当前值 (value,)；余温/历史需要服务端后续补齐
    """
    raw_symbol = payload.get("symbol") or payload.get("ticker") or "UNKNOWN"
    symbol = normalize_symbol(str(raw_symbol))

    # ts = float(payload.get("ts") or time.time())
    raw_ts = payload.get("timenow") or payload.get("ts")
    ts = parse_ts(raw_ts)


    interval = map_interval(payload.get("interval"))
    value = parse_value(payload.get("value"))

    bar_open_ts = payload.get("time")
    bar_open_ts = float(bar_open_ts) if bar_open_ts is not None else None


    signals: List[IntervalSignal] = []

    # 最小可运行：interval/value 任一缺失就当无效事件（signals 为空）
    if interval is not None and value is not None:
        signals.append(IntervalSignal(interval=interval, values=(value,)))

    return TvEvent(symbol=symbol, ts=ts, signals=signals)


def parse_divergence_payload(payload: Dict[str, Any]) -> Optional[DivergenceEvent]:
    """
    解析顶底背离 TV Webhook payload：

    {
      "symbol": "{{ticker}}",
      "interval": "{{interval}}",
      "event": "divergence",
      "indicator": "波段过滤器",
      "value": "",
      "desc": "△触发顶底背离"
    }

    返回 DivergenceEvent，关键字段缺失则返回 None。
    """
    raw_symbol = payload.get("symbol") or payload.get("ticker") or ""
    symbol = normalize_symbol(str(raw_symbol))
    if not symbol:
        return None

    interval = map_interval(payload.get("interval"))
    if interval is None:
        return None

    raw_ts = payload.get("timenow") or payload.get("ts")
    ts = parse_ts(raw_ts)

    return DivergenceEvent(symbol=symbol, interval=interval, ts=ts)


def parse_zone_payload(payload: Dict[str, Any]) -> Optional[ZoneEvent]:
    """
    解析 zone_interaction 类型的 TV Webhook payload：

    {
      "type": "zone_interaction",
      "ticker": "BTCUSDT.P",
      "interval": "60",
      "top": 69320,
      "bot": 69180,
      "role": "R",
      "close": 68370
    }

    返回 ZoneEvent，任意关键字段缺失则返回 None。
    """
    raw_symbol = payload.get("ticker") or payload.get("symbol") or ""
    symbol = normalize_symbol(str(raw_symbol))
    if not symbol:
        return None

    interval = map_interval(payload.get("interval"))
    if interval is None:
        return None

    try:
        top = float(payload["top"])
        bot = float(payload["bot"])
        close = float(payload["close"])
    except (KeyError, TypeError, ValueError):
        return None

    role = str(payload.get("role", "")).strip().upper()
    if role not in ("R", "S"):
        return None

    # Pine Script 的 timenow 是毫秒级整数（13位），转换为秒
    raw_ts = payload.get("ts")
    ts = float(raw_ts) / 1000 if raw_ts is not None else time.time()

    return ZoneEvent(
        symbol=symbol,
        interval=interval,
        top=top,
        bot=bot,
        role=role,
        close=close,
        ts=ts,
    )
