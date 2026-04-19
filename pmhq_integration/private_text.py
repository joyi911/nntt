# -*- coding: utf-8 -*-
"""私聊发文字：send_private_text 命令行（与 music_card / buddies 用法一致）。"""

from __future__ import annotations

# 在包目录内执行 python private_text.py 时先按包导入再调 CLI。
if __name__ == "__main__" and __package__ is None:
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    importlib.import_module("pmhq_integration.private_text")._cli()
    raise SystemExit(0)

import json

from .client import PMHQWsClient, send_private_text


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="向好友发送私聊文字（需 PMHQ WebSocket）")
    ap.add_argument("friend_uin", help="好友 uin（QQ 号）")
    ap.add_argument(
        "-m",
        "--message",
        required=True,
        help="消息正文",
    )
    ap.add_argument("--ws", default="ws://127.0.0.1:13000/ws", help="PMHQ WebSocket 地址")
    ap.add_argument("--no-wait", action="store_true", help="不等待发送回执")
    ap.add_argument("--timeout", type=float, default=90.0, help="等待回执超时（秒）")
    ap.add_argument(
        "--no-buddy-list",
        action="store_true",
        help="不从好友列表解析 uid，直接用 getUidByUin",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="打印调试信息")
    args = ap.parse_args()

    client = PMHQWsClient(args.ws, debug_events=False)
    try:
        out = send_private_text(
            client,
            str(args.friend_uin).strip(),
            args.message,
            wait_confirm=not args.no_wait,
            wait_timeout=args.timeout,
            verbose=args.verbose,
            prefer_buddy_list=not args.no_buddy_list,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    _cli()
