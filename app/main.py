from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request

from .config import settings
from .infra.store import AppState
from .infra.stats import MessageStats
from .infra.chart import register_analysis, register_stats
from .adapters.tg_client import TelegramClient
from .adapters.claude_client import ClaudeClient
from .services.chart_analysis import ChartAnalysisService
from .adapters.tv_parser import parse_tv_payload, parse_zone_payload, parse_ema_payload, parse_divergence_payload, parse_volatile_payload
from .services.resonance_service import ResonanceService
from .services.zone_service import ZoneService
from .services.ema_service import EmaService
from .services.divergence_service import DivergenceService
from .services.volatile_service import VolatileService
from .services.tg_command_handler import polling_loop
from .services.exhaustion_service import ExhaustionService, Ema21CrossEma200Rule
from .services.market_briefing_service import MarketBriefingService
from .services.obos_scan_service import ObosScanService
from .services.daily_summary_service import DailySummaryService
from .services.heartbeat_scheduler import HeartbeatScheduler
import logging
from .infra.logger_config import setup_logging

logger = logging.getLogger(__name__)

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

# 消息统计
msg_stats = MessageStats()

# Telegram client
tg = TelegramClient(bot_token=settings.TG_BOT_TOKEN, stats=msg_stats)

# Claude 图表分析 + 市场简报（ANTHROPIC_API_KEY 未配置时跳过）
_briefing_svc: MarketBriefingService | None = None

if settings.ANTHROPIC_API_KEY:
    _claude_client = ClaudeClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.CLAUDE_MODEL,
        max_tokens=settings.CLAUDE_MAX_TOKENS,
    )
    register_analysis(ChartAnalysisService(_claude_client))
    register_stats(msg_stats)
    _briefing_svc = MarketBriefingService(claude=_claude_client, tg=tg)
else:
    logger.warning("ANTHROPIC_API_KEY 未配置，图表分析和市场简报功能已禁用")

exhaustion_svc = ExhaustionService(state=state, tg=tg)
exhaustion_svc.register_rule(Ema21CrossEma200Rule())
exhaustion_svc.add_skip_filter(
    lambda zone_iv=None, obos_iv=None, **_: zone_iv == "1h" and obos_iv == "15m"
)

obos_scan_svc = ObosScanService(state=state, tg=tg)
daily_summary_svc = DailySummaryService(stats=msg_stats, tg=tg)
heartbeat_scheduler = HeartbeatScheduler(state=state)
svc = ResonanceService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)
zone_svc = ZoneService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)
ema_svc = EmaService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)
divergence_svc = DivergenceService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)
volatile_svc = VolatileService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)



@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(
        polling_loop(state=state, tg=tg, owner_chat_id=settings.TG_OWNER_CHAT_ID, stats=msg_stats, briefing_svc=_briefing_svc)
    )
    summary_task = asyncio.create_task(daily_summary_svc.run_loop())
    scan_task = asyncio.create_task(obos_scan_svc.run_loop())
    exhaustion_task = asyncio.create_task(exhaustion_svc.run_forever())
    heartbeat_task = asyncio.create_task(heartbeat_scheduler.run_forever())
    briefing_task = (
        asyncio.create_task(_briefing_svc.run_daily_loop())
        if _briefing_svc is not None
        else None
    )
    yield
    task.cancel()
    summary_task.cancel()
    scan_task.cancel()
    exhaustion_task.cancel()
    heartbeat_task.cancel()
    if briefing_task is not None:
        briefing_task.cancel()
    for t in (task, summary_task, scan_task, exhaustion_task, heartbeat_task, briefing_task):
        if t is None:
            continue
        try:
            await t
        except asyncio.CancelledError:
            pass

app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/webhook/tradingview")
async def tradingview_webhook(req: Request):
    try:
        payload = await req.json()
    except Exception as e:
        await svc.handle_raw_text_fallback(req, err=e)
        return {"ok": True, "fallback": True}

    # volatile 波动预警单独分发
    if payload.get("event") == "volatile":
        try:
            volatile_event = parse_volatile_payload(payload)
            if volatile_event is None:
                return {"ok": True, "ignored": True}
            await volatile_svc.handle_event(volatile_event)
        except Exception:
            logger.error("volatile_service 处理异常", exc_info=True)
        return {"ok": True}

    # divergence 单独分发
    if payload.get("event") == "divergence":
        try:
            div_event = parse_divergence_payload(payload)
            if div_event is None:
                return {"ok": True, "ignored": True}
            await divergence_svc.handle_event(div_event)
        except Exception:
            logger.error("divergence_service 处理异常", exc_info=True)
        return {"ok": True}

    # ema_interaction / ema200_interaction / ema55_interaction 统一分发
    if payload.get("type") in ("ema_interaction", "ema200_interaction", "ema55_interaction"):
        try:
            ema_event = parse_ema_payload(payload)
            if ema_event is None:
                return {"ok": True, "ignored": True}
            await ema_svc.handle_event(ema_event)
        except Exception:
            logger.error("ema_service 处理异常", exc_info=True)
        return {"ok": True}

    # zone_interaction 单独分发（独立异常处理，不走 fallback）
    if payload.get("type") == "zone_interaction":
        try:
            zone_event = parse_zone_payload(payload)
            if zone_event is None:
                return {"ok": True, "ignored": True}
            await zone_svc.handle_event(zone_event)
        except Exception:
            logger.error("zone_service 处理异常", exc_info=True)
        return {"ok": True}

    try:
        event = parse_tv_payload(payload)

        # parser 可能产生空 signals（无法识别 interval/value），直接 ack，避免 TV 重试
        if not event.signals:
            return {"ok": True, "ignored": True}

        await svc.handle_event(event)
        try:
            await zone_svc.handle_obos_reverse(event)
        except Exception:
            logger.error("zone_svc.handle_obos_reverse 处理异常", exc_info=True)
        return {"ok": True}

    except Exception as e:
        # JSON / parse 失败 -> fallback 到文本
        # 注意：这里不要再做业务判断，交给 svc
        # logger.info("解析失败json")
        await svc.handle_raw_text_fallback(req, err=e)
        return {"ok": True, "fallback": True}