# -*- coding: utf-8 -*-
"""
简单示例：获取好友列表 → 发文字消息 → 发群名片
"""

import json
import sys
from pathlib import Path

# 把上级目录加入路径，确保能 import pmhq_integration
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pmhq_integration import (
    PMHQWsClient,
    get_friends_flat,
    send_private_text,
    send_group_contact_card_via_ark,
)

WS = "ws://127.0.0.1:13000/ws"


def main():
    client = PMHQWsClient(WS, debug_events=False)
    try:

        # ── 1. 获取好友列表 ──────────────────────────────────────────────
        print("正在获取好友列表...")
        data = get_friends_flat(client, expand=True)
        friends = data.get("friends") or []

        if not friends:
            print("好友列表为空，请确认 QQ 已登录")
            return

        print(f"共 {len(friends)} 位好友，前 5 位：")
        for f in friends[:5]:
            print(f"  uin={f['uin']}  昵称={f['nick']}")

        # 取第一位好友作为发送目标
        target = friends[0]
        target_uin = target["uin"]
        print(f"\n目标好友：{target['nick']}（{target_uin}）")


        # ── 2. 发送文字消息 ──────────────────────────────────────────────
        print("\n正在发送文字消息...")
        result = send_private_text(
            client,
            target_uin,
            "你好，这是一条测试消息",
            wait_confirm=True,    # 等待 QQ 回执确认发送成功
            wait_timeout=30.0,
            verbose=False,
        )
        print("文字消息发送结果：", "成功" if result else "失败")


        # ── 3. 发送群名片 ────────────────────────────────────────────────
        group_number = "123456789"   # 换成你要推荐的群号
        print(f"\n正在发送群名片（群号 {group_number}）...")
        result = send_group_contact_card_via_ark(
            client,
            friend_uin=target_uin,
            group_code=group_number,
            wait_confirm=True,
            wait_timeout=30.0,
            verbose=False,
        )
        print("群名片发送结果：", "成功" if result else "失败")

    finally:
        client.close()
        print("\n已关闭连接")


if __name__ == "__main__":
    main()