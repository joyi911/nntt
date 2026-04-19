# -*- coding: utf-8 -*-
"""本机 QQ：getSelfInfo 已登录检测 + onSelfStatusChanged 解析（从 scripts/pmhq_self_online.py 整理）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .client import PMHQWsClient

# LuckyLilliaBot core.ts: online = info.status !== 20
SELF_OFFLINE_OR_HIDDEN_STATUS = 20


def get_self_login_info(client: PMHQWsClient, *, timeout: float = 15.0) -> Dict[str, Any]:
    """getSelfInfo 有有效 uin → ok=True（已登录）。"""
    info = client.call("getSelfInfo", [], timeout=timeout)
    if isinstance(info, dict):
        uin = info.get("uin")
        uid = info.get("uid")
        if uin is not None and str(uin).strip() not in ("", "0"):
            return {
                "ok": True,
                "uin": str(uin).strip(),
                "uid": str(uid).strip() if uid else "",
            }
        return {"ok": False, "reason": "getSelfInfo 无有效 uin", "raw": info}
    return {"ok": False, "reason": "getSelfInfo 返回非对象", "raw": info}


def parse_on_self_status_changed(ws_msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """WS 一条消息是否为 onSelfStatusChanged；online = status != 20。"""
    inner = ws_msg.get("data")
    if not isinstance(inner, dict):
        return None
    if inner.get("sub_type") != "onSelfStatusChanged":
        return None
    payload = inner.get("data")
    if isinstance(payload, dict):
        st = payload.get("status")
    elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
        st = payload[0].get("status")
        payload = payload[0]
    else:
        return {"matched": True, "online": None, "note": "未知 payload 形态", "raw": payload}
    try:
        iv = int(st) if st is not None else None
    except (TypeError, ValueError):
        iv = None
    online = iv != SELF_OFFLINE_OR_HIDDEN_STATUS if iv is not None else None
    return {
        "matched": True,
        "status": st,
        "online": online,
        "payload": payload if isinstance(payload, dict) else payload,
    }
