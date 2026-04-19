#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
其它程序对接示例：将本目录上级作为 PYTHONPATH，或安装为包后 import pmhq_integration。

  # 在「新版」等包含 pmhq_integration 的目录:
  set PYTHONPATH=%CD%
  python pmhq_integration/example_external.py

  # 仅检测本机 QQ 是否已登录（PMHQ 可达 + getSelfInfo 有有效 uin），打印 JSON：
  python pmhq_integration/example_external.py --check-online
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# 允许直接 python pmhq_integration/example_external.py 运行
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pmhq_integration import (
    PMHQWsClient,
    check_pmhq_http,
    get_friends_flat,
    get_self_login_info,
    pmhq_base_url,
    send_private_text,
    wait_pmhq_http,
)
from pmhq_integration.launch import default_pmhq_exe, launch_pmhq_subprocess, terminate_process

WS = "ws://127.0.0.1:13000/ws"


def check_local_qq_online(
    *,
    ws_uri: str = WS,
    http_url: Optional[str] = None,
    http_timeout: float = 3.0,
    info_timeout: float = 15.0,
) -> Dict[str, Any]:
    """
    经 PMHQ 检测本机 QQ 是否已登录：HTTP 端口可达 + WebSocket + getSelfInfo 含有效 uin。

    返回字段示例：
      pmhq_http: PMHQ HTTP 是否可达
      ws_ok: WebSocket 是否连接成功
      online: 是否判定为已登录（与 get_self_login_info 的 ok 一致）
      uin / uid: 已登录时填充
      reason: 未在线时的简要说明
    """
    base = http_url or pmhq_base_url()
    out: Dict[str, Any] = {
        "pmhq_http": check_pmhq_http(base, timeout=http_timeout),
        "ws_ok": False,
        "online": False,
        "uin": "",
        "uid": "",
        "reason": "",
    }
    if not out["pmhq_http"]:
        out["reason"] = "PMHQ HTTP 不可达（请确认 QQ/PMHQ 已启动且 13000 可用）"
        return out

    client: Optional[PMHQWsClient] = None
    try:
        client = PMHQWsClient(ws_uri, debug_events=False)
        out["ws_ok"] = True
    except Exception as e:
        out["reason"] = f"WebSocket 连接失败: {e}"
        return out

    try:
        info = get_self_login_info(client, timeout=info_timeout)
        if info.get("ok"):
            out["online"] = True
            out["uin"] = str(info.get("uin") or "").strip()
            out["uid"] = str(info.get("uid") or "").strip()
        else:
            out["reason"] = str(info.get("reason") or "未登录")
            raw = info.get("raw")
            if raw is not None:
                out["raw"] = raw
    finally:
        client.close()

    return out


def demo_launch_if_needed() -> None:
    if check_pmhq_http():
        print("PMHQ HTTP 已可用，跳过启动 exe")
        return
    exe = default_pmhq_exe()
    print(f"尝试启动: {exe}")
    proc, _ = launch_pmhq_subprocess(exe)
    if wait_pmhq_http(timeout=45.0):
        print("PMHQ 端口已就绪")
    else:
        print("等待超时；请手动开 QQ+PMHQ")
    if proc and proc.poll() is not None:
        print("说明: exe 已退出（常见为启动器），服务可能在 QQ 进程内")
    # 不在此 terminate，避免误关用户环境；需要时可 terminate_process(proc)


def demo_self_and_friends() -> None:
    c = PMHQWsClient(WS)
    try:
        me = get_self_login_info(c)
        print("本机登录:", json.dumps(me, ensure_ascii=False))
        if not me.get("ok"):
            return
        data = get_friends_flat(c, expand=True)
        n = len(data.get("friends") or [])
        print(f"好友展开条数: {n}（前 3 条）")
        for row in (data.get("friends") or [])[:3]:
            print(" ", row)
    finally:
        c.close()


def demo_send_text(uin: str, text: str) -> None:
    c = PMHQWsClient(WS)
    try:
        msg = send_private_text(
            c,
            uin,
            text,
            wait_confirm=False,
            verbose=True,
        )
        print("发送:", json.dumps(msg, ensure_ascii=False)[:500])
    finally:
        c.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="PMHQ 对接示例 / 本机 QQ 在线检测")
    ap.add_argument(
        "--check-online",
        action="store_true",
        help="仅检测本机 QQ 是否已登录，打印 JSON；online 为 false 时退出码 1",
    )
    args = ap.parse_args()

    if args.check_online:
        r = check_local_qq_online()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r.get("online") else 1)

    demo_launch_if_needed()
    demo_self_and_friends()
    # demo_send_text("123456789", "来自 example_external 的测试")
