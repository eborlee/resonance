from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def push_signal(
    symbol: str,
    direction: str,
    triggered_at: float,
    description: str,
    timeframe_combo: Optional[str] = None,
    score_total: Optional[int] = None,
    raw_payload: Optional[dict] = None,
    image_bytes: Optional[bytes] = None,
) -> bool:
    """
    旁路归档到 resonance_dashboard。火忘模式：失败只记 warning，绝不抛异常。
    DASHBOARD_PUSH_URL 未配置时静默跳过。
    image_bytes 若提供，JSON 推送成功后再 POST 图片到 /{signal_id}/image。
    """
    url = settings.DASHBOARD_PUSH_URL
    if not url:
        return False

    signal_id = str(uuid.uuid4())
    payload: dict = {
        "signal_id": signal_id,
        "symbol": symbol,
        "direction": direction,
        "triggered_at": _ts_to_iso(triggered_at),
        "description": description,
    }
    if timeframe_combo is not None:
        payload["timeframe_combo"] = timeframe_combo
    if score_total is not None:
        payload["score_total"] = score_total
    if raw_payload is not None:
        payload["raw_payload"] = raw_payload

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                url,
                json=payload,
                headers={
                    "X-Resonance-Token": settings.DASHBOARD_PUSH_TOKEN,
                    "Content-Type": "application/json",
                },
            )
            if r.status_code not in (200, 201):
                logger.warning(
                    f"[Dashboard] 归档失败 {symbol} {direction}: HTTP {r.status_code} {r.text[:200]}"
                )
                return False
    except Exception as e:
        logger.warning(f"[Dashboard] 归档异常 {symbol} {direction}: {e}")
        return False

    if image_bytes:
        image_url = f"{url.rstrip('/')}/{signal_id}/image"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r2 = await client.post(
                    image_url,
                    files={"file": ("chart.png", image_bytes, "image/png")},
                    headers={"X-Resonance-Token": settings.DASHBOARD_PUSH_TOKEN},
                )
                if r2.status_code not in (200, 201):
                    logger.warning(
                        f"[Dashboard] 图片归档失败 {symbol} {signal_id}: HTTP {r2.status_code}"
                    )
        except Exception as e:
            logger.warning(f"[Dashboard] 图片归档异常 {symbol} {signal_id}: {e}")

    return True
