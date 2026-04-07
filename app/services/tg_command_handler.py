from __future__ import annotations

import asyncio
import logging
import time
from typing import List

from ..config import settings, get_universe
from ..domain.models import Side, LevelState
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient

logger = logging.getLogger(__name__)

COMMANDS = """/cache <symbol>  — 各周期 IN/WARM/OUT 状态
/combo <symbol>  — 当前 active 共振组合
/zone <symbol>   — zone 触及 warm 状态
/universe        — 所有监控品种"""


def _handle_cache(state: AppState, symbol: str, now_ts: float) -> str:
    allowed = get_universe().get(symbol.upper())
    if not allowed:
        return f"{symbol} 不在 universe 中"

    symbol = symbol.upper()
    lines = [f"📊 {symbol} 缓存状态"]
    for iv in allowed:
        rec = state.cache.get((symbol, iv))
        if rec is None:
            lines.append(f"  {iv}: 无数据")
            continue

        ob_state = "IN" if rec.in_overbought else (
            "WARM" if state.is_warm(symbol, iv, Side.OVERBOUGHT, now_ts) else "OUT"
        )
        os_state = "IN" if rec.in_oversold else (
            "WARM" if state.is_warm(symbol, iv, Side.OVERSOLD, now_ts) else "OUT"
        )
        lines.append(f"  {iv}: 超买={ob_state} 超卖={os_state} val={rec.value:.2f}")

    return "\n".join(lines)


def _handle_combo(state: AppState, symbol: str) -> str:
    symbol = symbol.upper()
    lines = [f"🔗 {symbol} 共振组合"]
    found = False
    for side in (Side.OVERSOLD, Side.OVERBOUGHT):
        combos = state.latest_combo_state.get((symbol, side), {})
        active = [(combo, meta) for combo, meta in combos.items() if meta.get("active")]
        if not active:
            continue
        found = True
        lines.append(f"  [{side.value}]")
        for combo, meta in active:
            lines.append(f"    {'+'.join(combo)}")
    if not found:
        lines.append("  无 active 组合")
    return "\n".join(lines)


def _handle_zone(state: AppState, symbol: str, now_ts: float) -> str:
    symbol = symbol.upper()
    lines = [f"📍 {symbol} Zone 触及状态"]
    found = False
    for (sym, iv, role), touch_ts in state.zone_touch_cache.items():
        if sym != symbol:
            continue
        found = True
        warm = state.is_zone_warm(sym, iv, role, now_ts)
        elapsed = int(now_ts - touch_ts)
        status = "WARM" if warm else "EXPIRED"
        lines.append(f"  {iv} ({role}): {status} [{elapsed}s 前触及]")
    if not found:
        lines.append("  无触及记录")
    return "\n".join(lines)


def _handle_universe() -> str:
    uni = get_universe()
    lines = [f"🌐 监控品种（{len(uni)}个）"]
    for symbol, intervals in uni.items():
        lines.append(f"  {symbol}: {', '.join(intervals)}")
    return "\n".join(lines)


def _parse_command(text: str):
    """返回 (command, arg)，无法解析返回 (None, None)"""
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None, None
    cmd = parts[0].lower().split("@")[0]  # 去掉 @bot_name 后缀
    arg = parts[1].upper() if len(parts) > 1 else None
    return cmd, arg


async def _process_update(update: dict, state: AppState, tg: TelegramClient, allowed_chat_id: str) -> None:
    msg = update.get("message")
    if not msg:
        return

    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "")

    # 只响应授权的 chat
    if chat_id != str(allowed_chat_id):
        logger.debug(f"忽略非授权 chat_id: {chat_id}")
        return

    cmd, arg = _parse_command(text)
    if cmd is None:
        return

    now_ts = time.time()

    if cmd == "/cache":
        if not arg:
            reply = "用法: /cache <symbol>，例如 /cache BTCUSDT"
        else:
            reply = _handle_cache(state, arg, now_ts)

    elif cmd == "/combo":
        if not arg:
            reply = "用法: /combo <symbol>，例如 /combo BTCUSDT"
        else:
            reply = _handle_combo(state, arg)

    elif cmd == "/zone":
        if not arg:
            reply = "用法: /zone <symbol>，例如 /zone BTCUSDT"
        else:
            reply = _handle_zone(state, arg, now_ts)

    elif cmd == "/universe":
        reply = _handle_universe()

    elif cmd == "/help":
        reply = COMMANDS

    else:
        return  # 未知命令静默忽略

    try:
        await tg.send_message(chat_id=chat_id, text=reply)
    except Exception:
        logger.error("命令回复发送失败", exc_info=True)


async def polling_loop(state: AppState, tg: TelegramClient, allowed_chat_id: str) -> None:
    """后台 long-polling 循环，与 FastAPI 共用 asyncio 事件循环。"""
    logger.info("TG command polling 启动")
    offset: int | None = None

    while True:
        try:
            updates = await tg.get_updates(offset=offset, timeout=20)
            for update in updates:
                offset = update["update_id"] + 1
                await _process_update(update, state, tg, allowed_chat_id)
        except asyncio.CancelledError:
            logger.info("TG command polling 停止")
            return
        except Exception:
            logger.error("polling 异常，5s 后重试", exc_info=True)
            await asyncio.sleep(5)
