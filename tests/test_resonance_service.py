import os
os.environ["TG_BOT_TOKEN"] = "dummy"
os.environ["TG_CHAT_ID"] = "1234"
os.environ["TG_TOPIC_LONG"] = "4321"
os.environ["TG_TOPIC_MID"] = "3333"
os.environ["TG_TOPIC_SHORT"] = "2222"
os.environ["TG_TOPIC_ULTRA"] = "1111"
import os
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.resonance_service import ResonanceService
from app.infra.store import AppState
from app.domain.models import TvEvent, IntervalSignal


# ✅ 提前设置 .env 所需字段
os.environ["TG_BOT_TOKEN"] = "dummy"
os.environ["TG_CHAT_ID"] = "1234"
os.environ["TG_TOPIC_LONG"] = "4321"
os.environ["TG_TOPIC_MID"] = "3333"
os.environ["TG_TOPIC_SHORT"] = "2222"
os.environ["TG_TOPIC_ULTRA"] = "1111"
os.environ["MIN_RESONANCE"] = "2"  # 明确设置共振门槛为2

@pytest.mark.asyncio
async def test_resonance_trigger_push(monkeypatch):
    # ✅ monkeypatch config 中的 universe 与 routing_rules
    monkeypatch.setitem(__import__("app.config").config.universe, "BTCUSDT", ["1h", "4h"])
    monkeypatch.setitem(__import__("app.config").config.routing_rules, "max_interval_min_allowed", {
        "1w": "1h", "1D": "15m", "4h": "3m", "1h": "30s"
    })
    monkeypatch.setitem(__import__("app.config").config.routing_rules, "max_interval_to_topic", {
        "1w": "long", "1D": "mid", "4h": "short", "1h": "ultra"
    })

    # ✅ 构造 mock tg client
    mock_tg = MagicMock()
    mock_tg.send_message = AsyncMock()

    # ✅ 初始化 ResonanceService
    state = AppState(cooldown_seconds=0, warm_lookback=2)
    svc = ResonanceService(state=state, tg=mock_tg)

    # ✅ 构造事件，满足共振2的 oversold
    event = TvEvent(
        symbol="BTCUSDT",
        ts=1234567890.0,
        signals=[
            IntervalSignal(interval="1h", values=(-55.0,)),
            IntervalSignal(interval="4h", values=(-50.1,))
        ]
    )

    # ✅ 执行
    await svc.handle_event(event)

    # ✅ 断言发送调用成功
    assert mock_tg.send_message.called

    args, kwargs = mock_tg.send_message.call_args
    assert kwargs["chat_id"] == "1234"
    assert kwargs["message_thread_id"] == 2222  # max_interval = 1h → ultra
    assert "BTCUSDT" in kwargs["text"]
    assert "IN=2" in kwargs["text"]
    assert "- 1h" in kwargs["text"]
    assert "- 4h" in kwargs["text"]


@pytest.mark.asyncio
async def test_resonance_continuous_behavior(monkeypatch):
    # patch config
    monkeypatch.setitem(__import__("app.config").config.universe, "BTCUSDT", ["1h", "4h", "15m","3m","30s"])
    monkeypatch.setitem(__import__("app.config").config.routing_rules, "max_interval_min_allowed", {
        "1w": "1h", "1D": "15m", "4h": "3m", "1h": "30s"
    })
    monkeypatch.setitem(__import__("app.config").config.routing_rules, "max_interval_to_topic", {
        "1w": "long", "1D": "mid", "4h": "short", "1h": "ultra"
    })

    # 构造共用组件
    state = AppState(cooldown_seconds=0, warm_lookback=2)
    mock_tg = MagicMock()
    mock_tg.send_message = AsyncMock()
    svc = ResonanceService(state=state, tg=mock_tg)

    # ✅ Step 1: 初始 in_count = 2，触发推送
    event1 = TvEvent(
        symbol="BTCUSDT",
        ts=1000.0,
        signals=[
            IntervalSignal("1h", values=(-51.0,)),
            IntervalSignal("4h", values=(-50.1,))
        ]
    )
    await svc.handle_event(event1)
    assert mock_tg.send_message.called
    mock_tg.send_message.reset_mock()

    # ✅ Step 2: in_count 升级为 3，再次推送
    event2 = TvEvent(
        symbol="BTCUSDT",
        ts=1001.0,
        signals=[
            IntervalSignal("1h", values=(-51.0,)),
            IntervalSignal("4h", values=(-50.1,)),
            IntervalSignal("15m", values=(-51.2,))
        ]
    )
    await svc.handle_event(event2)
    assert mock_tg.send_message.called
    mock_tg.send_message.reset_mock()

    # ❌ Step 3: 重复 in_count = 3，签名未变，不推送
    await svc.handle_event(event2)
    assert not mock_tg.send_message.called
    mock_tg.send_message.reset_mock()

    # ❌ Step 4: 降为 in_count = 2，仍旧签名旧，不推送
    await svc.handle_event(event1)
    assert not mock_tg.send_message.called
    mock_tg.send_message.reset_mock()

    # ✅ Step 5: 签名切换（周期不同），新签名再触发
    event5 = TvEvent(
        symbol="BTCUSDT",
        ts=1002.0,
        signals=[
            IntervalSignal("4h", values=(51.5,)),
            IntervalSignal("1h", values=(42.0,)),
            IntervalSignal("30s", values=(42.0,)),
        ]
    )
    await svc.handle_event(event5)
    assert mock_tg.send_message.called