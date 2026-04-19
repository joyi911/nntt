# -*- coding: utf-8 -*-
"""启动 PMHQ 可执行文件并检测 HTTP 13000（从 scripts/pmhq_launch_monitor.py 整理）。

库函数供 import；也可直接运行：
  python launch.py                  # 启动 exe 并等待 13000
  python launch.py --check-only       # 只探测 http://127.0.0.1:13000/ 是否通
  python launch.py --no-launch      # 不启动 exe，只等待端口（QQ 已带 PMHQ 时）
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List, Optional, Tuple

INTEGRATION_ROOT = Path(__file__).resolve().parent
# 含 pmhq_integration 的目录：PMHQ-main 或「新版」分发根目录
REPO_ROOT = INTEGRATION_ROOT.parent

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 13000

# 常见 PMHQ / NT 宿主文件名（按顺序尝试）
PMHQ_EXE_NAMES = ("QQNT.exe", "pmhq-win-x64.exe", "qqnt_host.exe")


def application_runtime_base() -> Path:
    """
    软件运行根目录（用于 ./bin/ 下的 PMHQ）：
    - PyInstaller 打包：exe 所在目录
    - python ClientHeadless.py：主脚本所在目录（不依赖当前工作目录）
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    try:
        import __main__ as _main

        mf = getattr(_main, "__file__", None)
        if mf:
            return Path(mf).resolve().parent
    except Exception:
        pass
    return Path.cwd().resolve()


def pmhq_exe_candidates() -> List[Path]:
    """可能放置 PMHQ 可执行文件的位置（按顺序尝试）。"""
    env = os.environ.get("PMHQ_EXE", "").strip()
    cands: List[Path] = []
    if env:
        cands.append(Path(env))

    rt = application_runtime_base()
    for name in PMHQ_EXE_NAMES:
        cands.append(rt / "bin" / name)

    # PyInstaller onefile：模块在 _MEIPASS/pmhq_integration/，spec 的 datas 把整棵 qqnt 放在 _MEIPASS/qqnt/
    # 仅查 INTEGRATION_ROOT/pmhq 会落到错误的 _MEIPASS/pmhq_integration/pmhq（与解压数据不同级）
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        mp = Path(meipass)
        bundled_pmhq = mp / "qqnt" / "pmhq_integration" / "pmhq"
        for name in PMHQ_EXE_NAMES:
            cands.append(bundled_pmhq / name)

    for name in PMHQ_EXE_NAMES:
        cands.append(INTEGRATION_ROOT / "pmhq" / name)

    cands.append(REPO_ROOT / "pmhq-win-x64" / "pmhq-win-x64.exe")
    cands.append(REPO_ROOT.parent / "pmhq-win-x64" / "pmhq-win-x64.exe")
    return cands


def default_pmhq_exe() -> Path:
    for p in pmhq_exe_candidates():
        try:
            r = p.expanduser().resolve()
        except OSError:
            continue
        if r.is_file():
            return r
    c = pmhq_exe_candidates()
    return (c[0] if c else Path("bin") / "pmhq-win-x64.exe").expanduser().resolve()


def resolve_pmhq_exe(explicit: Optional[Path] = None) -> Path:
    """确定要启动的 exe；找不到则 FileNotFoundError，并写明已尝试路径。"""
    if explicit is not None:
        p = explicit.expanduser().resolve()
        if p.is_file():
            return p
        raise FileNotFoundError(f"未找到 PMHQ（--exe）: {p}")
    tried: List[str] = []
    for c in pmhq_exe_candidates():
        try:
            r = c.expanduser().resolve()
        except OSError:
            continue
        tried.append(str(r))
        if r.is_file():
            return r
    raise FileNotFoundError(
        "未找到 PMHQ（QQNT.exe / pmhq-win-x64.exe / qqnt_host.exe）。已尝试:\n  "
        + "\n  ".join(tried)
        + "\n请将 exe 放入「软件目录/bin/」或 qqnt/pmhq_integration/pmhq/，或设置环境变量 PMHQ_EXE，"
        + "或: python launch.py --exe D:\\path\\to\\pmhq-win-x64.exe"
    )


def pmhq_base_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    return f"http://{host}:{port}/"


def check_pmhq_http(url: Optional[str] = None, *, timeout: float = 3.0) -> bool:
    u = url or pmhq_base_url()
    try:
        req = urllib.request.Request(u, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            _ = r.read(1)
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def wait_pmhq_http(
    url: Optional[str] = None,
    *,
    timeout: float = 60.0,
    interval: float = 0.5,
) -> bool:
    u = url or pmhq_base_url()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_pmhq_http(u, timeout=min(2.0, interval + 1.0)):
            return True
        time.sleep(interval)
    return False


def launch_pmhq_subprocess(
    exe: Optional[Path] = None,
) -> Tuple[Optional[subprocess.Popen], Path]:
    """
    启动 PMHQ exe；返回 (Popen 或 None, 使用的 exe 路径)。
    子进程 stdout/stderr 丢弃；调用方可根据 poll() 是否为 launcher 秒退。
    """
    path = resolve_pmhq_exe(exe)
    cwd = path.parent
    proc = subprocess.Popen(
        [str(path)],
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return proc, path


def terminate_process(proc: Optional[subprocess.Popen], *, wait_s: float = 8.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=wait_s)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _main_cli() -> None:
    p = argparse.ArgumentParser(description="启动 PMHQ 并检测 13000 端口")
    p.add_argument(
        "--exe",
        type=Path,
        default=None,
        help="PMHQ 路径；默认优先 软件运行目录/bin/、再 qqnt 内置 pmhq/ 或环境变量 PMHQ_EXE",
    )
    p.add_argument("--no-launch", action="store_true", help="不启动进程，只等待 HTTP 就绪")
    p.add_argument("--check-only", action="store_true", help="只检测一次端口，不启动、不等待")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--ready-timeout", type=float, default=60.0, help="等待 HTTP 就绪的最长时间（秒）")
    p.add_argument("--interval", type=float, default=10.0, help="持续监控时轮询间隔（秒）")
    p.add_argument(
        "--monitor",
        action="store_true",
        help="就绪后每隔 --interval 秒打印 HTTP 状态，Ctrl+C 结束（会尝试结束已启动的子进程）",
    )
    args = p.parse_args()

    base_url = pmhq_base_url(args.host, args.port)
    proc_holder: List[Optional[subprocess.Popen]] = [None]

    def cleanup() -> None:
        terminate_process(proc_holder[0])

    def on_sig(_s: int, _f: Any) -> None:
        cleanup()
        sys.exit(130)

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, on_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_sig)

    if args.check_only:
        ok = check_pmhq_http(base_url)
        print(json_status(ok, base_url))
        sys.exit(0 if ok else 1)

    if not args.no_launch:
        try:
            proc_holder[0], exe_path = launch_pmhq_subprocess(args.exe)
            print(f"[launch] 已启动: {exe_path}", flush=True)
        except FileNotFoundError as e:
            print(f"[launch] 错误: {e}", file=sys.stderr, flush=True)
            sys.exit(1)
        if not wait_pmhq_http(base_url, timeout=args.ready_timeout):
            print(f"[launch] {args.ready_timeout}s 内 {base_url} 仍不可达", file=sys.stderr, flush=True)
            cleanup()
            sys.exit(2)
        print(f"[launch] {base_url} 已就绪", flush=True)
    else:
        if not wait_pmhq_http(base_url, timeout=args.ready_timeout):
            print(f"[launch] --no-launch: {base_url} 不可达", file=sys.stderr, flush=True)
            sys.exit(2)
        print(f"[launch] {base_url} 已就绪", flush=True)

    if proc_holder[0] is not None and proc_holder[0].poll() is not None:
        print(
            f"[launch] 子进程已退出 code={proc_holder[0].returncode}（多为启动器；服务可能在 QQ 内），继续按 HTTP 监控。",
            flush=True,
        )
        proc_holder[0] = None

    if not args.monitor:
        sys.exit(0)

    print(f"[launch] 监控中（间隔 {args.interval}s），Ctrl+C 退出", flush=True)
    try:
        while True:
            ok = check_pmhq_http(base_url)
            ts = time.strftime("%H:%M:%S")
            print(f"[launch] {ts} {base_url} ok={ok}", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        on_sig(0, None)


def json_status(ok: bool, url: str) -> str:
    return json.dumps({"ok": ok, "url": url}, ensure_ascii=False)


if __name__ == "__main__":
    _main_cli()
