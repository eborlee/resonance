from datetime import datetime, timezone

def ts_to_utc_str(ts: float) -> str:
    """
    Unix timestamp (seconds) -> UTC datetime string
    e.g. 1767844800.0 -> '2026-01-08 00:00:00 UTC'
    """
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
