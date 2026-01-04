from __future__ import annotations
import httpx


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
