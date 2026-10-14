#!/usr/bin/env python3
"""
老五信号监听器：使用 Telethon 监听老五 bot 推送，转发到 resonance /webhook/laowu。

环境变量：
  TG_API_ID           — Telegram user API ID（my.telegram.org 获取）
  TG_API_HASH         — Telegram user API Hash
  LAOWU_BOT_ID        — 老五 bot 的 Telegram user_id（整数）
  LAOWU_WEBHOOK_URL   — resonance webhook 地址，默认 http://localhost:80/webhook/laowu
  LAOWU_WEBHOOK_TOKEN — X-Resonance-Token 鉴权头（可选）

运行：
  pip install telethon httpx
  python scripts/laowu_listener.py
  首次运行会要求输入手机号和验证码，之后 session 文件保存在当前目录。
"""

import asyncio
import logging
import os
import time

import httpx
from telethon import TelegramClient, events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
# LAOWU_BOT_ID 未配置时进入"探测模式"：打印所有收到的消息发送者 ID，用于找到 bot ID
_RAW_BOT_ID = os.environ.get("LAOWU_BOT_ID", "")
LAOWU_BOT_ID: int | None = int(_RAW_BOT_ID) if _RAW_BOT_ID else None
WEBHOOK_URL = os.environ.get("LAOWU_WEBHOOK_URL", "http://localhost:80/webhook/laowu")
WEBHOOK_TOKEN = os.environ.get("LAOWU_WEBHOOK_TOKEN", "")

client = TelegramClient("laowu_session", API_ID, API_HASH)


@client.on(events.NewMessage())
async def on_any_message(event):
    """探测模式：LAOWU_BOT_ID 未配置时打印所有消息发送者，用于确认 bot ID。"""
    if LAOWU_BOT_ID is not None:
        return
    sender = await event.get_sender()
    sender_id = sender.id if sender else event.sender_id
    text_preview = (event.message.text or "")[:40]
    logger.info(f"[探测] sender_id={sender_id}  text={text_preview!r}")


@client.on(events.NewMessage())
async def on_message(event):
    if LAOWU_BOT_ID is None or event.sender_id != LAOWU_BOT_ID:
        return
    text = event.message.text or ""
    if not text:
        return
    logger.info(f"收到老五消息: {text[:80]!r}")
    payload = {"text": text, "ts": time.time()}
    headers = {"Content-Type": "application/json"}
    if WEBHOOK_TOKEN:
        headers["X-Resonance-Token"] = WEBHOOK_TOKEN
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(WEBHOOK_URL, json=payload, headers=headers)
            logger.info(f"webhook 响应: {r.status_code} {r.text[:80]}")
    except Exception as e:
        logger.warning(f"webhook 发送失败: {e}")


async def main():
    if LAOWU_BOT_ID is None:
        logger.warning("LAOWU_BOT_ID 未配置，进入探测模式：将打印所有收到消息的发送者 ID")
    else:
        logger.info(f"老五信号监听器启动，监听 bot_id={LAOWU_BOT_ID}")
    await client.start()
    logger.info("Telethon 已登录")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
