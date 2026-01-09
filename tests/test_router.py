import pytest
from app.services.router import max_interval, apply_min_interval_floor


def test_max_interval_basic():
    assert max_interval(["1h", "4h", "15m"]) == "4h"
    assert max_interval(["1w", "3m", "1D"]) == "1w"
    assert max_interval(["30s", "1m"]) == "1m"
    assert max_interval(["30s"]) == "30s"
    assert max_interval([]) is None


def test_max_interval_unknown_periods():
    # 包含未知周期，忽略处理
    assert max_interval(["UNKNOWN", "1h", "15m"]) == "1h"
    assert max_interval(["FOO", "BAR"]) is None


def test_apply_min_interval_floor_basic():
    in_intervals = ["1w", "4h", "1h", "15m", "3m"]

    rules = {
        "1w": "1h",
        "1D": "15m",
        "4h": "3m",
        "1h": "30s",
    }

    # max = 1w, floor = 1h -> 只保留 1h 及以上
    result = apply_min_interval_floor(in_intervals, "1w", rules)
    assert set(result) == {"1w", "4h", "1h"}

    # max = 4h, floor = 3m -> 3m以上都保留
    result = apply_min_interval_floor(in_intervals, "4h", rules)
    assert set(result) == {"1w", "4h", "1h", "15m", "3m"}

    # max = 1h, floor = 30s -> 全部都保留
    result = apply_min_interval_floor(in_intervals, "1h", rules)
    assert set(result) == {"1w", "4h", "1h", "15m", "3m"}

    # max = 1D, floor = 15m -> 15m以下被过滤
    result = apply_min_interval_floor(in_intervals, "1D", rules)
    assert set(result) == {"1w", "4h", "1h", "15m"}

def test_apply_min_interval_floor_missing_rules():
    # 如果规则缺失该 max_iv -> 不过滤
    in_intervals = ["1w", "4h", "15m"]
    rules = {
        "1D": "15m"
    }
    result = apply_min_interval_floor(in_intervals, "1w", rules)
    assert set(result) == {"1w", "4h", "15m"}

def test_apply_min_interval_floor_bad_rank():
    # 如果 floor 本身是非法周期 -> 不过滤
    in_intervals = ["1h", "15m"]
    rules = {
        "1h": "BAD_INTERVAL"
    }
    result = apply_min_interval_floor(in_intervals, "1h", rules)
    assert set(result) == {"1h", "15m"}
