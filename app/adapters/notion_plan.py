from __future__ import annotations

import logging
import re
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "山寨关注"
_PAGE_ID_RE = re.compile(r"([0-9a-fA-F]{32})(?:[?#].*)?$")


class NotionPlanError(Exception):
    pass


def _unwrap(rec: dict) -> dict:
    v = rec.get("value", rec)
    return v.get("value", v) if isinstance(v, dict) else {}


def _text(prop) -> str:
    if not prop:
        return ""
    return "".join(seg[0] for seg in prop if isinstance(seg, list) and seg).strip()


def _parse_url(url: str) -> Tuple[str, str]:
    """返回 (host, page_uuid)。仅允许 notion.site / notion.so 域名。"""
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    if not (host.endswith(".notion.site") or host.endswith(".notion.so") or host in ("notion.site", "notion.so")):
        raise NotionPlanError("不是 Notion 链接")
    m = _PAGE_ID_RE.search(u.path)
    if not m:
        raise NotionPlanError("链接里找不到页面 ID")
    h = m.group(1).lower()
    return host, f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


async def fetch_alt_plan(url: str) -> Dict[str, Tuple[str, str]]:
    """
    抓取老五每日关注页的「山寨关注」表，返回 {标的: (4H 一句话, 1H 一句话)}。
    4H 取「关注原因」列，1H 取「简述」列。依赖 Notion 公开页面的内部接口（非官方文档）。
    """
    host, page_id = _parse_url(url)
    api = f"https://{host}/api/v3"

    async with httpx.AsyncClient(timeout=20.0) as http:
        r = await http.post(f"{api}/loadCachedPageChunkV2", json={
            "page": {"id": page_id}, "limit": 100,
            "cursor": {"stack": []}, "chunkNumber": 0, "verticalColumns": False,
        })
        r.raise_for_status()
        rm = r.json().get("recordMap", {})

        collection_id: Optional[str] = None
        schema: dict = {}
        for cid, rec in rm.get("collection", {}).items():
            v = _unwrap(rec)
            if _text(v.get("name")) == _COLLECTION_NAME:
                collection_id, schema = cid, v.get("schema", {})
                break
        if collection_id is None:
            raise NotionPlanError(f"页面里没有「{_COLLECTION_NAME}」表")

        view_id = space_id = None
        for rec in rm.get("block", {}).values():
            b = _unwrap(rec)
            if b.get("type") == "collection_view" and b.get("collection_id") == collection_id:
                view_id = (b.get("view_ids") or [None])[0]
                space_id = b.get("space_id")
                break
        if not view_id or not space_id:
            raise NotionPlanError("找不到表格视图")

        r = await http.post(f"{api}/queryCollection", json={
            "source": {"type": "collection", "id": collection_id, "spaceId": space_id},
            "collectionView": {"id": view_id, "spaceId": space_id},
            "loader": {
                "type": "reducer",
                "reducers": {"collection_group_results": {"type": "results", "limit": 200}},
                "searchQuery": "", "userTimeZone": "Asia/Shanghai",
            },
        })
        r.raise_for_status()
        rows = r.json().get("recordMap", {}).get("block", {})

    prop_by_name = {p.get("name"): pid for pid, p in schema.items()}
    p_symbol = prop_by_name.get("标的", "title")
    p_4h = prop_by_name.get("关注原因")
    p_1h = prop_by_name.get("简述")

    result: Dict[str, Tuple[str, str]] = {}
    for rec in rows.values():
        b = _unwrap(rec)
        if b.get("parent_table") != "collection" or b.get("parent_id") != collection_id:
            continue
        props = b.get("properties", {})
        symbol = _text(props.get(p_symbol)).upper()
        if not symbol:
            continue
        result[symbol] = (_text(props.get(p_4h)), _text(props.get(p_1h)))

    if not result:
        raise NotionPlanError(f"「{_COLLECTION_NAME}」表里没有数据")
    return result
