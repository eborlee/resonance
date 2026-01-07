
from __future__ import annotations

from typing import Dict, List
from pydantic_settings import BaseSettings
import yaml
import os


class Settings(BaseSettings):

    def __init__(self, **data):  # type: ignore[call-arg]
        super().__init__(**data)
    # Telegram
    TG_BOT_TOKEN: str
    TG_CHAT_ID: str

    # 你新的 topic 分层（按最大周期路由）
    TG_TOPIC_LONG: int
    TG_TOPIC_MID: int
    TG_TOPIC_SHORT: int
    TG_TOPIC_ULTRA: int

    # Thresholds
    OB_LEVEL: float = 40.0
    OS_LEVEL: float = -40.0
    WARM_LOOKBACK: int = 2

    # Resonance gate
    MIN_RESONANCE: int = 2
    COOLDOWN_SECONDS: int = 1800

    # Config paths
    UNIVERSE_PATH: str = "config/universe.yaml"
    ROUTING_PATH: str = "config/routing.yaml"

    # Logging
    LOG_PATH: str = "data/app.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5

    INTERVAL_ORDER = [


        
        
        
        
        "30s",  "3m", "5m", "15m", "1h", "4h","1d","1w"
    ]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


def load_universe(path: str) -> Dict[str, List[str]]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"universe config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw or "symbols" not in raw:
        raise ValueError("universe.yaml missing 'symbols' field")

    out: Dict[str, List[str]] = {}
    for symbol, cfg in raw["symbols"].items():
        if not isinstance(cfg, dict):
            continue
        intervals = cfg.get("intervals", [])
        if not intervals:
            continue
        out[str(symbol)] = [str(iv) for iv in intervals]

    if not out:
        raise ValueError("universe.yaml contains no valid symbols")

    return out


def load_routing(path: str) -> Dict[str, Dict[str, str]]:
    """
    加载 routing.yaml

    期望结构：
    max_interval_to_topic:
      1w: long
      1d: mid
      4h: short
      1h: ultra

    max_interval_min_allowed:
      1w: 1h
      1d: 15m
      4h: 3m
      1h: 30s

    返回：
    {
      "max_interval_to_topic": {...},
      "max_interval_min_allowed": {...}
    }
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"routing config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    a = raw.get("max_interval_to_topic", {}) or {}
    b = raw.get("max_interval_min_allowed", {}) or {}

    if not isinstance(a, dict) or not a:
        raise ValueError("routing.yaml missing 'max_interval_to_topic' mapping")
    if not isinstance(b, dict) or not b:
        raise ValueError("routing.yaml missing 'max_interval_min_allowed' mapping")

    return {
        "max_interval_to_topic": {str(k): str(v) for k, v in a.items()},
        "max_interval_min_allowed": {str(k): str(v) for k, v in b.items()},
    }



settings = Settings()
universe = load_universe(settings.UNIVERSE_PATH)
routing_rules = load_routing(settings.ROUTING_PATH)
