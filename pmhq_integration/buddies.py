# -*- coding: utf-8 -*-
"""好友列表：getBuddyList / getBuddyListV2 + getCoreAndBaseInfo 展开 uin（从 scripts/pmhq_buddy_list.py 整理）。"""

from __future__ import annotations

# 在包目录内执行 python buddies.py 时先按包导入再调 CLI，否则顶层相对导入会失败。
if __name__ == "__main__" and __package__ is None:
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    importlib.import_module("pmhq_integration.buddies")._cli()
    raise SystemExit(0)

from typing import Any, Dict, List

from .client import PMHQWsClient, map_to_dict


def buddy_uids_from_v2(raw: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(raw, dict):
        return out
    data = raw.get("data")
    if data is None:
        r = raw.get("result")
        if isinstance(r, dict):
            data = r.get("data")
    if not isinstance(data, list):
        return out
    for cat in data:
        if not isinstance(cat, dict):
            continue
        for u in cat.get("buddyUids") or []:
            if u:
                out.append(str(u))
    return out


def expand_buddy_uins(
    client: PMHQWsClient,
    uids: List[str],
    *,
    chunk: int = 120,
    timeout: float = 90.0,
) -> List[Dict[str, Any]]:
    """分批 getCoreAndBaseInfo，得到 uid / uin / nick。"""
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for i in range(0, len(uids), chunk):
        batch = [u for u in uids[i : i + chunk] if u not in seen]
        if not batch:
            continue
        for u in batch:
            seen.add(u)
        core = client.call(
            "wrapperSession.getProfileService().getCoreAndBaseInfo",
            ["nodeStore", batch],
            timeout=timeout,
        )
        m = map_to_dict(core)
        for uid_key, info in m.items():
            if not isinstance(info, dict):
                continue
            uin = str(info.get("uin") or "").strip()
            ci = info.get("coreInfo") if isinstance(info.get("coreInfo"), dict) else {}
            uin = uin or str(ci.get("uin") or "").strip()
            nick = str(ci.get("nick") or "") if ci else ""
            rows.append({"uid": str(uid_key), "uin": uin, "nick": nick})
    return rows


def fetch_get_buddy_list(client: PMHQWsClient, *, timeout: float = 90.0) -> Any:
    return client.call("getBuddyList", [], timeout=timeout)


def fetch_get_buddy_list_v2(client: PMHQWsClient, *, timeout: float = 90.0) -> Any:
    for args in (["", True, 0], [True, 0]):
        try:
            return client.call(
                "wrapperSession.getBuddyService().getBuddyListV2",
                list(args),
                timeout=timeout,
            )
        except Exception:
            continue
    raise RuntimeError("getBuddyListV2 两种参数均失败")


def get_friends_flat(
    client: PMHQWsClient,
    *,
    expand: bool = True,
    timeout: float = 90.0,
) -> Dict[str, Any]:
    """
    汇总好友数据。
    expand=True：用 V2 的 buddyUid 列表 + getCoreAndBaseInfo 得到 {uid,uin,nick} 列表。
    """
    out: Dict[str, Any] = {"getBuddyList": None, "getBuddyListV2": None, "friends": []}
    try:
        out["getBuddyList"] = fetch_get_buddy_list(client, timeout=timeout)
    except Exception as e:
        out["getBuddyList"] = {"error": str(e)}
    try:
        v2 = fetch_get_buddy_list_v2(client, timeout=timeout)
        out["getBuddyListV2"] = v2
        if expand:
            uids = buddy_uids_from_v2(v2)
            out["friends"] = expand_buddy_uins(client, uids, timeout=timeout) if uids else []
    except Exception as e:
        out["getBuddyListV2"] = {"error": str(e)}
    return out


def _cli() -> None:
    import argparse
    import json
    import sys

    from .client import PMHQWsClient

    ap = argparse.ArgumentParser(description="获取好友列表（getBuddyList + V2 + 可选展开 uin）")
    ap.add_argument("--ws", default="ws://127.0.0.1:13000/ws", help="PMHQ WebSocket")
    ap.add_argument(
        "--no-expand",
        action="store_true",
        help="不调用 getCoreAndBaseInfo，仅原始 getBuddyList / V2（friends 为空）",
    )
    ap.add_argument("--timeout", type=float, default=90.0, help="单次 NT 调用超时（秒）")
    ap.add_argument(
        "--out",
        default="",
        help="可选：将完整 JSON 写入该文件（UTF-8）；不写则打印到 stdout",
    )
    args = ap.parse_args()

    client = PMHQWsClient(args.ws, debug_events=False)
    try:
        data = get_friends_flat(
            client,
            expand=not args.no_expand,
            timeout=args.timeout,
        )
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if args.out:
            from pathlib import Path

            Path(args.out).write_text(text, encoding="utf-8")
            print(f"已写入 {args.out}", file=sys.stderr, flush=True)
        else:
            print(text)
    finally:
        client.close()


if __name__ == "__main__":
    _cli()
