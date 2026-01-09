import pytest
from app.adapters.tv_parser import map_interval, normalize_symbol, parse_tv_payload
from app.domain.models import IntervalSignal, TvEvent


@pytest.mark.parametrize("raw, expected", [
    ("1", "1m"),
    ("3", "3m"),
    ("5", "5m"),
    ("60", "1h"),
    ("240", "4h"),
    ("D", "1D"),
    ("1W", "1w"),
    ("1h", "1h"),
    ("15m", "15m"),
    ("", None),
    (None, None),
    ("UNKNOWN", None),  # 不在 INTERVAL_MAP 且不符合格式
])
def test_map_interval(raw, expected):
    assert map_interval(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("btcusdt", "btcusdt"),
    ("ETHUSDT.P", "ETHUSDT"),
    ("SOLUSDT.Q", "SOLUSDT"),
    ("  dogeusdt.p  ", "dogeusdt"),
    ("TEST.ABC", "TEST"),
    ("ADAUSDT.XYZ", "ADAUSDT"),
    ("foo.bar.baz", "foo.bar"),  # 只去掉最后的
    ("NoDotSymbol", "NoDotSymbol"),
])
def test_normalize_symbol(raw, expected):
    assert normalize_symbol(raw) == expected


def test_parse_tv_payload_valid():
    payload = {
        "symbol": "BTCUSDT.P",
        "interval": "60",
        "value": "49.9",
        "ts": 1234567890.0
    }
    event = parse_tv_payload(payload)
    assert isinstance(event, TvEvent)
    assert event.symbol == "BTCUSDT"
    assert event.ts == 1234567890.0
    assert len(event.signals) == 1
    sig = event.signals[0]
    assert isinstance(sig, IntervalSignal)
    assert sig.interval == "1h"
    assert sig.values == (49.9,)


def test_parse_tv_payload_invalid_interval():
    payload = {
        "symbol": "BTCUSDT",
        "interval": "bad_value",
        "value": 50,
    }
    event = parse_tv_payload(payload)
    assert isinstance(event, TvEvent)
    assert len(event.signals) == 0  # 无效 interval，signals 为空


def test_parse_tv_payload_missing_value():
    payload = {
        "symbol": "BTCUSDT",
        "interval": "60",
    }
    event = parse_tv_payload(payload)
    assert isinstance(event, TvEvent)
    assert len(event.signals) == 0  # 缺少 value
