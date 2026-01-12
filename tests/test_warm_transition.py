import requests
from datetime import datetime, timedelta

URL = "http://127.0.0.1:8000/webhook/tradingview"


def post_signal(
    symbol: str,
    interval: str,
    value: float,
    ts: datetime,
    desc: str = "",
):
    payload = {
        "symbol": symbol,
        "interval": interval,
        "value": value,
        "timenow": ts.isoformat() + "Z",
        "desc": desc,
    }
    resp = requests.post(URL, json=payload)
    print(f"[{ts.isoformat()}] {symbol} {interval} {value}")
    try:
        print(resp.json())
    except Exception:
        print(resp.text)

symbol = "BTCUSDT"
interval = "60"

# --------------------------------------------------
# 测试开始
# --------------------------------------------------

t0 = datetime(2026, 1, 8, 0, 0, 0)

# Step 1：OUT -> IN
post_signal(
    symbol=symbol,
    interval=interval,
    value=-55,
    ts=t0,
    desc="Step1: 首次进入超卖 (OUT -> IN)",
)

# Step 2：IN -> IN（仍在超卖）
t1 = t0 + timedelta(minutes=60)
post_signal(
    symbol=symbol,
    interval=interval,
    value=-60,
    ts=t1,
    desc="Step2: 持续超卖 (IN -> IN)",
)

# Step 3：IN -> WARM（离开超卖，但仍在 warm 窗口内）
t2 = t1 + timedelta(minutes=60)
post_signal(
    symbol=symbol,
    interval=interval,
    value=-30,
    ts=t2,
    desc="Step3: 离开超卖，仍在 warm 窗口 (IN -> WARM)",
)

# Step 4：WARM
t3 = t2 + timedelta(minutes=60)
post_signal(
    symbol=symbol,
    interval=interval,
    value=-25,
    ts=t3,
    desc="Step4: 超过 warm 窗口 (WARM -> OUT)",
)

# Step 5：OUT -> IN（新一轮）
t4 = t3 + timedelta(minutes=60)
post_signal(
    symbol=symbol,
    interval=interval,
    value=-35,
    ts=t4,
    desc="Step5: 再次进入超卖 (OUT -> IN)",
)

t5 = t4 + timedelta(minutes=60)
post_signal(
    symbol=symbol,
    interval=interval,
    value=-35,
    ts=t5,
    desc="Step5: 再次进入超卖 (OUT -> IN)",
)


t6 = t5 + timedelta(minutes=60)
post_signal(
    symbol=symbol,
    interval=interval,
    value=-55,
    ts=t6,
    desc="Step5: 再次进入超卖 (OUT -> IN)",
)
