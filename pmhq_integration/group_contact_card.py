# -*- coding: utf-8 -*-
"""
独立的群名片发送封装：拉取「推荐该群」Ark JSON，并通过 sendMsg 发给好友。

风格对齐 ``lengy_share_music.py``：拆成

- ``fetch_group_contact_ark_json``：仅负责向 NT 拉 Ark JSON
- ``send_group_contact_card_via_ark``：负责组装元素并发送
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

from .client import PMHQWsClient, check_nt_general_result, send_private_elements
from .music_card import build_music_ark_element


def fetch_group_contact_ark_json(
    client: PMHQWsClient,
    *,
    group_code: Union[int, str],
    timeout: float = 60.0,
    debug_capture: Optional[Dict[str, Any]] = None,
) -> str:
    """
    拉取「推荐该群」用的 Ark JSON（可直接写入 ``arkElement.bytesData``）。

    若传入 ``debug_capture``（dict），会 ``clear`` 后写入：
    ``group_code``、``call_func``、``call_args``、``raw_result``、``ark_json_len``。
    """
    gid = str(group_code).strip()
    if not gid.isdigit():
        raise RuntimeError(f"群号应为纯数字: {group_code!r}")

    func = "wrapperSession.getGroupService().getGroupRecommendContactArkJson"
    args = [gid]
    raw = client.call(func, args, timeout=timeout)

    if debug_capture is not None:
        debug_capture.clear()
        debug_capture["group_code"] = gid
        debug_capture["call_func"] = func
        debug_capture["call_args"] = list(args)
        debug_capture["raw_result"] = raw

    check_nt_general_result(raw, what="getGroupRecommendContactArkJson")
    if not isinstance(raw, dict):
        raise RuntimeError(f"getGroupRecommendContactArkJson 返回非对象: {raw!r}")

    ark = raw.get("arkJson")
    if not ark or not isinstance(ark, str):
        raise RuntimeError(f"未返回 arkJson: {raw!r}")

    if debug_capture is not None:
        debug_capture["ark_json_len"] = len(ark)

    return ark


def send_group_contact_card_via_ark(
    client: PMHQWsClient,
    *,
    friend_uin: Union[int, str],
    group_code: Union[int, str],
    fetch_timeout: float = 60.0,
    wait_confirm: bool = True,
    wait_timeout: float = 90.0,
    verbose: bool = True,
    prefer_buddy_list: bool = True,
    debug_capture: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    向指定好友私聊发送群名片。

    实现不依赖 ``contact_card.send_group_contact_card_to_friend``，
    而是自行完成「拉 Ark JSON -> 组装 arkElement -> send_private_elements」。
    """
    ark = fetch_group_contact_ark_json(
        client,
        group_code=group_code,
        timeout=fetch_timeout,
        debug_capture=debug_capture,
    )
    elements = [build_music_ark_element(ark)]
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
