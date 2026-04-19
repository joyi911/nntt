# -*- coding: utf-8 -*-
"""
独立的私聊语音发送封装：本地音频 -> 可选转 silk -> 写入 PMHQ RichMedia 路径 -> sendMsg。

风格对齐 ``group_contact_card.py``：

- ``prepare_private_record_source``：仅负责解析时长与可选转码
- ``send_private_record``：负责上传富媒体、组装 pttElement 并发送给好友
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, Optional, Union

from pmhq_integration.client import PMHQWsClient, send_private_elements

try:
    import pysilk  # type: ignore[import-not-found]
except Exception:
    pysilk = None


SILK_PCM_SAMPLE_RATE = 16000
SILK_BIT_RATE = 16000
SILK_CACHE_META_VERSION = 1


def _candidate_runtime_roots() -> list[Path]:
    roots: list[Path] = []
    for p in (
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
        Path(__file__).resolve().parent.parent,
        Path.cwd(),
    ):
        if not p:
            continue
        p = Path(p).resolve()
        if p not in roots:
            roots.append(p)
    return roots


def _find_bundled_binary(file_name: str) -> Optional[str]:
    for root in _candidate_runtime_roots():
        for candidate in (
            root / "ffmpeg" / file_name,
            root / file_name,
            root / "bin" / file_name,
        ):
            if candidate.is_file():
                return str(candidate.resolve())
    return None


def _refresh_windows_path_from_registry() -> None:
    """合并注册表中的 Machine/User PATH，解决新装 ffmpeg 后当前终端未继承的问题。"""
    if sys.platform != "win32":
        return
    try:
        import winreg
    except ImportError:
        return
    chunks: list[str] = []
    for root, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ):
        try:
            with winreg.OpenKey(root, subkey) as key:
                path_val, _ = winreg.QueryValueEx(key, "PATH")
        except OSError:
            continue
        if path_val:
            for part in str(path_val).split(os.pathsep):
                p = os.path.expandvars(part.strip())
                if p:
                    chunks.append(p)
    extra = os.environ.get("PATH", "")
    if extra:
        for part in extra.split(os.pathsep):
            p = part.strip()
            if p and p not in chunks:
                chunks.append(p)
    os.environ["PATH"] = os.pathsep.join(chunks)


def _find_ffmpeg_exe_fallback() -> Optional[str]:
    """优先查运行目录自带 ffmpeg，其次补查 WinGet / 常见目录。"""
    if sys.platform != "win32":
        return None
    bundled = _find_bundled_binary("ffmpeg.exe")
    if bundled:
        return bundled
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        link = Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
        if link.is_file():
            return str(link.resolve())
        pkg_root = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if pkg_root.is_dir():
            for pat in ("Gyan.FFmpeg*", "ffmpeg*"):
                for p in pkg_root.glob(f"{pat}/**/bin/ffmpeg.exe"):
                    if p.is_file():
                        candidates.append(p)
    for base in (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ):
        if not base:
            continue
        for rel in (Path(base) / "ffmpeg" / "bin" / "ffmpeg.exe", Path(base) / "Gyan" / "FFmpeg" / "bin" / "ffmpeg.exe"):
            if rel.is_file():
                candidates.append(rel)
    return str(candidates[0].resolve()) if candidates else None


def resolve_ffmpeg_bin(explicit: Optional[Union[str, Path]]) -> Optional[str]:
    if explicit:
        ep = Path(explicit)
        if ep.is_file():
            return str(ep)
        bundled = _find_bundled_binary(str(explicit))
        if bundled:
            return bundled
        _refresh_windows_path_from_registry()
        return shutil.which(str(explicit)) or shutil.which("ffmpeg")
    bundled = _find_bundled_binary("ffmpeg.exe")
    if bundled:
        return bundled
    _refresh_windows_path_from_registry()
    w = shutil.which("ffmpeg")
    if w:
        return w
    return _find_ffmpeg_exe_fallback()


def resolve_ffprobe_bin(explicit: Optional[Union[str, Path]], ffmpeg_bin: Optional[str]) -> Optional[str]:
    if explicit:
        ep = Path(explicit)
        if ep.is_file():
            return str(ep)
        bundled = _find_bundled_binary(str(explicit))
        if bundled:
            return bundled
        _refresh_windows_path_from_registry()
        return shutil.which(str(explicit)) or shutil.which("ffprobe")
    bundled = _find_bundled_binary("ffprobe.exe")
    if bundled:
        return bundled
    _refresh_windows_path_from_registry()
    w = shutil.which("ffprobe")
    if w:
        return w
    if ffmpeg_bin:
        sibling = Path(ffmpeg_bin).parent / "ffprobe.exe"
        if sibling.is_file():
            return str(sibling)
    fb = _find_ffmpeg_exe_fallback()
    if fb:
        sibling = Path(fb).parent / "ffprobe.exe"
        if sibling.is_file():
            return str(sibling)
    return None


def _convert_to_wav_with_ffmpeg(input_path: Path, output_wav: Path, ffmpeg_bin: str) -> None:
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_path),
            "-ar",
            "24000",
            "-ac",
            "1",
            str(output_wav),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 转 wav 失败: {proc.stderr.strip() or proc.stdout.strip()}")
    if not output_wav.is_file() or output_wav.stat().st_size <= 0:
        raise RuntimeError(f"ffmpeg 转 wav 失败: {output_wav}")


def _convert_to_pcm_with_ffmpeg(input_path: Path, output_pcm: Path, ffmpeg_bin: str) -> None:
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(input_path),
            "-f",
            "s16le",
            "-ar",
            str(SILK_PCM_SAMPLE_RATE),
            "-ac",
            "1",
            str(output_pcm),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 转 pcm 失败: {proc.stderr.strip() or proc.stdout.strip()}")
    if not output_pcm.is_file() or output_pcm.stat().st_size <= 0:
        raise RuntimeError(f"ffmpeg 转 pcm 失败: {output_pcm}")


def _ensure_pysilk_available() -> None:
    if pysilk is not None:
        return
    raise RuntimeError(
        "未安装 Python silk 编码模块 silk-python。"
        "请执行: python -m pip install silk-python"
    )


def _encode_pcm_to_silk_with_python(input_path: Path, output_path: Path) -> None:
    _ensure_pysilk_available()
    with input_path.open("rb") as pcm_fp, output_path.open("wb") as silk_fp:
        pysilk.encode(
            pcm_fp,
            silk_fp,
            SILK_PCM_SAMPLE_RATE,
            SILK_BIT_RATE,
            tencent=True,
        )
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError(f"silk 文件异常: {output_path}")


def _cached_silk_path_for(local_file: Path) -> Path:
    return local_file.with_suffix(".silk")


def _cached_silk_meta_path_for(silk_file: Path) -> Path:
    return silk_file.with_suffix(".silk.meta.json")


def _find_sibling_raw_audio(file_path: Path) -> Optional[Path]:
    base = file_path.with_suffix("")
    for ext in (".m4a", ".mp3", ".wav"):
        cand = Path(str(base) + ext)
        if cand.is_file():
            return cand
    return None


def _write_silk_cache_meta(silk_file: Path, raw_source_file: Path, duration_sec: float) -> None:
    meta = {
        "version": SILK_CACHE_META_VERSION,
        "encoder": "silk-python",
        "tencent": True,
        "sample_rate": SILK_PCM_SAMPLE_RATE,
        "bit_rate": SILK_BIT_RATE,
        "source_file": str(raw_source_file),
        "source_mtime": float(raw_source_file.stat().st_mtime),
        "duration_sec": float(duration_sec),
    }
    _cached_silk_meta_path_for(silk_file).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_silk_cache_meta(silk_file: Path) -> Optional[Dict[str, Any]]:
    meta_path = _cached_silk_meta_path_for(silk_file)
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_silk_cache_compatible(silk_file: Path, raw_source_file: Optional[Path]) -> bool:
    if not silk_file.is_file() or silk_file.stat().st_size <= 0:
        return False
    if raw_source_file is None:
        return True
    meta = _read_silk_cache_meta(silk_file)
    if not meta:
        return False
    try:
        if int(meta.get("version", 0)) != SILK_CACHE_META_VERSION:
            return False
        if str(meta.get("encoder") or "") != "silk-python":
            return False
        if not bool(meta.get("tencent", False)):
            return False
        if int(meta.get("sample_rate", 0)) != SILK_PCM_SAMPLE_RATE:
            return False
        if int(meta.get("bit_rate", 0)) != SILK_BIT_RATE:
            return False
        if Path(str(meta.get("source_file") or "")).resolve() != raw_source_file.resolve():
            return False
        if float(meta.get("source_mtime", 0.0)) != float(raw_source_file.stat().st_mtime):
            return False
    except Exception:
        return False
    return True


def _probe_duration_seconds(file_path: Path) -> Optional[float]:
    if file_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(file_path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return frames / float(rate)
        except Exception:
            pass

    ps = f"""
$w = New-Object -ComObject WMPlayer.OCX
$m = $w.newMedia('{str(file_path).replace("'", "''")}')
$d = [double]$m.duration
[Console]::WriteLine($d)
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        if proc.returncode == 0:
            out = (proc.stdout or "").strip()
            if out:
                v = float(out)
                if v > 0:
                    return v
    except Exception:
        pass
    return None


def _probe_duration_with_ffprobe(file_path: Path, ffprobe_bin: str) -> Optional[float]:
    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        v = float(out)
    except Exception:
        return None
    return v if v > 0 else None


def prepare_private_record_source(
    input_path: Union[str, Path],
    *,
    duration_override: Optional[float] = None,
    no_transcode: bool = False,
    ffmpeg: Optional[Union[str, Path]] = None,
    ffprobe: Optional[Union[str, Path]] = None,
    temp_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    解析本地音频，并在需要时转成 silk。

    返回字典至少包含：
    ``input_file``、``source_file``、``duration_sec``、``cleanup_paths``、``warnings``。
    """
    local_file = Path(input_path).expanduser().resolve()
    if not local_file.is_file():
        raise FileNotFoundError(f"音频文件不存在: {local_file}")

    raw_probe_file = local_file
    sibling_raw = _find_sibling_raw_audio(local_file) if local_file.suffix.lower() == ".silk" else None
    if sibling_raw is not None:
        raw_probe_file = sibling_raw

    ffmpeg_bin = resolve_ffmpeg_bin(ffmpeg)
    ffprobe_bin = resolve_ffprobe_bin(ffprobe, ffmpeg_bin)
    auto_duration = _probe_duration_with_ffprobe(raw_probe_file, ffprobe_bin) if ffprobe_bin else None
    if auto_duration is None:
        auto_duration = _probe_duration_seconds(raw_probe_file)

    warnings = []
    if duration_override is not None:
        duration_sec = float(duration_override)
    elif auto_duration is not None:
        duration_sec = auto_duration
    else:
        duration_sec = 5.0
        warnings.append("无法自动读取音频时长，已回退为 5 秒；可用 duration_override 或脚本参数 --duration 指定。")

    source_file = local_file
    cleanup_paths: list[Path] = []
    temp_root = Path(temp_dir).expanduser().resolve() if temp_dir else Path(tempfile.gettempdir())

    if not no_transcode and local_file.suffix.lower() == ".silk" and sibling_raw is not None:
        if not _is_silk_cache_compatible(local_file, sibling_raw):
            temp_silk = temp_root / f"{local_file.stem}_pmhq_send.silk"
            temp_pcm = temp_root / f"{local_file.stem}_pmhq_send.pcm"
            if not ffmpeg_bin:
                raise RuntimeError(
                    "检测到同名原始音频，但未找到 ffmpeg，无法重建 silk 缓存。"
                    "请将 ffmpeg.exe / ffprobe.exe 放到软件运行目录下的 ffmpeg 文件夹，"
                    "或安装 ffmpeg 并加入 PATH。"
                )
            _convert_to_pcm_with_ffmpeg(sibling_raw, temp_pcm, ffmpeg_bin)
            cleanup_paths.append(temp_pcm)
            _encode_pcm_to_silk_with_python(temp_pcm, temp_silk)
            shutil.copyfile(temp_silk, local_file)
            _write_silk_cache_meta(local_file, sibling_raw, duration_sec)
            cleanup_paths.append(temp_silk)
        source_file = local_file
        return {
            "input_file": raw_probe_file,
            "source_file": source_file,
            "duration_sec": duration_sec,
            "auto_duration": auto_duration,
            "ffmpeg_bin": ffmpeg_bin,
            "ffprobe_bin": ffprobe_bin,
            "used_transcode": True,
            "cleanup_paths": cleanup_paths,
            "warnings": warnings,
        }

    if not no_transcode and local_file.suffix.lower() != ".silk":
        cached_silk = _cached_silk_path_for(local_file)
        if _is_silk_cache_compatible(cached_silk, local_file):
            source_file = cached_silk
            cached_duration = _probe_duration_with_ffprobe(cached_silk, ffprobe_bin) if ffprobe_bin else None
            if cached_duration is None:
                cached_duration = _probe_duration_seconds(cached_silk)
            if cached_duration is not None and duration_override is None:
                duration_sec = cached_duration
            return {
                "input_file": local_file,
                "source_file": source_file,
                "duration_sec": duration_sec,
                "auto_duration": auto_duration,
                "ffmpeg_bin": ffmpeg_bin,
                "ffprobe_bin": ffprobe_bin,
                "used_transcode": True,
                "cleanup_paths": cleanup_paths,
                "warnings": warnings,
            }

        temp_silk = temp_root / f"{local_file.stem}_pmhq_send.silk"
        temp_pcm = temp_root / f"{local_file.stem}_pmhq_send.pcm"
        if not ffmpeg_bin:
            raise RuntimeError(
                "检测到非 .silk 音频输入，但未找到 ffmpeg。"
                "请将 ffmpeg.exe / ffprobe.exe 放到软件运行目录下的 ffmpeg 文件夹，"
                "或安装 ffmpeg 并加入 PATH。"
            )
        _convert_to_pcm_with_ffmpeg(local_file, temp_pcm, ffmpeg_bin)
        cleanup_paths.append(temp_pcm)

        _encode_pcm_to_silk_with_python(temp_pcm, temp_silk)
        cached_silk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(temp_silk, cached_silk)
        _write_silk_cache_meta(cached_silk, local_file, duration_sec)
        source_file = cached_silk
        cleanup_paths.append(temp_silk)

    return {
        "input_file": local_file,
        "source_file": source_file,
        "duration_sec": duration_sec,
        "auto_duration": auto_duration,
        "ffmpeg_bin": ffmpeg_bin,
        "ffprobe_bin": ffprobe_bin,
        "used_transcode": source_file != local_file,
        "cleanup_paths": cleanup_paths,
        "warnings": warnings,
    }


def cleanup_private_record_prepare(prepared: Dict[str, Any]) -> None:
    for path_obj in prepared.get("cleanup_paths") or []:
        try:
            Path(path_obj).unlink()
        except Exception:
            pass


def prepare_private_record_richmedia(client: PMHQWsClient, file_path: Union[str, Path]) -> Dict[str, Any]:
    file_path = Path(file_path).expanduser().resolve()
    md5_hex = hashlib.md5(file_path.read_bytes()).hexdigest()
    req = {
        "md5HexStr": md5_hex,
        "fileName": file_path.name,
        "elementType": 4,
        "elementSubType": 0,
        "thumbSize": 0,
        "needCreate": True,
        "downloadType": 1,
        "file_uuid": "",
    }
    media_path = client.call(
        "wrapperSession.getMsgService().getRichMediaFilePathForGuild",
        [req],
        timeout=30.0,
    )
    target = Path(str(media_path)).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(file_path.read_bytes())
    return {"md5": md5_hex, "path": str(target), "name": file_path.name, "size": target.stat().st_size}


def build_private_record_element(prepared_media: Dict[str, Any], *, duration_sec: float) -> Dict[str, Any]:
    return {
        "elementType": 4,
        "elementId": "",
        "pttElement": {
            "fileName": prepared_media["name"],
            "filePath": prepared_media["path"],
            "md5HexStr": prepared_media["md5"],
            "fileSize": str(prepared_media["size"]),
            "duration": int(max(1, round(duration_sec))),
            "formatType": 1,
            "voiceType": 1,
            "voiceChangeType": 0,
            "canConvert2Text": True,
            "waveAmplitudes": [0, 18, 9, 23, 16, 17, 16, 15, 44, 17, 24, 20, 14, 15, 17],
            "fileSubId": "",
            "playState": 1,
            "autoConvertText": 0,
            "storeID": 0,
            "otherBusinessInfo": {"aiVoiceType": 0},
        },
    }


def send_private_record(
    client: PMHQWsClient,
    friend_uin: Union[int, str],
    file_path: Union[str, Path],
    *,
    duration_override: Optional[float] = None,
    no_transcode: bool = False,
    ffmpeg: Optional[Union[str, Path]] = None,
    ffprobe: Optional[Union[str, Path]] = None,
    wait_timeout: float = 90.0,
    verbose: bool = True,
    prefer_buddy_list: bool = True,
    cleanup_temp_files: bool = True,
) -> Dict[str, Any]:
    """
    向好友私聊发送本地原声音频。

    实现不依赖脚本内联逻辑，而是自行完成：
    ``prepare_private_record_source -> prepare_private_record_richmedia -> build_private_record_element -> send_private_elements``。
    """
    prepared = prepare_private_record_source(
        file_path,
        duration_override=duration_override,
        no_transcode=no_transcode,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )
    try:
        prepared_media = prepare_private_record_richmedia(client, prepared["source_file"])
        element = build_private_record_element(prepared_media, duration_sec=float(prepared["duration_sec"]))
        response = send_private_elements(
            client,
            friend_uin,
            [element],
            wait_confirm=True,
            wait_timeout=wait_timeout,
            verbose=verbose,
            confirm_text_hint=None,
            prefer_buddy_list=prefer_buddy_list,
        )
        return {
            "ok": True,
            "uin": str(friend_uin).strip(),
            "file": str(prepared["input_file"]),
            "send_file": str(prepared["source_file"]),
            "duration_sec": prepared["duration_sec"],
            "used_transcode": prepared["used_transcode"],
            "warnings": list(prepared.get("warnings") or []),
            "response": response,
        }
    finally:
        if cleanup_temp_files:
            cleanup_private_record_prepare(prepared)
