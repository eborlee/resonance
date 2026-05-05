"""
模拟场景：ETHUSDT 4h 区域触及 + 超卖共振

步骤：
1. 推送 4h 超卖信号，让 cache 进入 IN 状态
2. 推送 1h 超卖信号
3. 推送 15m 超卖信号
4. 推送 zone_interaction（4h 支撑区触及）
   → 预期：匹配到 4h/1h/15m 超卖，推送 TG
"""

import requests
import time
from datetime import datetime, timezone

URL = "http://8.209.204.201:80/webhook/tradingview"
SYMBOL = "ETHUSDT.P"
NOW = datetime(2026, 4, 7, 19, 0, 0, tzinfo=timezone.utc)


def post_obos(interval: str, value: float, desc: str = ""):
    payload = {
        "symbol": SYMBOL,
        "interval": interval,
        "value": value,
        "timenow": NOW.isoformat(),
    }
    resp = requests.post(URL, json=payload)
    print(f"[ob/os] {SYMBOL} {interval} value={value} | {desc}")
    print(f"  → {resp.status_code} {resp.json()}\n")


def post_zone(interval: str, top: float, bot: float, role: str, close: float, desc: str = ""):
    payload = {
        "type": "zone_interaction",
        "ticker": SYMBOL,
        "interval": interval,
        "top": top,
        "bot": bot,
        "role": role,
        "close": close,
        "ts": int(NOW.timestamp()),
    }
    resp = requests.post(URL, json=payload)
    print(f"[zone] {SYMBOL} {interval} role={role} top={top} bot={bot} | {desc}")
    print(f"  → {resp.status_code} {resp.json()}\n")


print("=" * 50)
print("Step 1: 推送 ob/os 信号，填充 cache")
print("=" * 50)

post_obos("240", -55.0, "4h 超卖 IN")
time.sleep(0.2)

post_obos("60", -48.0, "1h 超卖 IN")
time.sleep(0.2)

post_obos("15", -42.0, "15m 超卖 IN")
time.sleep(0.2)

print("=" * 50)
print("Step 2: 推送 zone_interaction（4h 支撑区）")
print("=" * 50)

post_zone(
    interval="240",
    top=1850.0,
    bot=1820.0,
    role="S",
    close=1835.0,
    desc="4h 支撑区触及，预期匹配 4h/1h/15m 超卖"
)

print("=" * 50)
print("Step 3: 推送 zone_interaction（1h 阻力区，cache 无超买 → 预期不推送）")
print("=" * 50)

post_zone(
    interval="60",
    top=1920.0,
    bot=1900.0,
    role="R",
    close=1910.0,
    desc="1h 阻力区触及，cache 无超买，预期静默"
)

print("=" * 50)
print("Step 4: 推送 4h 超买信号，再触发 1h 阻力区")
print("=" * 50)

post_obos("240", 55.0, "4h 超买 IN")
time.sleep(0.2)

post_zone(
    interval="60",
    top=1920.0,
    bot=1900.0,
    role="R",
    close=1910.0,
    desc="1h 阻力区触及，4h 超买 IN，预期推送"
)
