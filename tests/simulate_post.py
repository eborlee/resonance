import requests
import time
from datetime import datetime, timedelta
URL = "http://127.0.0.1:8000/webhook/tradingview"

# 模拟一个 BTCUSDT.P 的超卖信号（4h 周期，值 = -55）
def post_signal(
    symbol: str,
    interval: str,
    value: float,
    ts: datetime,
    desc: str = ""
):
    payload = {
        "symbol": symbol,
        "interval": interval,
        "value": value,
        "timenow": ts.isoformat() + "Z",
        "desc": desc,
    }
    resp = requests.post(URL, json=payload)
    print(f"[{ts.isoformat()}] {symbol} {interval} {value} -> {resp.status_code}")
    try:
        print(resp.json())
    except Exception:
        print(resp.text)


ts = datetime(2026, 1, 8, 3, 0, 0)
post_signal(
    symbol="BTCUSDT.P",
    interval="240",   # 4h
    value=-55,
    ts=ts,
    desc="4h 首次进入超卖"
)


ts += timedelta(minutes=60)

post_signal(
    symbol="BTCUSDT.P",
    interval="60",    # 1h
    value=-48,
    ts=ts,
    desc="1h 进入超卖，形成 4h+1h"
)

ts += timedelta(minutes=10)

post_signal(
    symbol="BTCUSDT.P",
    interval="3",
    value=-52,
    ts=ts,
    desc="1h 持续超卖"
)

ts += timedelta(minutes=10)

post_signal(
    symbol="BTCUSDT.P",
    interval="15",
    value=-52,
    ts=ts,
    desc="15m 触发超卖"
)

ts += timedelta(minutes=10)

post_signal(
    symbol="BTCUSDT.P",
    interval="1D",
    value=-52,
    ts=ts,
    desc="15m 触发超卖"
)