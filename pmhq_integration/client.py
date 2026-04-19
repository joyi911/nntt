#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMHQ WebSocket 客户端与私聊发送（供 pmhq_integration 与其它程序对接）。
从 scripts/pmhq_private_text.py 同步复制；命令行入口请用仓库 scripts/pmhq_private_text.py。

依赖: websocket-client
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from queue import Empty, Queue
from typing import Any, Dict, List, Optional, Union

import websocket


def pmhq_map(entries: List[List[Any]]) -> Dict[str, Any]:
    """与 LLBot deepStringifyMap 一致，供 PMHQ 侧还原为 JavaScript Map。"""
    return {"__dataType": "Map", "data": entries}


def map_to_dict(obj: Any) -> Dict[Any, Any]:
    """把 PMHQ 返回的 Map 包装转成 Python dict。"""
    if obj is None:
        return {}
    if isinstance(obj, dict) and obj.get("__dataType") == "Map":
        out: Dict[Any, Any] = {}
        for pair in obj.get("data") or []:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                out[pair[0]] = pair[1]
        return out
    if isinstance(obj, dict):
        return obj
    return {}


def check_nt_general_result(raw: Any, *, what: str = "NT 调用") -> None:
    """校验常见 ``{ result: 0 }`` 形态；``result`` 存在且非 0 时抛错。"""
    if raw is None or not isinstance(raw, dict):
        return
    if "result" not in raw:
        return
    r = raw.get("result")
    try:
        if int(r) != 0:
            raise RuntimeError("%s 失败: result=%r 完整=%r" % (what, r, raw))
    except (TypeError, ValueError):
        pass


def _parse_int_id(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v == int(v) else None
    s = str(v).strip()
    if not s or s.lower() in ("null", "none"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def attr_ids_match(expected: str, got: Any) -> bool:
    """
    LLBot 在 Node 里用同一份 JSON 解析结果比 attrId；Python 若一端为完整大整数、
    另一端经 JS 序列化成 IEEE754 会略有偏差。尽量字符串一致，否则整数一致。
    """
    if got is None:
        return False
    if str(got) == str(expected):
        return True
    ie, ig = _parse_int_id(expected), _parse_int_id(got)
    if ie is not None and ig is not None and ie == ig:
        return True
    return False


def msg_attrs_any_attr_id(msg: Dict[str, Any], attr_id: str) -> bool:
    """在整条消息的 msgAttrs 任意槽位里找 attrId（NT 不一定用 key 0）。"""
    raw = msg.get("msgAttrs")
    m = map_to_dict(raw)
    for v in m.values():
        if isinstance(v, dict) and attr_ids_match(attr_id, v.get("attrId")):
            return True
    return False


def msg_text_content(msg: Dict[str, Any]) -> str:
    """拼接文本元素，便于与发送内容比对。"""
    parts: List[str] = []
    for el in msg.get("elements") or []:
        if not isinstance(el, dict):
            continue
        te = el.get("textElement")
        if isinstance(te, dict) and te.get("content"):
            parts.append(str(te["content"]))
    return "".join(parts)


class PMHQWsClient:
    def __init__(self, uri: str, debug_events: bool = False) -> None:
        self._debug_events = debug_events
        self._ws = websocket.create_connection(uri, timeout=60)
        self._echo_queues: Dict[str, Queue] = {}
        self._event_queue: Queue = Queue()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        # 与 LLBot 一致：在 sendMsg 调用过程中就要能收到 onMsgInfoListUpdate（先挂“钩”再 call）
        self._listen_spec: Optional[tuple] = None  # (attr_id, peer_uid, text)
        self._listen_result: Queue = Queue(maxsize=1)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._ws.close()
        except Exception:
            pass

    def poll_event(self, timeout: float = 0.5) -> Optional[Dict[str, Any]]:
        """取一条已入队的 NT 监听推送（on_profile / on_message 等），超时返回 None。"""
        try:
            ev = self._event_queue.get(timeout=timeout)
            if isinstance(ev, dict):
                return ev
            return None
        except Empty:
            return None

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._ws.recv()
            except Exception:
                break
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "call":
                echo = (data.get("data") or {}).get("echo")
                if echo:
                    with self._lock:
                        q = self._echo_queues.get(echo)
                    if q is not None:
                        q.put(data)
                        continue
            inner = data.get("data")
            is_nt_listener = isinstance(inner, dict) and "sub_type" in inner
            if is_nt_listener:
                self._try_capture_outgoing(inner, data)
            # 与 hook.ts 一致：只要 data 里带 sub_type 就是 NT 监听推送，勿因 type 不是 on_message 而丢弃
            if data.get("type") in ("on_message", "on_buddy", "on_group", "on_profile", "on_flash_file") or is_nt_listener:
                if self._debug_events:
                    preview = json.dumps(data, ensure_ascii=False)[:800]
                    print(f"[pmhq-debug] {preview}", flush=True)
                self._event_queue.put(data)

    def call(self, func: str, args: List[Any], timeout: float = 60.0) -> Any:
        echo = str(uuid.uuid4())
        q: Queue = Queue()
        with self._lock:
            self._echo_queues[echo] = q
        try:
            payload = {"type": "call", "data": {"echo": echo, "func": func, "args": args}}
            self._ws.send(json.dumps(payload, ensure_ascii=False))
            resp = q.get(timeout=timeout)
        finally:
            with self._lock:
                self._echo_queues.pop(echo, None)

        code = resp.get("code", 0)
        if code != 0:
            raise RuntimeError(resp.get("message") or json.dumps(resp, ensure_ascii=False))
        return (resp.get("data") or {}).get("result")

    def _messages_from_inner(self, inner: Dict[str, Any]) -> List[Dict[str, Any]]:
        """对齐 LLBot hook：payload 为 data.data（即 inner['data']），形态可能多样。"""
        sub = (inner.get("sub_type") or "") or ""
        raw = inner.get("data")
        if sub == "onAddSendMsg":
            if isinstance(raw, dict):
                return [raw]
            if isinstance(raw, list):
                return [m for m in raw if isinstance(m, dict)]
            return []
        if sub in (
            "onMsgInfoListUpdate",
            "onActiveMsgInfoUpdate",
            "onRecvActiveMsg",
        ):
            if isinstance(raw, list):
                return [m for m in raw if isinstance(m, dict)]
            if isinstance(raw, dict):
                if "msgId" in raw or "elements" in raw:
                    return [raw]
                for key in ("msgList", "records", "msgRecords"):
                    v = raw.get(key)
                    if isinstance(v, list):
                        return [m for m in v if isinstance(m, dict)]
                return [raw]
            return []
        if isinstance(raw, list) and raw:
            if all(isinstance(x, dict) for x in raw) and any(
                isinstance(x, dict) and ("msgId" in x or "elements" in x) for x in raw
            ):
                return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict) and ("msgId" in raw or "elements" in raw):
            return [raw]
        return []

    def _try_capture_outgoing(self, inner: Dict[str, Any], _envelope: Dict[str, Any]) -> None:
        with self._lock:
            spec = self._listen_spec
        if not spec:
            return
        attr_id, peer_uid, text = spec
        for msg in self._messages_from_inner(inner):
            if isinstance(msg, dict) and self._msg_matches_send(msg, attr_id, peer_uid, text):
                with self._lock:
                    if self._listen_spec == spec:
                        self._listen_spec = None
                try:
                    self._listen_result.put_nowait(msg)
                except Exception:
                    pass
                return

    def arm_outgoing_listener(self, attr_id: str, peer_uid: str, text: Optional[str]) -> None:
        """在调用 sendMsg 之前调用（与 LLBot registerReceiveHook 再 invoke 顺序一致）。"""
        with self._lock:
            self._listen_spec = (attr_id, peer_uid, text)
        try:
            while True:
                self._listen_result.get_nowait()
        except Empty:
            pass

    def disarm_outgoing_listener(self) -> None:
        with self._lock:
            self._listen_spec = None
        try:
            while True:
                self._listen_result.get_nowait()
        except Empty:
            pass

    def wait_outgoing_captured(self, timeout: float) -> Dict[str, Any]:
        """阻塞等待 arm 之后由读线程匹配到的消息；超时抛 queue.Empty。"""
        return self._listen_result.get(timeout=timeout)

    def _msg_matches_send(
        self,
        msg: Dict[str, Any],
        attr_id: str,
        peer_uid: Optional[str],
        text: Optional[str],
    ) -> bool:
        """
        NT 往往先推「发送中」再推「已发送」，若强制 sendStatus==2 会漏掉首包、一直等到超时。
        只要能在 msgAttrs 里对上本次 attrId，即视为本条（与 LLBot 最终态一致，略早返回）。
        """
        if msg_attrs_any_attr_id(msg, attr_id):
            return True

        # attrId 若经 JSON number 失真，用会话 + 全文匹配；允许发送中/已发送
        if peer_uid and str(msg.get("peerUid", "")) == str(peer_uid) and text:
            st = msg.get("sendStatus")
            if st is None or st in (0, 1, 2, "0", "1", "2"):
                if msg_text_content(msg) == text:
                    return True
        return False

    def wait_send_confirmed(
        self,
        attr_id: str,
        *,
        peer_uid: Optional[str] = None,
        text: Optional[str] = None,
        timeout: float = 45.0,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """等待发送结果：onMsgInfoListUpdate / onAddSendMsg / onActiveMsgInfoUpdate。"""
        deadline = time.time() + timeout
        pending: List[Any] = []
        last_progress = 0.0
        try:
            while time.time() < deadline:
                if verbose and (time.time() - last_progress) >= 8.0:
                    remain = max(0, int(deadline - time.time()))
                    print(
                        f"[pmhq] 仍在等待 NT 消息回执（剩余约 {remain}s）…",
                        file=sys.stderr,
                        flush=True,
                    )
                    last_progress = time.time()
                try:
                    ev = self._event_queue.get(timeout=0.25)
                except Empty:
                    continue
                if ev.get("type") != "on_message":
                    pending.append(ev)
                    continue
                inner = ev.get("data") or {}
                sub = inner.get("sub_type") or ""
                if sub not in (
                    "onMsgInfoListUpdate",
                    "onAddSendMsg",
                    "onActiveMsgInfoUpdate",
                    "onRecvActiveMsg",
                ):
                    pending.append(ev)
                    continue
                for msg in self._messages_from_inner(inner):
                    if self._msg_matches_send(msg, attr_id, peer_uid, text):
                        return msg
                pending.append(ev)
        finally:
            for ev in pending:
                self._event_queue.put(ev)
        raise TimeoutError(
            f"等待发送确认超时（attrId={attr_id}）。"
            f"可加 --debug-events 查看 PMHQ 实际推送的 sub_type 与字段。"
        )


def _norm_uin_buddy(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _uid_from_buddy_row(item: Any, want: str) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    uin_v = _norm_uin_buddy(item.get("uin"))
    uid_v = item.get("uid")
    si = item.get("simpleInfo")
    if isinstance(si, dict):
        uin_v = uin_v or _norm_uin_buddy(si.get("uin"))
        if uid_v is None:
            uid_v = si.get("uid")
    ci = item.get("coreInfo")
    if isinstance(ci, dict):
        uin_v = uin_v or _norm_uin_buddy(ci.get("uin"))
        if uid_v is None:
            uid_v = ci.get("uid")
    if uin_v != want:
        return None
    if uid_v is not None:
        s = str(uid_v).strip()
        if s:
            return s
    return None


def _peer_uid_from_get_buddy_list_raw(raw: Any, want: str) -> Optional[str]:
    items: List[Any] = []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        for k in ("buddyList", "data", "list", "buddies"):
            v = raw.get(k)
            if isinstance(v, list):
                items = v
                break
        if not items:
            r = raw.get("result")
            if isinstance(r, dict):
                for k in ("buddyList", "data", "list"):
                    v = r.get(k)
                    if isinstance(v, list):
                        items = v
                        break
    for it in items:
        uid = _uid_from_buddy_row(it, want)
        if uid:
            return uid
    return None


def _buddy_uids_from_list_v2(raw: Any) -> List[str]:
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


def _peer_uid_from_core_map(result: Any, want: str) -> Optional[str]:
    m = map_to_dict(result)
    for info in m.values():
        uid = _uid_from_buddy_row(info, want)
        if uid:
            return uid
    return None


def peer_uid_from_buddy_list(
    client: PMHQWsClient,
    uin: str,
    *,
    verbose: bool = False,
) -> Optional[str]:
    """
    从好友列表按 QQ 号匹配 uid（与客户端「好友」会话一致，减少进临时会话概率）。
    顺序：getBuddyList → getBuddyListV2 + 分批 getCoreAndBaseInfo。
    """
    want = _norm_uin_buddy(uin)
    if not want.isdigit():
        return None

    try:
        raw = client.call("getBuddyList", [], timeout=45.0)
    except Exception:
        raw = None
    uid = _peer_uid_from_get_buddy_list_raw(raw, want)
    if uid:
        if verbose:
            print("[pmhq] peerUid 取自 getBuddyList（优先于 getUidByUin）", file=sys.stderr, flush=True)
        return uid

    uids: List[str] = []
    for args in (["", True, 0], [True, 0]):
        try:
            raw2 = client.call(
                "wrapperSession.getBuddyService().getBuddyListV2",
                list(args),
                timeout=45.0,
            )
        except Exception:
            continue
        uids = _buddy_uids_from_list_v2(raw2)
        if uids:
            break
    if not uids:
        return None

    chunk = 120
    for i in range(0, len(uids), chunk):
        batch = uids[i : i + chunk]
        try:
            core = client.call(
                "wrapperSession.getProfileService().getCoreAndBaseInfo",
                ["nodeStore", batch],
                timeout=45.0,
            )
        except Exception:
            continue
        uid = _peer_uid_from_core_map(core, want)
        if uid:
            if verbose:
                print(
                    "[pmhq] peerUid 取自 getBuddyListV2 + getCoreAndBaseInfo（优先于 getUidByUin）",
                    file=sys.stderr,
                    flush=True,
                )
            return uid
    return None


def uid_from_profile_result(result: Any, uin: str) -> str:
    m = map_to_dict(result)
    uid = m.get(uin) or m.get(str(uin))
    if not uid:
        hint = ""
        if not str(uin).strip().isdigit():
            hint = (
                " 若命令行写成了「--uin 号码--debug-xxx」之类（号码与下一参数粘在一起），"
                "请改成「--uin 号码 --debug-xxx」（中间加空格）。"
            )
        raise RuntimeError(
            f"getUidByUin 未返回该 QQ 的 uid（请确认对方已是好友、QQ 号是否纯数字）: {result!r}{hint}"
        )
    return str(uid)


def send_private_elements(
    client: PMHQWsClient,
    friend_uin: Union[int, str],
    elements: List[Dict[str, Any]],
    *,
    wait_confirm: bool = True,
    wait_timeout: float = 90.0,
    verbose: bool = True,
    confirm_text_hint: Optional[str] = None,
    prefer_buddy_list: bool = True,
) -> Dict[str, Any]:
    """
    向好友私聊发送任意 NT 消息元素列表（文本 / Ark 音乐卡等）。
    confirm_text_hint：等待回执时用于辅助匹配的纯文本；音乐卡等非文本可留 None（仅按 attrId 匹配）。
    prefer_buddy_list：为 True 时先按好友列表匹配 uid，再回退 getUidByUin（更易对齐「好友」会话）。
    """
    uin_s = str(friend_uin).strip()
    if not uin_s.isdigit():
        raise RuntimeError(
            f"好友 QQ 号应为纯数字，当前为 {friend_uin!r}。"
            f"常见误写：--uin 2690574510--debug-sign（应改为 --uin 2690574510 --debug-sign，中间加空格）。"
        )

    server_time = client.call("wrapperSession.getMSFService().getServerTime", [])
    if server_time is None:
        raise RuntimeError("getServerTime 返回空")

    unique_raw = client.call(
        "wrapperSession.getMsgService().generateMsgUniqueId",
        [1, server_time],
    )
    if unique_raw is None or unique_raw == "":
        raise RuntimeError("generateMsgUniqueId 返回空")
    unique_id = str(unique_raw)

    peer_uid: Optional[str] = None
    if prefer_buddy_list:
        peer_uid = peer_uid_from_buddy_list(client, uin_s, verbose=verbose)
    if not peer_uid:
        uid_map = client.call(
            "wrapperSession.getProfileService().getUidByUin",
            ["FriendsServiceImpl", [uin_s]],
        )
        peer_uid = uid_from_profile_result(uid_map, uin_s)
        if verbose and prefer_buddy_list:
            print(
                "[pmhq] 好友列表未命中，已回退 getUidByUin（若进临时会话可改用 pmhq_add_friend resolve 拿 uid）",
                file=sys.stderr,
                flush=True,
            )

    peer = {"chatType": 1, "peerUid": peer_uid, "guildId": ""}
    msg_attribute_infos = pmhq_map(
        [
            [
                0,
                {
                    "attrType": 0,
                    "attrId": unique_id,
                    "vasMsgInfo": {
                        "msgNamePlateInfo": {},
                        "bubbleInfo": {},
                        "avatarPendantInfo": {},
                        "vasFont": {},
                        "iceBreakInfo": {},
                    },
                },
            ]
        ]
    )

    if wait_confirm:
        client.arm_outgoing_listener(unique_id, peer_uid, confirm_text_hint)

    try:
        send_result = client.call(
            "wrapperSession.getMsgService().sendMsg",
            ["0", peer, elements, msg_attribute_infos],
            timeout=90.0,
        )

        if isinstance(send_result, dict):
            rc = send_result.get("result")
            if rc is not None:
                try:
                    if int(rc) != 0:
                        raise RuntimeError(f"sendMsg 调用失败: {send_result!r}")
                except (TypeError, ValueError):
                    raise RuntimeError(f"sendMsg 返回异常: {send_result!r}") from None

        if not wait_confirm:
            if verbose:
                print(
                    "[pmhq] sendMsg 已返回成功，已跳过等待 WebSocket 回执（--no-wait）。",
                    file=sys.stderr,
                    flush=True,
                )
            return {
                "_fastExit": True,
                "attrId": unique_id,
                "peerUid": peer_uid,
                "sendResult": send_result,
                "hint": "消息通常已发出；若需完整 RawMessage JSON，请去掉 --no-wait 或增大 --wait-timeout",
            }

        deadline = time.time() + wait_timeout
        if verbose:
            print(
                f"[pmhq] 已与 LLBot 相同顺序挂好监听；sendMsg 已返回，等待回执（最长 {wait_timeout:.0f}s）…",
                file=sys.stderr,
                flush=True,
            )

        remain = max(0.05, deadline - time.time())
        try:
            confirmed = client.wait_outgoing_captured(remain)
        except Empty:
            remain = max(0.05, deadline - time.time())
            if verbose:
                print(
                    "[pmhq] 读线程未在时限内命中，改为扫描事件队列（可能推送的 type 与预期不一致）…",
                    file=sys.stderr,
                    flush=True,
                )
            confirmed = client.wait_send_confirmed(
                unique_id,
                peer_uid=peer_uid,
                text=confirm_text_hint,
                timeout=remain,
                verbose=verbose,
            )
        return confirmed
    finally:
        if wait_confirm:
            client.disarm_outgoing_listener()


def send_private_text(
    client: PMHQWsClient,
    friend_uin: Union[int, str],
    text: str,
    *,
    wait_confirm: bool = True,
    wait_timeout: float = 90.0,
    verbose: bool = True,
    prefer_buddy_list: bool = True,
) -> Dict[str, Any]:
    elements = [
        {
            "elementType": 1,
            "elementId": "",
            "textElement": {
                "content": text,
                "atType": 0,
                "atUid": "",
                "atTinyId": "",
                "atNtUid": "",
            },
        }
    ]
    return send_private_elements(
        client,
        friend_uin,
        elements,
        wait_confirm=wait_confirm,
        wait_timeout=wait_timeout,
        verbose=verbose,
        confirm_text_hint=text,
        prefer_buddy_list=prefer_buddy_list,
    )
