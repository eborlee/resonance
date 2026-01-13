from datetime import datetime, timedelta
import requests

URL = "http://127.0.0.1:8000/webhook/tradingview"

def post(symbol, interval, value, ts, desc):
    payload = {
        "symbol": symbol,
        "interval": interval,
        "value": value,
        "timenow": ts.isoformat() + "Z",
        "desc": desc
    }
    resp = requests.post(URL, json=payload)
    print(f"[{ts}] {interval} {value} -> {desc}")
    try:
        print(resp.json())
    except:
        print(resp.text)

symbol = "BTCUSDT"
t0 = datetime(2026, 1, 8, 0, 0, 0)

post(symbol, "240", -55, t0, "Step1: 4h 超卖（OUT→IN）❌")
t1 = t0 + timedelta(minutes=240)
post(symbol, "60", -58, t1, "Step2: 1h 超卖（OUT→IN）✅ 组合 240+60")

t2 = t1 + timedelta(minutes=60)
post(symbol, "15", -52, t2, "Step3: 15min 超卖（OUT→IN）✅ 组合 240+60+15, 60+15")

t3 = t2 + timedelta(minutes=15)
post(symbol, "3", -50, t3, "Step4: 3min 超卖（OUT→IN）✅ 最大组合 240+60+15+3")

t4 = t3 + timedelta(minutes=3)
post(symbol, "3", -20, t4, "Step5: 3min 离开超卖（IN→WARM）❌")

t5 = t4 + timedelta(minutes=30)
post(symbol, "15", -15, t5, "Step6: 15min 离开超卖（IN→WARM）❌")

t6 = t5 + timedelta(minutes=120)
post(symbol, "60", -10, t6, "Step7: 1h 离开超卖（IN→WARM）❌")

t7 = t6 + timedelta(minutes=240)
post(symbol, "240", -15, t7, "Step8: 4h 离开超卖（IN→WARM）❌")

t8 = t7 + timedelta(minutes=240)
post(symbol, "240", -12, t8, "Step9: 4h 还是warm❌")

t9 = t8 + timedelta(minutes=240)
post(symbol, "240", -23, t9, "Step9: 4h 超过warm变OUT（WARM→OUT）❌ 组合失效")

t10 = t9 + timedelta(minutes=240)
post(symbol, "240", -41, t10, "Step19: 4h 4h 重新超卖❌ 组合失效")

t11 = t10 + timedelta(minutes=60)
post(symbol, "60", -43, t11, "Step19: 1h 重新超卖✅ 4h+1h")

# t10 = t9 + timedelta(minutes=3)
# post(symbol, "3", -55, t10, "Step10: 3min 再次进入超卖（WARM→IN）✅ 组合 60+15+3")
