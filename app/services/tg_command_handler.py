from __future__ import annotations

import asyncio
import logging
import time
from typing import List

import yaml

from ..config import settings, get_universe
from ..domain.models import Side, LevelState
from ..infra.store import AppState
from ..adapters.tg_client import TelegramClient

logger = logging.getLogger(__name__)

COMMANDS = """/cache <symbol>   — 各周期 IN/WARM/OUT 状态
/combo <symbol>   — 当前 active 共振组合
/zone <symbol>    — zone 触及 warm 状态
/check <symbol>   — 查询品种是否在 universe 中
/add <symbol>     — 添加品种到 universe
/remove <symbol>  — 从 universe 移除品种
/universe         — 所有监控品种"""


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


class _FlowSeqDumper(yaml.Dumper):
    pass

_FlowSeqDumper.add_representer(
    list,
    lambda dumper, data: dumper.represent_sequence(
        "tag:yaml.org,2002:seq", data, flow_style=True
    ),
)


def _write_local_universe(raw: dict) -> None:
    with open(settings.UNIVERSE_LOCAL_PATH, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, Dumper=_FlowSeqDumper, allow_unicode=True, default_flow_style=False)


def _load_local_raw() -> dict:
    import os
    if os.path.exists(settings.UNIVERSE_LOCAL_PATH):
        with open(settings.UNIVERSE_LOCAL_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _handle_add(symbol: str) -> str:
    symbol = symbol.upper()

    # 检查是否已在 universe（base 或 local）
    if symbol in get_universe():
        return f"⚠️ {symbol} 已在 universe 中"

    # 取全量品种的周期并集作为新品种的周期
    all_intervals: set = set()
    for intervals in get_universe().values():
        all_intervals.update(intervals)

    interval_seconds = settings.INTERVAL_SECONDS
    sorted_intervals = sorted(
        all_intervals,
        key=lambda iv: interval_seconds.get(iv, 0),
        reverse=True,
    )

    local_raw = _load_local_raw()
    symbols: dict = local_raw.get("symbols", {})
    symbols[symbol] = {"intervals": sorted_intervals}
    local_raw["symbols"] = symbols
    _write_local_universe(local_raw)

    return f"✅ 已添加 {symbol}\n  周期: {', '.join(sorted_intervals)}"


def _handle_remove(symbol: str) -> str:
    symbol = symbol.upper()

    # 只能移除 local 文件里的品种
    local_raw = _load_local_raw()
    symbols: dict = local_raw.get("symbols", {})

    if symbol not in symbols:
        if symbol in get_universe():
            return f"⚠️ {symbol} 在 base universe.yaml 中，无法通过命令移除"
        return f"❌ {symbol} 不在 universe 中"

    del symbols[symbol]
    local_raw["symbols"] = symbols
    _write_local_universe(local_raw)

    return f"✅ 已从 universe 移除 {symbol}"


def _handle_check(symbol: str) -> str:
    uni = get_universe()
    symbol = symbol.upper()
    intervals = uni.get(symbol)
    if intervals:
        return f"✅ {symbol} 在 universe 中\n  周期: {', '.join(intervals)}"
    else:
        return f"❌ {symbol} 不在 universe 中"


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


async def _process_update(update: dict, state: AppState, tg: TelegramClient, owner_chat_id: str) -> None:
    msg = update.get("message")
    if not msg:
        return

    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    chat_type = chat.get("type", "")
    text = msg.get("text", "")

    # 只响应私聊 + 授权 owner
    if chat_type != "private" or chat_id != str(owner_chat_id):
        logger.debug(f"忽略非授权消息 chat_id={chat_id} type={chat_type}")
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

    elif cmd == "/check":
        if not arg:
            reply = "用法: /check <symbol>，例如 /check BTCUSDT"
        else:
            reply = _handle_check(arg)

    elif cmd == "/add":
        if not arg:
            reply = "用法: /add <symbol>，例如 /add SOLUSDT"
        else:
            reply = _handle_add(arg)

    elif cmd == "/remove":
        if not arg:
            reply = "用法: /remove <symbol>，例如 /remove SOLUSDT"
        else:
            reply = _handle_remove(arg)

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


COMMAND_MENU = [
    {"command": "cache",    "description": "查询品种各周期缓存状态，如 /cache BTCUSDT"},
    {"command": "combo",    "description": "查询当前 active 共振组合，如 /combo ETHUSDT"},
    {"command": "zone",     "description": "查询 zone 触及 warm 状态，如 /zone BTCUSDT"},
    {"command": "check",    "description": "查询品种是否在 universe 中，如 /check BTCUSDT"},
    {"command": "add",      "description": "添加品种到 universe，如 /add SOLUSDT"},
    {"command": "remove",   "description": "从 universe 移除品种，如 /remove SOLUSDT"},
    {"command": "universe", "description": "列出所有监控品种"},
    {"command": "help",     "description": "查看命令列表"},
]


async def polling_loop(state: AppState, tg: TelegramClient, owner_chat_id: str) -> None:
    """后台 long-polling 循环，与 FastAPI 共用 asyncio 事件循环。"""
    logger.info("TG command polling 启动")

    # 注册命令菜单
    try:
        await tg.set_my_commands(COMMAND_MENU)
        logger.info("TG 命令菜单注册成功")
    except Exception:
        logger.warning("TG 命令菜单注册失败", exc_info=True)

    offset: int | None = None

    while True:
        try:
            updates = await tg.get_updates(offset=offset, timeout=20)
            for update in updates:
                offset = update["update_id"] + 1
                await _process_update(update, state, tg, owner_chat_id)
        except asyncio.CancelledError:
            logger.info("TG command polling 停止")
            return
        except Exception:
            logger.error("polling 异常，5s 后重试", exc_info=True)
            await asyncio.sleep(5)
