import importlib
import os
import sys
from pathlib import Path

import pytest


def _write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _reload_app_config(monkeypatch, tmp_path: Path):
    """
    动态加载/重载 app.config：
    - 通过 monkeypatch 设置工作目录到 tmp_path
    - 确保 .env / config/*.yaml 都从 tmp_path 读取
    """
    monkeypatch.chdir(tmp_path)

    # 如果之前已经 import 过 app.config，需要先卸载，避免缓存影响
    if "app.config" in sys.modules:
        del sys.modules["app.config"]

    import app.config  # noqa: F401
    return importlib.reload(sys.modules["app.config"])


def test_config_loading_success(monkeypatch, tmp_path: Path):
    """
    成功加载：
    - .env 必要字段齐全
    - universe.yaml 正确
    - routing.yaml 正确
    """
    # 1) 写 .env
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "TG_BOT_TOKEN=TEST_TOKEN",
                "TG_CHAT_ID=123456",
                "TG_TOPIC_LONG=100",
                "TG_TOPIC_MID=200",
                "TG_TOPIC_SHORT=300",
                "TG_TOPIC_ULTRA=400",
                "UNIVERSE_PATH=config/universe.yaml",
                "ROUTING_PATH=config/routing.yaml",
            ]
        ),
        encoding="utf-8",
    )

    # 2) 写 universe.yaml
    _write_yaml(
        tmp_path / "config" / "universe.yaml",
        """
symbols:
  BTCUSDT:
    intervals: ["1w", "1D", "4h", "1h", "15m", "3m"]
  ASTERUSDT:
    intervals: ["4h", "1h", "15m", "3m"]
""".lstrip(),
    )

    # 3) 写 routing.yaml
    _write_yaml(
        tmp_path / "config" / "routing.yaml",
        """
max_interval_to_topic:
  1w: long
  1D: mid
  4h: short
  1h: ultra

max_interval_min_allowed:
  1w: 1h
  1D: 15m
  4h: 3m
  1h: 30s
""".lstrip(),
    )

    cfg = _reload_app_config(monkeypatch, tmp_path)

    # settings
    assert cfg.settings.TG_BOT_TOKEN == "TEST_TOKEN"
    assert cfg.settings.TG_CHAT_ID == "123456"  # BaseSettings 默认把 env 当字符串读入，然后 pydantic 处理
    assert cfg.settings.TG_TOPIC_LONG == 100
    assert cfg.settings.TG_TOPIC_MID == 200
    assert cfg.settings.TG_TOPIC_SHORT == 300
    assert cfg.settings.TG_TOPIC_ULTRA == 400


    # universe
    assert "BTCUSDT" in cfg.universe
    assert cfg.universe["BTCUSDT"] == ["1w", "1D", "4h", "1h", "15m", "3m"]

    # routing
    assert cfg.routing_rules["max_interval_to_topic"]["1w"] == "long"
    assert cfg.routing_rules["max_interval_min_allowed"]["1w"] == "1h"


def test_config_missing_env_required_field_fail(monkeypatch, tmp_path: Path):
    """
    .env 缺少必填字段 -> import app.config 时应直接失败
    """
    # 少写 TG_BOT_TOKEN
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "TG_CHAT_ID=123456",
                "TG_TOPIC_LONG=100",
                "TG_TOPIC_MID=200",
                "TG_TOPIC_SHORT=300",
                "TG_TOPIC_ULTRA=400",
                "UNIVERSE_PATH=config/universe.yaml",
                "ROUTING_PATH=config/routing.yaml",
            ]
        ),
        encoding="utf-8",
    )

    _write_yaml(
        tmp_path / "config" / "universe.yaml",
        """
symbols:
  BTCUSDT:
    intervals: ["1w", "1D"]
""".lstrip(),
    )

    _write_yaml(
        tmp_path / "config" / "routing.yaml",
        """
max_interval_to_topic:
  1w: long
max_interval_min_allowed:
  1w: 1h
""".lstrip(),
    )

    monkeypatch.chdir(tmp_path)
    if "app.config" in sys.modules:
        del sys.modules["app.config"]

    with pytest.raises(Exception):
        import app.config  # noqa: F401


def test_universe_file_missing_fail(monkeypatch, tmp_path: Path):
    """
    universe.yaml 路径存在于 env，但文件不存在 -> fail-fast
    """
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "TG_BOT_TOKEN=TEST_TOKEN",
                "TG_CHAT_ID=123456",
                "TG_TOPIC_LONG=100",
                "TG_TOPIC_MID=200",
                "TG_TOPIC_SHORT=300",
                "TG_TOPIC_ULTRA=400",
                "UNIVERSE_PATH=config/universe.yaml",
                "ROUTING_PATH=config/routing.yaml",
            ]
        ),
        encoding="utf-8",
    )

    # routing 存在
    _write_yaml(
        tmp_path / "config" / "routing.yaml",
        """
max_interval_to_topic:
  1w: long
max_interval_min_allowed:
  1w: 1h
""".lstrip(),
    )

    monkeypatch.chdir(tmp_path)
    if "app.config" in sys.modules:
        del sys.modules["app.config"]

    with pytest.raises(FileNotFoundError):
        import app.config  # noqa: F401


def test_universe_missing_symbols_field_fail(monkeypatch, tmp_path: Path):
    """
    universe.yaml 缺少 symbols -> ValueError
    """
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "TG_BOT_TOKEN=TEST_TOKEN",
                "TG_CHAT_ID=123456",
                "TG_TOPIC_LONG=100",
                "TG_TOPIC_MID=200",
                "TG_TOPIC_SHORT=300",
                "TG_TOPIC_ULTRA=400",
                "UNIVERSE_PATH=config/universe.yaml",
                "ROUTING_PATH=config/routing.yaml",
            ]
        ),
        encoding="utf-8",
    )

    _write_yaml(
        tmp_path / "config" / "universe.yaml",
        """
foo: bar
""".lstrip(),
    )

    _write_yaml(
        tmp_path / "config" / "routing.yaml",
        """
max_interval_to_topic:
  1w: long
max_interval_min_allowed:
  1w: 1h
""".lstrip(),
    )

    monkeypatch.chdir(tmp_path)
    if "app.config" in sys.modules:
        del sys.modules["app.config"]

    with pytest.raises(ValueError):
        import app.config  # noqa: F401


def test_routing_missing_required_mapping_fail(monkeypatch, tmp_path: Path):
    """
    routing.yaml 缺少 max_interval_min_allowed 或 max_interval_to_topic -> ValueError
    """
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "TG_BOT_TOKEN=TEST_TOKEN",
                "TG_CHAT_ID=123456",
                "TG_TOPIC_LONG=100",
                "TG_TOPIC_MID=200",
                "TG_TOPIC_SHORT=300",
                "TG_TOPIC_ULTRA=400",
                "UNIVERSE_PATH=config/universe.yaml",
                "ROUTING_PATH=config/routing.yaml",
            ]
        ),
        encoding="utf-8",
    )

    _write_yaml(
        tmp_path / "config" / "universe.yaml",
        """
symbols:
  BTCUSDT:
    intervals: ["1w", "1D"]
""".lstrip(),
    )

    # 故意缺少 max_interval_min_allowed
    _write_yaml(
        tmp_path / "config" / "routing.yaml",
        """
max_interval_to_topic:
  1w: long
""".lstrip(),
    )

    monkeypatch.chdir(tmp_path)
    if "app.config" in sys.modules:
        del sys.modules["app.config"]

    with pytest.raises(ValueError):
        import app.config  # noqa: F401
