# -*- coding: utf-8 -*-
"""
PMHQ 对接包（自包含副本，便于其它程序 import）。

依赖: pip install -r requirements.txt

典型用法:
    from pmhq_integration import PMHQWsClient, send_private_text
    from pmhq_integration import send_custom_music_card, get_friends_flat
    from pmhq_integration import get_self_login_info, launch_pmhq_subprocess, wait_pmhq_http

详见 example_external.py。命令行发私聊文字：在包目录执行 python private_text.py。
"""

from .buddies import (
    buddy_uids_from_v2,
    expand_buddy_uins,
    fetch_get_buddy_list,
    fetch_get_buddy_list_v2,
    get_friends_flat,
)
from .client import (
    PMHQWsClient,
    map_to_dict,
    peer_uid_from_buddy_list,
    pmhq_map,
    send_private_elements,
    send_private_text,
    uid_from_profile_result,
)
from .launch import (
    check_pmhq_http,
    default_pmhq_exe,
    json_status,
    launch_pmhq_subprocess,
    pmhq_base_url,
    terminate_process,
    wait_pmhq_http,
)
from .music_card import (
    build_music_ark_bytesdata_local,
    build_music_ark_element,
    send_custom_music_card,
    sign_custom_music,
    DEFAULT_SIGN_URL,
)
from .group_contact_card import (
    fetch_group_contact_ark_json,
    send_group_contact_card_via_ark,
)
from private_record import send_private_record
from .profile_edit import update_self_profile
from .self_avatar import set_self_avatar
from .self_status import (
    SELF_OFFLINE_OR_HIDDEN_STATUS,
    get_self_login_info,
    parse_on_self_status_changed,
)

__all__ = [
    "PMHQWsClient",
    "map_to_dict",
    "pmhq_map",
    "send_private_text",
    "send_private_elements",
    "peer_uid_from_buddy_list",
    "uid_from_profile_result",
    "get_friends_flat",
    "fetch_get_buddy_list",
    "fetch_get_buddy_list_v2",
    "buddy_uids_from_v2",
    "expand_buddy_uins",
    "sign_custom_music",
    "build_music_ark_element",
    "build_music_ark_bytesdata_local",
    "send_custom_music_card",
    "fetch_group_contact_ark_json",
    "send_group_contact_card_via_ark",
    "send_private_record",
    "DEFAULT_SIGN_URL",
    "get_self_login_info",
    "update_self_profile",
    "set_self_avatar",
    "parse_on_self_status_changed",
    "SELF_OFFLINE_OR_HIDDEN_STATUS",
    "default_pmhq_exe",
    "pmhq_base_url",
    "check_pmhq_http",
    "wait_pmhq_http",
    "json_status",
    "launch_pmhq_subprocess",
    "terminate_process",
]
