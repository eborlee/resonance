from __future__ import annotations

import os
from fastapi import FastAPI, Request

from .config import settings
# from .infra.logging import setup_logging
from .infra.store import AppState
from .adapters.tg_client import TelegramClient
from .adapters.tv_parser import parse_tv_payload
from .services.resonance_service import ResonanceService
import logging
from .infra.logger_config import setup_logging

logger = logging.getLogger(__name__)
app = FastAPI()

# 确保日志目录存在（最小可运行）
log_dir = os.path.dirname(settings.LOG_PATH)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)
 
setup_logging(
    log_path=settings.LOG_PATH,
    max_bytes=settings.LOG_MAX_BYTES,
    backup_count=settings.LOG_BACKUP_COUNT,
)

# ✅ 日志校验
for iv in settings.INTERVAL_ORDER:
    if iv not in settings.WARM_K_MAP:
        logging.error(f"warm_k_map 缺少周期配置: {iv}")
        raise ValueError(f"warm_k_map missing key: {iv}")
    if iv not in settings.INTERVAL_SECONDS:
        logging.error(f"interval_seconds 缺少周期配置: {iv}")
        raise ValueError(f"interval_seconds missing key: {iv}")

# 运行期状态（包含 cache + gate）
state = AppState(
    cooldown_seconds=settings.COOLDOWN_SECONDS,
    warm_k_map=settings.WARM_K_MAP,
    interval_seconds=settings.INTERVAL_SECONDS
)

# Telegram client + 主服务
tg = TelegramClient(bot_token=settings.TG_BOT_TOKEN)
svc = ResonanceService(state=state, tg=tg)


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/webhook/tradingview")
async def tradingview_webhook(req: Request):
    payload = await req.json()
    event = parse_tv_payload(payload)

    # parser 可能产生空 signals（无法识别 interval/value），直接 ack，避免 TV 重试
    if not event.signals:
        return {"ok": True, "ignored": True}

    await svc.handle_event(event)
    return {"ok": True}
