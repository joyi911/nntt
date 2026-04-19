# -*- coding: utf-8 -*-
"""自定义音乐 Ark 卡片：远端签名或本地拼装 + sendMsg（从 scripts/pmhq_private_music.py 整理）。"""

from __future__ import annotations

# 在包目录内执行 python music_card.py 时先按包导入再调 CLI。
if __name__ == "__main__" and __package__ is None:
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    importlib.import_module("pmhq_integration.music_card")._cli()
    raise SystemExit(0)

import json
import secrets
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .client import PMHQWsClient, send_private_elements

DEFAULT_SIGN_URL = "https://llob.linyuchen.net/sign/music"

# 默认音乐卡参数（命令行未指定时使用，可按环境修改）
DEFAULT_JUMP_URL = "https://www.baidu.com"
DEFAULT_AUDIO_URL = "http://192.168.1.5:8000/1.mp3"
# 两行展示：title 第一行、desc 第二行（整句共 15 个「哈」对半分，QQ 不支持 title 内换行）
DEFAULT_MUSIC_TITLE = "哈哈哈哈哈哈哈"
DEFAULT_MUSIC_DESC = "你好"
DEFAULT_MUSIC_IMAGE = (
    "https://img0.baidu.com/it/u=3591665277,2616537962&fm=253&app=138&f=JPEG?w=800&h=1333"
)


def sign_custom_music(
    sign_url: str,
    *,
    jump_url: str,
    audio_url: str,
    title: str,
    image: Optional[str] = None,
    singer: Optional[str] = None,
) -> str:
    payload: Dict[str, Any] = {
        "type": "custom",
        "url": jump_url,
        "audio": audio_url,
        "title": title,
    }
    if image:
        payload["image"] = image
    if singer:
        payload["singer"] = singer
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        sign_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8").strip()
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"签名服务 HTTP {e.code}: {err_body}") from e
    if not raw:
        raise RuntimeError("签名服务返回空内容")
    return raw


def build_music_ark_bytesdata_local(
    *,
    jump_url: str,
    audio_url: str,
    title: str,
    self_uin: int,
    image: Optional[str] = None,
    singer: Optional[str] = None,
) -> str:
    ctime = int(time.time())
    token = secrets.token_hex(16)
    desc = (singer.strip() if singer and singer.strip() else " ")
    preview = (image.strip() if image and image.strip() else "")
    obj: Dict[str, Any] = {
        "app": "com.tencent.music.lua",
        "bizsrc": "qqconnect.sdkshare_music",
        "config": {"ctime": ctime, "forward": 1, "token": token, "type": "normal"},
        "extra": {"app_type": 1, "appid": 100497308, "uin": self_uin},
        "meta": {
            "music": {
                "app_type": 1,
                "appid": 100497308,
                "ctime": ctime,
                "desc": desc,
                "jumpUrl": jump_url,
                "musicUrl": audio_url,
                "preview": preview,
                "tag": "QQ音乐",
                "tagIcon": "https://p.qpic.cn/qqconnect/0/app_100497308_1626060999/100?max-age=2592000&t=0",
                "title": title,
                "uin": self_uin,
            }
        },
        "prompt": f"[分享]{title}",
        "ver": "0.0.0.1",
        "view": "music",
    }
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build_music_ark_element(bytes_data: str) -> Dict[str, Any]:
    return {
        "elementType": 10,
        "elementId": "",
        "arkElement": {
            "bytesData": bytes_data,
            "linkInfo": None,
            "subElementType": None,
        },
    }


def send_custom_music_card(
    client: PMHQWsClient,
    friend_uin: str,
    *,
    jump_url: str,
    audio_url: str,
    title: str,
    image: Optional[str] = None,
    desc: Optional[str] = None,
    sign_url: str = DEFAULT_SIGN_URL,
    use_remote_sign: bool = True,
    wait_confirm: bool = True,
    wait_timeout: float = 90.0,
    verbose: bool = False,
    prefer_buddy_list: bool = True,
) -> Dict[str, Any]:
    """
    发音乐卡片。use_remote_sign=False 时用本地 Ark（token 随机），可能受限。
    """
    if use_remote_sign:
        signed = sign_custom_music(
            sign_url,
            jump_url=jump_url,
            audio_url=audio_url,
            title=title,
            image=image,
            singer=desc,
        )
    else:
        info = client.call("getSelfInfo", [], timeout=30.0)
        if not isinstance(info, dict) or not info.get("uin"):
            raise RuntimeError(f"getSelfInfo 失败: {info!r}")
        self_uin = int(str(info["uin"]).strip())
        signed = build_music_ark_bytesdata_local(
            jump_url=jump_url,
            audio_url=audio_url,
            title=title,
            self_uin=self_uin,
            image=image,
            singer=desc,
        )
    elements = [build_music_ark_element(signed)]
    return send_private_elements(
        client,
        friend_uin,
        elements,
        wait_confirm=wait_confirm,
        wait_timeout=wait_timeout,
        verbose=verbose,
        confirm_text_hint=None,
        prefer_buddy_list=prefer_buddy_list,
    )


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="向好友发送自定义音乐 Ark 卡片（需 PMHQ WebSocket）")
    ap.add_argument("friend_uin", help="好友 uin（QQ 号）")
    ap.add_argument(
        "--jump-url",
        default=DEFAULT_JUMP_URL,
        help="点击跳转链接（未传参则用 DEFAULT_JUMP_URL）",
    )
    ap.add_argument(
        "--audio-url",
        default=DEFAULT_AUDIO_URL,
        help="音频地址",
    )
    ap.add_argument("--title", default=DEFAULT_MUSIC_TITLE, help="标题（第一行）")
    ap.add_argument("--image", default=DEFAULT_MUSIC_IMAGE, help="封面图 URL")
    ap.add_argument("--desc", default=DEFAULT_MUSIC_DESC, help="副标题/歌手（第二行）")
    ap.add_argument("--ws", default="ws://127.0.0.1:13000/ws", help="PMHQ WebSocket 地址")
    ap.add_argument(
        "--local-ark",
        action="store_true",
        help="不用远端签名，本地拼 Ark（可能受限；需能 getSelfInfo）",
    )
    ap.add_argument("--sign-url", default=DEFAULT_SIGN_URL, help="远端签名服务 URL")
    ap.add_argument("--no-wait", action="store_true", help="不等待发送确认")
    ap.add_argument("--timeout", type=float, default=90.0, help="等待确认超时（秒）")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印调试信息")
    args = ap.parse_args()

    image = args.image.strip() or None
    desc = args.desc.strip() or None

    client = PMHQWsClient(args.ws, debug_events=False)
    try:
        out = send_custom_music_card(
            client,
            str(args.friend_uin).strip(),
            jump_url=(args.jump_url or DEFAULT_JUMP_URL).strip(),
            audio_url=(args.audio_url or DEFAULT_AUDIO_URL).strip(),
            title=(args.title or DEFAULT_MUSIC_TITLE).strip(),
            image=image,
            desc=desc,
            sign_url=args.sign_url,
            use_remote_sign=not args.local_ark,
            wait_confirm=not args.no_wait,
            wait_timeout=args.timeout,
            verbose=args.verbose,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    _cli()
