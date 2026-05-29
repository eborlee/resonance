from __future__ import annotations

import asyncio
import datetime
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

# Claude 图表分析（ANTHROPIC_API_KEY 未配置时跳过）
if settings.ANTHROPIC_API_KEY:
    _claude_client = ClaudeClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.CLAUDE_MODEL,
        max_tokens=settings.CLAUDE_MAX_TOKENS,
    )
    register_analysis(ChartAnalysisService(_claude_client))
    register_stats(msg_stats)
else:
    logger.warning("ANTHROPIC_API_KEY 未配置，图表分析功能已禁用")

# Telegram client + 主服务
tg = TelegramClient(bot_token=settings.TG_BOT_TOKEN, stats=msg_stats)

exhaustion_svc = ExhaustionService(state=state, tg=tg)
exhaustion_svc.register_rule(Ema21CrossEma200Rule())
exhaustion_svc.add_skip_filter(
    lambda zone_iv=None, obos_iv=None, **_: zone_iv == "1h" and obos_iv == "15m"
)

svc = ResonanceService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)
zone_svc = ZoneService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)
ema_svc = EmaService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)
divergence_svc = DivergenceService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)
volatile_svc = VolatileService(state=state, tg=tg, exhaustion_svc=exhaustion_svc)


async def daily_summary_loop():
    while True:
        now = datetime.datetime.now(datetime.timezone.utc)
        next_midnight = (now + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        await asyncio.sleep((next_midnight - now).total_seconds())

        counts, tokens = msg_stats.get_and_reset()
        if not counts and tokens.analysis_count == 0:
            continue

        topic_names = settings.topic_name_map()
        date_str = now.strftime("%Y-%m-%d")
        lines = [f"📊 {date_str} 推送汇总（UTC）"]
        total = 0
        for topic_id, count in sorted(counts.items(), key=lambda x: -(x[1])):
            name = topic_names.get(topic_id, f"Topic#{topic_id}")
            lines.append(f"  {name}: {count} 条")
            total += count
        lines.append(f"  ————")
        lines.append(f"  合计: {total} 条")

        if tokens.analysis_count > 0:
            cost = (
                tokens.input_tokens * 3.00
                + tokens.output_tokens * 15.00
                + tokens.cache_creation_tokens * 3.75
                + tokens.cache_read_tokens * 0.30
            ) / 1_000_000
            lines.append("")
            lines.append("🤖 AI分析用量")
            lines.append(f"  分析次数: {tokens.analysis_count}")
            lines.append(f"  输入tokens: {tokens.input_tokens:,}")
            lines.append(f"  输出tokens: {tokens.output_tokens:,}")
            if tokens.cache_read_tokens:
                lines.append(f"  缓存命中: {tokens.cache_read_tokens:,}")
            if tokens.cache_creation_tokens:
                lines.append(f"  缓存写入: {tokens.cache_creation_tokens:,}")
            lines.append(f"  估算成本: ${cost:.4f}")

        try:
            await tg.send_message(
                chat_id=settings.TG_CHAT_ID,
                text="\n".join(lines),
                message_thread_id=settings.TG_TOPIC_SUMMARY,
            )
        except Exception:
            logger.error("daily_summary 发送失败", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(
        polling_loop(state=state, tg=tg, owner_chat_id=settings.TG_OWNER_CHAT_ID, stats=msg_stats)
    )
    summary_task = asyncio.create_task(daily_summary_loop())
    exhaustion_task = asyncio.create_task(exhaustion_svc.run_forever())
    yield
    task.cancel()
    summary_task.cancel()
    exhaustion_task.cancel()
    for t in (task, summary_task, exhaustion_task):
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