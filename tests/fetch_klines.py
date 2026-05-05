"""
从 Binance 合约接口获取 K 线数据，模拟 chart.py 的 _fetch_klines 行为。
用途：验证指定 symbol 能否正常拉到数据，以及实际价格区间。

用法：
    python tests/fetch_klines.py
    python tests/fetch_klines.py BTCUSDT 4h 10
"""

import sys
import httpx

BINANCE_FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"

TV_TO_BINANCE = {
    "RAYUSDT": "RAYSOLUSDT",
}

def fetch(tv_symbol: str, interval: str = "1h", days: int = 3) -> None:
    candles_per_day = {"4h": 6, "1h": 24}.get(interval)
    if candles_per_day is None:
        print(f"不支持的周期: {interval}，只支持 4h / 1h")
        return

    display_n = days * candles_per_day
    fetch_limit = display_n + 200

    binance_symbol = TV_TO_BINANCE.get(tv_symbol.upper(), tv_symbol.upper())
    print(f"TV symbol  : {tv_symbol.upper()}")
    print(f"Binance sym: {binance_symbol}")
    print(f"interval   : {interval}，获取 {fetch_limit} 根（display {display_n} + warmup 200）")
    print("-" * 50)

    r = httpx.get(
        BINANCE_FUTURES_KLINES,
        params={"symbol": binance_symbol, "interval": interval, "limit": fetch_limit},
        timeout=10.0,
    )
    print(f"HTTP {r.status_code}")

    if r.status_code in (400, 404):
        print(f"Binance 无此合约品种: {binance_symbol}")
        print(r.text)
        return
    if r.status_code == 451:
        print("地区限制 (451)")
        return
    r.raise_for_status()

    data = r.json()
    if not isinstance(data, list) or len(data) == 0:
        print("返回数据为空")
        return

    closes = [float(k[4]) for k in data]
    highs  = [float(k[2]) for k in data]
    lows   = [float(k[3]) for k in data]

    print(f"共 {len(data)} 根 K 线")
    print(f"最早一根开盘时间 (ms): {data[0][0]}")
    print(f"最新一根开盘时间 (ms): {data[-1][0]}")
    print()
    print(f"Close 范围: {min(closes):.6g} ~ {max(closes):.6g}")
    print(f"High  范围: {min(highs):.6g}  ~ {max(highs):.6g}")
    print(f"Low   范围: {min(lows):.6g}  ~ {max(lows):.6g}")
    print()
    print("最近 5 根收盘价:")
    for k in data[-5:]:
        print(f"  {k[0]}  close={float(k[4]):.6g}")

if __name__ == "__main__":
    tv_sym  = sys.argv[1] if len(sys.argv) > 1 else "RAYUSDT"
    iv      = sys.argv[2] if len(sys.argv) > 2 else "1h"
    days    = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    fetch(tv_sym, iv, days)
