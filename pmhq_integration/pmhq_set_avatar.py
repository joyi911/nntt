#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通过 PMHQ 修改当前登录 QQ 的头像（NT ProfileService.setHeader，与 LuckyLilliaBot setSelfAvatar 一致）。

要求：
  - 图片必须是本机路径，QQ/NT 进程能读取（建议绝对路径）。
  - PMHQ / QQNT 已登录且 WebSocket 可用。

依赖: pip install websocket-client（与其它 pmhq_*.py 相同）

用法:
  python pmhq_set_avatar.py --image C:\\path\\to\\avatar.png
  python pmhq_set_avatar.py --image ./a.jpg --ws ws://127.0.0.1:13000/ws --timeout 180
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pmhq_integration import PMHQWsClient, set_self_avatar


def main() -> int:
    ap = argparse.ArgumentParser(description="通过 PMHQ 修改当前 QQ 头像")
    ap.add_argument(
        "--image",
        required=True,
        help="本地图片文件路径（jpg/png 等）",
    )
    ap.add_argument("--ws", default="ws://127.0.0.1:13000/ws", help="PMHQ WebSocket")
    ap.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="setHeader 调用超时（秒），大文件可适当加大",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="将 NT 返回结果以 JSON 打印到 stdout",
    )
    args = ap.parse_args()

    client = PMHQWsClient(args.ws, debug_events=False)
    try:
        raw = set_self_avatar(client, args.image, timeout=args.timeout)
        if args.json:
            print(json.dumps(raw, ensure_ascii=False, indent=2))
        else:
            print("头像设置请求已完成（NT 返回见 --json）。", flush=True)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
