# -*- coding: utf-8 -*-
"""
当前账号资料：昵称、签名(longNick)、性别、生日 → modifyDesktopMiniProfile。

对齐 LuckyLilliaBot ntUserApi.modifySelfProfile → modifyDesktopMiniProfile。

拉资料：LLBot 的 getUserDetailInfoWithBizInfo 依赖监听 onUserDetailInfoChanged，PMHQ 同步 call 往往只有
``{result:0}``。此处优先 ``getUserDetailInfoByUin``、``fetchUserDetailInfo``，最后再 ``getUserDetailInfoWithBizInfo`` + 轮询 WS。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .client import PMHQWsClient, check_nt_general_result, map_to_dict

# LuckyLilliaBot src/ntqqapi/types/user.ts enum Sex
SEX_UNKNOWN = 0
SEX_MALE = 1
SEX_FEMALE = 2
SEX_HIDDEN = 255

# UserDetailSource.KSERVER=1, ProfileBizType.KALL=0
_FETCH_SOURCE_KSERVER = 1
_FETCH_BIZ_KALL = 0


def _as_int(v: Any, default: int = 0) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def get_self_uid_and_uin(client: PMHQWsClient, *, timeout: float = 15.0) -> Tuple[str, str]:
    info = client.call("getSelfInfo", [], timeout=timeout)
    if not isinstance(info, dict):
        raise RuntimeError(f"getSelfInfo 异常: {info!r}")
    uid = str(info.get("uid") or "").strip()
    uin = str(info.get("uin") or "").strip()
    if not uid:
        raise RuntimeError("getSelfInfo 无 uid，请确认已登录")
    return uid, uin


def get_self_uid(client: PMHQWsClient, *, timeout: float = 15.0) -> str:
    uid, _ = get_self_uid_and_uin(client, timeout=timeout)
    return uid


def _unwrap_user_detail_from_api_response(raw: Any, want_uid: str) -> Dict[str, Any]:
    """从 getUserDetailInfoByUin / fetchUserDetailInfo 的返回中取出含 ``simpleInfo`` 的 UserDetailInfo。"""
    if not isinstance(raw, dict):
        raise RuntimeError(f"资料 API 返回非对象: {raw!r}")
    check_nt_general_result(raw, what="拉取用户详细资料")
    detail = raw.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("simpleInfo"), dict):
        return detail
    if isinstance(detail, dict):
        want = str(want_uid).strip()
        m = map_to_dict(detail)
        exact: Optional[Dict[str, Any]] = None
        fallback: Optional[Dict[str, Any]] = None
        for v in m.values():
            if isinstance(v, dict) and isinstance(v.get("simpleInfo"), dict):
                si = v["simpleInfo"]
                if str(si.get("uid") or "").strip() == want:
                    exact = v
                    break
                if fallback is None:
                    fallback = v
        if exact is not None:
            return exact
        if fallback is not None:
            return fallback
        for v in detail.values():
            if isinstance(v, dict) and isinstance(v.get("simpleInfo"), dict):
                return v
    if isinstance(raw.get("simpleInfo"), dict):
        return raw
    raise RuntimeError(f"响应中找不到 simpleInfo: {raw!r}")


def _find_user_detail_in_message(obj: Any, want_uid: str) -> Optional[Dict[str, Any]]:
    """在监听推送里深度查找带 ``simpleInfo.coreInfo`` 的 UserDetailInfo。"""
    want = str(want_uid).strip()
    hits: List[Dict[str, Any]] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            si = o.get("simpleInfo")
            if isinstance(si, dict) and isinstance(si.get("coreInfo"), dict):
                hits.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(obj)
    for d in hits:
        si = d["simpleInfo"]
        if str(si.get("uid") or "").strip() == want:
            return d
    return hits[0] if hits else None


def _fetch_via_with_biz_listener(
    client: PMHQWsClient, uid: str, wait_s: float
) -> Dict[str, Any]:
    client.call(
        "wrapperSession.getProfileService().getUserDetailInfoWithBizInfo",
        [uid, [0]],
        timeout=min(max(wait_s, 5.0), 45.0),
    )
    deadline = time.time() + max(wait_s, 3.0)
    while time.time() < deadline:
        remain = min(0.4, max(0.05, deadline - time.time()))
        ev = client.poll_event(remain)
        if not ev:
            continue
        hit = _find_user_detail_in_message(ev, uid)
        if hit:
            return hit
    raise RuntimeError(
        "getUserDetailInfoWithBizInfo 未返回 detail，且在超时内未收到带 simpleInfo 的监听；"
        "请确认 PMHQ 转发资料类事件，或加大 fetch timeout"
    )


def fetch_user_detail_with_biz_info(
    client: PMHQWsClient,
    uid: str,
    *,
    uin: Optional[str] = None,
    timeout: float = 90.0,
) -> Dict[str, Any]:
    """
    拉取用于合并改资料的 UserDetailInfo（含 ``simpleInfo``）。

    优先 ``getUserDetailInfoByUin``（需有效 uin），再 ``fetchUserDetailInfo``，最后 WithBiz + WS 兜底。
    """
    errs: List[str] = []
    uin_s = (uin or "").strip()
    if not uin_s:
        try:
            _, uin_s = get_self_uid_and_uin(client, timeout=min(20.0, timeout))
        except Exception as e:
            errs.append(f"getSelfInfo 取 uin: {e}")
            uin_s = ""

    if uin_s.isdigit():
        try:
            raw = client.call(
                "wrapperSession.getProfileService().getUserDetailInfoByUin",
                [uin_s],
                timeout=timeout,
            )
            return _unwrap_user_detail_from_api_response(raw, uid)
        except Exception as e:
            errs.append(f"getUserDetailInfoByUin: {e}")

    try:
        raw = client.call(
            "wrapperSession.getProfileService().fetchUserDetailInfo",
            [
                "BuddyProfileStore",
                [uid],
                _FETCH_SOURCE_KSERVER,
                [_FETCH_BIZ_KALL],
            ],
            timeout=timeout,
        )
        return _unwrap_user_detail_from_api_response(raw, uid)
    except Exception as e:
        errs.append(f"fetchUserDetailInfo: {e}")

    try:
        listen_budget = min(45.0, max(12.0, timeout * 0.5))
        return _fetch_via_with_biz_listener(client, uid, wait_s=listen_budget)
    except Exception as e:
        errs.append(f"getUserDetailInfoWithBizInfo+监听: {e}")

    raise RuntimeError("无法拉取本人详细资料:\n" + "\n".join(errs))


def mini_profile_from_user_detail(detail: Dict[str, Any]) -> Dict[str, Any]:
    si = detail.get("simpleInfo")
    if not isinstance(si, dict):
        raise RuntimeError(f"资料中缺少 simpleInfo: {detail!r}")
    core = si.get("coreInfo") if isinstance(si.get("coreInfo"), dict) else {}
    base = si.get("baseInfo") if isinstance(si.get("baseInfo"), dict) else {}
    return {
        "nick": str(core.get("nick") or ""),
        "longNick": str(base.get("longNick") or ""),
        "sex": _as_int(base.get("sex"), 0),
        "birthday": {
            "birthday_year": _as_int(base.get("birthday_year"), 0),
            "birthday_month": _as_int(base.get("birthday_month"), 0),
            "birthday_day": _as_int(base.get("birthday_day"), 0),
        },
        "location": {
            "country": "",
            "province": "",
            "city": "",
            "zone": "",
        },
    }


def modify_desktop_mini_profile(
    client: PMHQWsClient,
    profile: Dict[str, Any],
    *,
    timeout: float = 90.0,
) -> Any:
    raw = client.call(
        "wrapperSession.getProfileService().modifyDesktopMiniProfile",
        [profile],
        timeout=timeout,
    )
    check_nt_general_result(raw, what="modifyDesktopMiniProfile")
    return raw


def update_self_profile(
    client: PMHQWsClient,
    *,
    uid: Optional[str] = None,
    nick: Optional[str] = None,
    long_nick: Optional[str] = None,
    sex: Optional[int] = None,
    birthday_year: Optional[int] = None,
    birthday_month: Optional[int] = None,
    birthday_day: Optional[int] = None,
    fetch_timeout: float = 90.0,
    modify_timeout: float = 90.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    合并修改当前登录账号的昵称、签名(longNick)、性别、生日。

    未传入的字段保持 NT 当前值。客户端展示的「年龄」由生日推导，接口只改生日年月日三字段。

    性别与 NT 枚举一致：``sex=1`` 男，``sex=2`` 女（见本文件 ``SEX_MALE`` / ``SEX_FEMALE``）。
    """
    want_any = (
        nick is not None
        or long_nick is not None
        or sex is not None
        or birthday_year is not None
        or birthday_month is not None
        or birthday_day is not None
    )
    if not want_any and not dry_run:
        raise ValueError("至少需要指定一项：nick / long_nick / sex / birthday_*")

    uin_hint: Optional[str] = None
    if uid is None:
        uid_resolved, uin_hint = get_self_uid_and_uin(
            client, timeout=min(fetch_timeout, 30.0)
        )
    else:
        uid_resolved = uid
        try:
            _, uin_hint = get_self_uid_and_uin(
                client, timeout=min(fetch_timeout, 30.0)
            )
        except Exception:
            uin_hint = None

    detail = fetch_user_detail_with_biz_info(
        client, uid_resolved, uin=uin_hint, timeout=fetch_timeout
    )
    prof = mini_profile_from_user_detail(detail)

    if nick is not None:
        prof["nick"] = nick
    if long_nick is not None:
        prof["longNick"] = long_nick
    if sex is not None:
        prof["sex"] = int(sex)
    bd = prof["birthday"]
    if birthday_year is not None:
        bd["birthday_year"] = int(birthday_year)
    if birthday_month is not None:
        bd["birthday_month"] = int(birthday_month)
    if birthday_day is not None:
        bd["birthday_day"] = int(birthday_day)

    out: Dict[str, Any] = {"uid": uid_resolved, "miniProfile": prof}
    if dry_run:
        out["applied"] = False
        return out

    modify_res = modify_desktop_mini_profile(client, prof, timeout=modify_timeout)
    out["applied"] = True
    out["modifyResult"] = modify_res
    return out


def parse_birthday_arg(text: str) -> Tuple[int, int, int]:
    """解析 ``YYYY-M-D`` 或 ``YYYY/M/D``。"""
    s = text.strip()
    m = re.match(r"^(\d{4})[\-/](\d{1,2})[\-/](\d{1,2})$", s)
    if not m:
        raise ValueError(f"生日格式须为 YYYY-M-D 或 YYYY/M/D: {text!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))
