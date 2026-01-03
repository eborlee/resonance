from __future__ import annotations

from typing import Dict, List
from pydantic_settings import BaseSettings
import yaml


class Settings(BaseSettings):
    # Telegram
    TG_BOT_TOKEN: str
    TG_CHAT_ID: str
    TG_TOPIC_SHORT: int
    TG_TOPIC_LONG: int

    # Thresholds
    OB_LEVEL: float = 40.0
    OS_LEVEL: float = -40.0
    WARM_LOOKBACK: int = 2
    MIN_RESONANCE: int = 2

    # Dedup/Cooldown
    COOLDOWN_SECONDS: int = 1800

    # Universe config
    UNIVERSE_PATH: str = "config/universe.yaml"

    # Logging
    LOG_PATH: str = "data/app.log"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5

    class Config:
        env_file = ".env"
        extra = "ignore"


def load_universe(path: str) -> Dict[str, List[str]]:
    """
    返回: symbol -> allowed intervals
    """
    with open(path, "r", encoding="utf-8") as f:
        obj = yaml.safe_load(f) or {}

    symbols = obj.get("symbols", {}) or {}
    out: Dict[str, List[str]] = {}

    for sym, cfg in symbols.items():
        intervals = cfg.get("intervals", []) if isinstance(cfg, dict) else []
        out[str(sym)] = [str(x) for x in intervals]

    return out


settings = Settings()
universe = load_universe(settings.UNIVERSE_PATH)
