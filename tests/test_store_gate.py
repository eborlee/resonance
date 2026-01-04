import time
import pytest


from app.domain.models import Side
from app.infra.store import AppState, IntervalCache


@pytest.fixture
def store():
    # cooldown = 60s, warm_lookback = 2
    return AppState(cooldown_seconds=60, warm_lookback=2)


def test_update_interval_enters_and_exits_oversold(store):
    symbol = "BTCUSDT"
    interval = "1h"
    ob_level = 40
    os_level = -40

    # 初次进入 oversold（IN）
    store.update_interval(symbol, interval, value=-50, ob_level=ob_level, os_level=os_level)
    rec = store.cache[(symbol, interval)]
    assert rec.prev_in_oversold is True
    assert rec.warm_ttl_oversold == 0

    # 离开 IN，进入 WARM
    store.update_interval(symbol, interval, value=-10, ob_level=ob_level, os_level=os_level)
    rec = store.cache[(symbol, interval)]
    assert rec.prev_in_oversold is False
    assert rec.warm_ttl_oversold == 2  # warm_lookback 生效

    # 再离开 IN（但未曾IN过）→ 不触发 warm
    store.update_interval(symbol, interval, value=-20, ob_level=ob_level, os_level=os_level)
    rec = store.cache[(symbol, interval)]
    assert rec.warm_ttl_oversold == 0


def test_tick_warm_ttl(store):
    symbol = "ETHUSDT"
    interval = "4h"
    store.cache[(symbol, interval)] = IntervalCache(
        value=-20,
        updated_ts=time.time(),
        warm_ttl_oversold=2,
        warm_ttl_overbought=1,
        prev_in_oversold=False,
        prev_in_overbought=False,
    )

    store.tick_warm_ttl(symbol, interval, Side.OVERSOLD)
    assert store.cache[(symbol, interval)].warm_ttl_oversold == 1

    store.tick_warm_ttl(symbol, interval, Side.OVERSOLD)
    assert store.cache[(symbol, interval)].warm_ttl_oversold == 0

    # 过了 0 再 tick 也不会变负
    store.tick_warm_ttl(symbol, interval, Side.OVERSOLD)
    assert store.cache[(symbol, interval)].warm_ttl_oversold == 0


def test_should_emit_resonance_gate_behavior(monkeypatch):
    store = AppState(cooldown_seconds=0, warm_lookback=2)
    symbol = "DOGEUSDT"
    side = Side.OVERSOLD
    key = (symbol, side.value)

    base_time = time.time()
    monkeypatch.setattr("time.time", lambda: base_time)

    # 初次 in_count=1，不够门槛
    assert not store.should_emit_resonance(symbol, side, in_count=1, min_resonance=2)
    assert store.gate[key].last_in_count == 1

    # 提升到 2，满足推送条件（首次推）
    assert store.should_emit_resonance(symbol, side, in_count=2, min_resonance=2)
    assert store.gate[key].last_in_count == 2
    last_ts = store.gate[key].last_sent_ts

    # 不变：仍是2，不推
    assert not store.should_emit_resonance(symbol, side, in_count=2, min_resonance=2)

    # 降为 1，不推
    assert not store.should_emit_resonance(symbol, side, in_count=1, min_resonance=2)

    # 升为 3，
    assert store.should_emit_resonance(symbol, side, in_count=3, min_resonance=2)  # ✅ 推
