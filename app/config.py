
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
    TG_TOPIC_WEEK: int
    TG_TOPIC_DAY: int
    TG_TOPIC_4H: int
    TG_TOPIC_1H: int
    TG_TOPIC_15MIN: int

    # Thresholds
    OB_LEVEL: float = 40.0
    OS_LEVEL: float = -40.0
    WARM_LOOKBACK: int = 2

    # Resonance gate
    MIN_RESONANCE: int = 2
    COOLDOWN_SECONDS: int = 0

    # Config paths
    UNIVERSE_PATH: str = "config/universe.yaml"
    ROUTING_PATH: str = "config/routing.yaml"

    # Logging
    LOG_PATH: str = "logs/app.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5

    INTERVAL_ORDER: List = [
        "30s",  "3m", "5m", "15m", "1h", "4h","1D","1W"
    ]

    INTERVAL_SECONDS: Dict[str,int] = {
        "30s": 30,
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1D": 86400,
        "1W": 604800,
    }

    WARM_K_MAP: Dict[str, int] = {
        "30s": 2,
        "3m": 2,
        "5m": 2,
        "15m": 2,
        "1h": 2,
        "4h": 2,
        "1D": 2,
        "1W": 2,
    }

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
      1W: long
      1D: mid
      4h: short
      1h: ultra

    max_interval_min_allowed:
      1W: 1h
      1D: 15m
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
def get_universe() -> Dict[str, List[str]]:
    return load_universe(settings.UNIVERSE_PATH)

def get_routing_rules() -> Dict[str, Dict[str, str]]:
    return load_routing(settings.ROUTING_PATH)
universe = load_universe(settings.UNIVERSE_PATH)
routing_rules = load_routing(settings.ROUTING_PATH)
