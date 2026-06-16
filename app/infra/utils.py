from datetime import datetime, timezone

def ts_to_utc_str(ts: float) -> str:
    """
    Unix timestamp (seconds) -> UTC datetime string
    e.g. 1767844800.0 -> '2026-01-08 00:00:00 UTC'
    """
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# 调度器监控的周期及对应秒数（仅包含共振组合实际用到的周期）
HEARTBEAT_INTERVAL_SECONDS: dict[str, int] = {
    "15m": 900,
    "1h":  3600,
    "4h":  14400,
    "1D":  86400,
}


def get_last_bar_close_ts(now_ts: float, interval: str) -> float:
    """
    返回最近一根已收盘 K 线的收盘时间戳（UTC 对齐，加密货币通用）。

    例：now=10:17 UTC，interval="15m" → 返回 10:15:00 UTC 的 timestamp
    """
    interval_sec = HEARTBEAT_INTERVAL_SECONDS.get(interval)
    if interval_sec is None:
        raise ValueError(f"Unsupported heartbeat interval: {interval}")
    return (int(now_ts) // interval_sec) * interval_sec


def is_crypto_symbol(symbol: str) -> bool:
    """Crypto 资产以 USDT 结尾，走心跳调度器；美股/大宗商品保留双 alert。"""
    return symbol.endswith("USDT")
