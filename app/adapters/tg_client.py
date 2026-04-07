from __future__ import annotations
import httpx
from typing import List, Dict, Any


class TelegramClient:
    def __init__(self, bot_token: str):
        self.base = f"https://api.telegram.org/bot{bot_token}"

    async def send_message(self, chat_id: str, text: str, message_thread_id: int | None = None):
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if message_thread_id is not None:
            payload["message_thread_id"] = int(message_thread_id)

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.base}/sendMessage", json=payload)
            r.raise_for_status()
            return r.json()

    async def get_updates(self, offset: int | None = None, timeout: int = 20) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset

        async with httpx.AsyncClient(timeout=timeout + 5.0) as client:
            r = await client.get(f"{self.base}/getUpdates", params=params)
            r.raise_for_status()
            return r.json().get("result", [])

    async def set_my_commands(self, commands: List[Dict[str, str]]) -> None:
        """注册 bot 命令菜单（私聊范围）"""
        payload = {
            "commands": commands,
            "scope": {"type": "all_private_chats"},
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.base}/setMyCommands", json=payload)
            r.raise_for_status()
