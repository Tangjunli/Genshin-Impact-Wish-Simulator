"""ffmpeg 抽帧与媒体解析。这是本地/云端都会真实执行的步骤，进度来自 ffmpeg 自身输出。"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .base import EngineContext, EngineUnavailable, EngineResult, Job, run_command


def _binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise EngineUnavailable(
            name,
            f"未找到 {name}，无法解析或抽帧",
            "安装 ffmpeg（桌面端可随包分发）后重试，或切换到云端处理",
        )
    return path


def probe_video(video: Path) -> dict:
    ffprobe = _binary("ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,duration,nb_frames",
        "-show_entries",
        "format=duration,size,bit_rate",
        "-of",
        "json",
        str(video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise EngineUnavailable("ffprobe", f"无法读取视频信息: {proc.stderr.strip()[:200]}",
                                "确认视频文件完整，或重新上传")
    info = json.loads(proc.stdout or "{}")
    stream = (info.get("streams") or [{}])[0]
    fmt = info.get("format") or {}

    def _fps(raw: str) -> float:
        try:
            num, den = raw.split("/")
            return float(num) / float(den) if float(den) else 0.0
        except (ValueError, AttributeError):
            return 0.0

    duration = float(stream.get("duration") or fmt.get("duration") or 0)
    fps = _fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
    return {
        "duration": round(duration, 3),
        "fps": round(fps, 3),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "size": int(fmt.get("size") or 0),
        "bit_rate": int(fmt.get("bit_rate") or 0),
        "has_video": bool(stream.get("width")),
    }


def extract_frames(
    job: Job, ctx: EngineContext, sample_fps: float = 2.0, max_frames: int = 360
) -> EngineResult:
    ffmpeg = _binary("ffmpeg")
    meta = probe_video(job.video)
    if not meta["has_video"]:
        raise EngineUnavailable("ffmpeg", "视频中没有可解码的视频流", "请换一个包含画面内容的视频")

    duration = meta["duration"] or 0
    estimated = max(1, min(max_frames, int(duration * sample_fps) + 1))
    ctx.log("info", f"视频 {meta['width']}x{meta['height']} · {duration:.1f}s · 计划抽帧 {estimated} 张")

    job.frames_dir.mkdir(parents=True, exist_ok=True)
    for old in job.frames_dir.glob("*.jpg"):
        old.unlink()

    pattern = re.compile(r"^frame=(\d+)")

    def on_line(line: str) -> None:
        match = pattern.match(line.strip())
        if not match:
            return
        current = int(match.group(1))
        ctx.progress("extract_frames", min(current, estimated), estimated,
                     f"正在抽帧：{min(current, estimated)}/{estimated}")

    out_pattern = str(job.frames_dir / "%05d.jpg")
    cmd = (
        f"{shlex.quote(ffmpeg)} -y -hide_banner -loglevel error "
        f"-i {shlex.quote(str(job.video))} "
        f'-vf "fps={sample_fps},scale=-2:720" -q:v 3 '
        f"-progress pipe:1 -nostats {shlex.quote(out_pattern)}"
    )
    run_command(cmd, ctx=ctx, step="extract_frames", on_line=on_line)

    frames = sorted(job.frames_dir.glob("*.jpg"))
    actual = len(frames)
    ctx.progress("extract_frames", actual, actual, f"已抽取 {actual} 帧")
    ctx.log("info", f"抽帧完成：{actual} 张")
    return EngineResult(meta={"frames": actual, "source_meta": meta, "fps_sample": sample_fps})


def make_thumbnail(job: Job) -> Optional[Path]:
    """生成缩略图（真实取自视频），失败不影响主流程。"""
    try:
        ffmpeg = _binary("ffmpeg")
    except EngineUnavailable:
        return None
    job.preview_dir().mkdir(parents=True, exist_ok=True)
    target = job.preview_dir() / "thumbnail.jpg"
    try:
        meta = probe_video(job.video)
    except EngineUnavailable:
        meta = {"duration": 0}
    seek = max(0.0, min(2.0, (meta.get("duration") or 0) * 0.1))
    cmd = (
        f"{shlex.quote(ffmpeg)} -y -hide_banner -loglevel error -ss {seek:.2f} "
        f"-i {shlex.quote(str(job.video))} -frames:v 1 -vf scale=640:-2 -q:v 3 "
        f"{shlex.quote(str(target))}"
    )
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    return target if proc.returncode == 0 and target.exists() else None
