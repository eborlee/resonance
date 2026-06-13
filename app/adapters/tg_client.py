from __future__ import annotations
import httpx
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..infra.stats import MessageStats


class TelegramClient:
    def __init__(self, bot_token: str, stats: Optional["MessageStats"] = None):
        self.base = f"https://api.telegram.org/bot{bot_token}"
        self._stats = stats

    async def send_message(
        self,
        chat_id: str,
        text: str,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> int | None:
        """发送文字消息，返回 Telegram message_id（失败返回 None）。"""
        payload: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if message_thread_id is not None:
            payload["message_thread_id"] = int(message_thread_id)
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = int(reply_to_message_id)
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.base}/sendMessage", json=payload)
            r.raise_for_status()
            if self._stats is not None:
                self._stats.record(message_thread_id)
            data = r.json()
            return data.get("result", {}).get("message_id")

    async def get_updates(self, offset: int | None = None, timeout: int = 20) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset

        async with httpx.AsyncClient(timeout=timeout + 5.0) as client:
            r = await client.get(f"{self.base}/getUpdates", params=params)
            r.raise_for_status()
            return r.json().get("result", [])

    async def send_photo(
        self,
        chat_id: str,
        photo: bytes,
        caption: str | None = None,
        message_thread_id: int | None = None,
    ):
        data: dict = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        if message_thread_id is not None:
            data["message_thread_id"] = int(message_thread_id)

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{self.base}/sendPhoto",
                data=data,
                files={"photo": ("chart.png", photo, "image/png")},
            )
            r.raise_for_status()
            return r.json()

    async def edit_message_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
    ) -> None:
        payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text, "disable_web_page_preview": True}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.base}/editMessageText", json=payload)
            if r.status_code == 400 and "not modified" in r.text.lower():
                return
            r.raise_for_status()

    async def answer_callback_query(self, callback_query_id: str) -> None:
        payload = {"callback_query_id": callback_query_id}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.base}/answerCallbackQuery", json=payload)
            r.raise_for_status()

    async def set_my_commands(self, commands: List[Dict[str, str]]) -> None:
        """注册 bot 命令菜单（私聊范围）"""
        payload = {
            "commands": commands,
            "scope": {"type": "all_private_chats"},
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.base}/setMyCommands", json=payload)
            r.raise_for_status()
