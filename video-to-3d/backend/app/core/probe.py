"""硬件与引擎能力探测：供本地模式决策与“是否引导切换云端”。"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from ..config import settings
from ..engines import capabilities

_cache: dict = {"ts": 0.0, "data": None}


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str], timeout: int = 8) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _gpu() -> dict | None:
    raw = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if not raw:
        return None
    first = raw.splitlines()[0]
    try:
        name, vram_mb = [part.strip() for part in first.split(",")[:2]]
        return {"name": name, "vram_gb": round(float(vram_mb) / 1024, 1)}
    except (ValueError, IndexError):
        return {"name": first, "vram_gb": None}


def _ram_gb() -> float | None:
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
        except (ValueError, IndexError):
            return None
    return None


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def probe(force: bool = False) -> dict:
    now = time.monotonic()
    if not force and _cache["data"] and now - _cache["ts"] < 60:
        return _cache["data"]

    gpu = _gpu()
    ffmpeg = _which("ffmpeg")
    colmap = _which("colmap")
    engines = capabilities()
    disk = shutil.disk_usage(str(settings.data_dir))

    blockers: list[str] = []
    if not ffmpeg:
        blockers.append("未安装 ffmpeg，无法抽帧")
    if not gpu:
        blockers.append("未检测到 NVIDIA GPU")
    elif (gpu.get("vram_gb") or 0) < 8:
        blockers.append(f"显存不足（{gpu.get('vram_gb')} GB < 8 GB）")
    if not colmap:
        blockers.append("未安装 COLMAP，无法做相机位姿估计")
    local_scene = next((e for e in engines if e["name"] in ("ooosplat", "mock")), None)
    if not local_scene or not local_scene["available"]:
        blockers.append("本地 3DGS 引擎未配置（OOOSplat）")

    data = {
        "device": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "ram_gb": _ram_gb(),
            "gpu": gpu,
            "disk_free_gb": round(disk.free / 1024**3, 1),
        },
        "tools": {
            "ffmpeg": bool(ffmpeg),
            "ffprobe": bool(_which("ffprobe")),
            "colmap": bool(colmap),
            "blender": bool(_which("blender")),
            "mediapipe": _has_module("mediapipe"),
            "torch": _has_module("torch"),
            "gsplat": _has_module("gsplat"),
        },
        "engines": engines,
        "recommendation": {
            "mode": "local" if not blockers else "cloud",
            "blockers": blockers,
            "reason": "本机满足本地处理条件" if not blockers else "本机不满足本地处理条件",
        },
        "engine_mode": settings.engine_mode,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _cache.update({"ts": now, "data": data})
    return data
