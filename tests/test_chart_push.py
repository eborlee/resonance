"""
测试：Binance K线拉取 + Zone+OBOS 事件 TG 推送（含K线图）

运行方式（项目根目录）：
    python -m tests.test_chart_push

分两部分：
1. 直接调用 chart 模块，验证 Binance 数据拉取和画图是否正常，图片保存到 /tmp/
2. 通过 HTTP 触发 webhook，让服务端走完整流程（文字 + 图片 → TG）
"""

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────
WEBHOOK_URL = "http://8.209.204.201:80/webhook/tradingview"
SYMBOL      = "BTCUSDT"          # 合约品种，Binance 一定有
SYMBOL_TV   = "BTCUSDT.P"        # TV 推送格式
NOW         = datetime.now(tz=timezone.utc)
NOW_ISO     = NOW.isoformat()

# Zone 参数（当前 BTC 大概价位，测试用，不必精确）
ZONE_TOP  = 96000.0
ZONE_BOT  = 94500.0
ZONE_ROLE = "S"   # 支撑区


# ══════════════════════════════════════════════
# Part 1：本地验证 Binance 拉取 + 画图
# ══════════════════════════════════════════════

async def test_fetch_and_draw():
    print("\n" + "=" * 55)
    print("Part 1: 直接测试 Binance K线拉取 + 本地画图")
    print("=" * 55)

    # 动态 import（避免项目包路径问题时在此处报错）
    from app.infra.chart import _fetch_klines, _draw_chart

    for max_iv, binance_iv, limit, days in [
        ("4h", "4h",  60, 10),
        ("1h", "1h",  72,  3),
    ]:
        print(f"\n[{max_iv}] 拉取 {SYMBOL} {binance_iv} 最近 {days} 天（{limit} 根）…")
        klines = await _fetch_klines(SYMBOL, binance_iv, limit)

        if not klines:
            print(f"  ✗ 拉取失败，检查网络或 symbol")
            continue

        print(f"  ✓ 拉取到 {len(klines)} 根 K线")
        first_ts = datetime.fromtimestamp(klines[0][0]  / 1000, tz=timezone.utc)
        last_ts  = datetime.fromtimestamp(klines[-1][0] / 1000, tz=timezone.utc)
        print(f"    时间范围: {first_ts:%Y-%m-%d %H:%M} → {last_ts:%Y-%m-%d %H:%M} UTC")
        print(f"    最新收盘: {float(klines[-1][4]):,.2f}")

        # 画图（zone 4h 带区间，1h 带价位线）
        label = f"{binance_iv.upper()} · {days}d"
        if max_iv == "4h":
            png = _draw_chart(SYMBOL, label, klines, zone_bot=ZONE_BOT, zone_top=ZONE_TOP, zone_role=ZONE_ROLE)
        else:
            mid_price = (float(klines[-1][4]))
            png = _draw_chart(SYMBOL, label, klines, price_level=mid_price * 0.995)

        out = Path(f"/tmp/chart_test_{max_iv}.png")
        out.write_bytes(png)
        print(f"  ✓ 图片已保存: {out}  ({len(png)//1024} KB)")


# ══════════════════════════════════════════════
# Part 2：通过 webhook 触发完整推送流程
# ══════════════════════════════════════════════

def post(payload: dict, label: str):
    try:
        r = httpx.post(WEBHOOK_URL, json=payload, timeout=15)
        status = "✓" if r.status_code == 200 else "✗"
        print(f"  {status} {label} → HTTP {r.status_code}")
    except Exception as e:
        print(f"  ✗ {label} → 请求失败: {e}")
    time.sleep(0.3)


def test_webhook_push():
    print("\n" + "=" * 55)
    print("Part 2: webhook 端到端推送测试（文字 + K线图 → TG）")
    print("=" * 55)

    # Step 1：推超卖信号，让 cache 进入 IN 状态
    print("\n[Step 1] 推入超卖 ob/os 信号…")
    post({
        "symbol": SYMBOL_TV, "interval": "240",
        "value": -55.0, "timenow": NOW_ISO,
    }, "4h 超卖 IN")

    post({
        "symbol": SYMBOL_TV, "interval": "60",
        "value": -48.0, "timenow": NOW_ISO,
    }, "1h 超卖 IN")

    # Step 2：触发 zone 事件，预期推送文字 + 图片
    print("\n[Step 2] 触发 zone_interaction（4h 支撑区 + 超卖）…")
    post({
        "type": "zone_interaction",
        "ticker": SYMBOL_TV,
        "interval": "240",
        "top": ZONE_TOP,
        "bot": ZONE_BOT,
        "role": ZONE_ROLE,
        "close": ZONE_BOT + 500,
        "ts": int(NOW.timestamp() * 1000),
    }, f"zone 4h S区 {ZONE_BOT}~{ZONE_TOP}")

    print("\n预期结果：TG 4h频道收到文字消息 + BTCUSDT 4h K线图（含支撑区绿色区间）")


# ══════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════

async def main():
    await test_fetch_and_draw()
    test_webhook_push()
    print("\n✅ 测试完成\n")


if __name__ == "__main__":
    asyncio.run(main())
