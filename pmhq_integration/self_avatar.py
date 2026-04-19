# -*- coding: utf-8 -*-
"""
通过 PMHQ 调用 NT ProfileService.setHeader 设置当前登录 QQ 头像。

与 pmhq_set_avatar.py、LuckyLilliaBot setSelfAvatar 一致：参数为本机可读绝对路径。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Union

from .client import PMHQWsClient

# PMHQ 侧映射到 wrapperSession.getProfileService().setHeader
_SET_HEADER_FUNC = "wrapperSession.getProfileService().setHeader"


def set_self_avatar(
    client: PMHQWsClient,
    image_path: Union[str, Path],
    *,
    timeout: float = 120.0,
) -> Any:
    """
    将本机图片设为当前 QQ 头像。

    :param image_path: 本地文件路径（将转为绝对路径；须保证 QQ/NT 进程能访问）
    """
    p = Path(os.path.expanduser(str(image_path)))
    if not p.is_file():
        raise FileNotFoundError("头像文件不存在: %s" % p.resolve())
    abs_path = str(p.resolve())
    return client.call(_SET_HEADER_FUNC, [abs_path], timeout=float(timeout))
