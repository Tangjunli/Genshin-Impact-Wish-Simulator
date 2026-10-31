"""引擎通用协议与工具：子进程执行、命令行模板、进度解析。"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..core.control import TaskCanceled

PROGRESS_RE = re.compile(r"(\d{1,7})\s*[\/|]\s*(\d{1,7})")


class EngineUnavailable(RuntimeError):
    """引擎未配置/环境不满足。error_json 中会带上 suggestion，用于“切换云端”引导。"""

    def __init__(self, engine: str, message: str, suggestion: str | None = None):
        super().__init__(message)
        self.engine = engine
        self.suggestion = suggestion


class EngineFailed(RuntimeError):
    def __init__(self, engine: str, message: str, tail: str = ""):
        super().__init__(message)
        self.engine = engine
        self.tail = tail


@dataclass
class Job:
    task_id: str
    video: Path
    frames_dir: Path
    work_dir: Path
    out_dir: Path
    person_policy: str = "auto"

    def scene_dir(self) -> Path:
        return self.out_dir / "scene"

    def person_dir(self) -> Path:
        return self.out_dir / "person"

    def motion_dir(self) -> Path:
        return self.out_dir / "motion"

    def preview_dir(self) -> Path:
        return self.out_dir / "preview"


@dataclass
class EngineContext:
    task_id: str
    workspace: Path
    report: Callable[[str, float, Optional[int], Optional[int], str], None]
    log: Callable[[str, str], None]
    stop: threading.Event

    def should_stop(self) -> bool:
        return self.stop.is_set()

    def progress(
        self, step: str, current: float, total: Optional[float], message: str = ""
    ) -> None:
        if total and total > 0:
            ratio = min(0.99, max(0.0, float(current) / float(total)))
        else:
            ratio = min(0.99, max(0.0, float(current)))
        self.report(step, ratio, int(current) if current is not None else None,
                    int(total) if total else None, message)

    def check_cancel(self) -> None:
        if self.stop.is_set():
            raise TaskCanceled("任务已取消")


@dataclass
class EngineResult:
    artifacts: List[Path] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    simulated: bool = False


def expand(template: str, job: Job, extra: Dict[str, str] | None = None) -> str:
    mapping = {
        "{video}": str(job.video),
        "{frames}": str(job.frames_dir),
        "{work}": str(job.work_dir),
        "{out}": str(job.out_dir),
        "{scene}": str(job.scene_dir()),
        "{person}": str(job.person_dir()),
        "{motion}": str(job.motion_dir()),
        "{preview}": str(job.preview_dir()),
        "{task_id}": job.task_id,
    }
    if extra:
        mapping.update({f"{{{k}}}": str(v) for k, v in extra.items()})
    result = template
    for key, value in mapping.items():
        result = result.replace(key, value)
    return result


def run_command(
    cmd: str,
    *,
    ctx: EngineContext,
    step: str,
    progress_re: re.Pattern = PROGRESS_RE,
    total_hint: Optional[int] = None,
    cwd: Optional[Path] = None,
    timeout: Optional[int] = None,
    on_line: Optional[Callable[[str], None]] = None,
) -> int:
    """执行外部命令，按行转发进度与日志；取消时终止子进程。

    `progress` 来自工具真实输出（如 ffmpeg 的 frame=、训练的 iteration），不做时间估算。
    """
    ctx.log("info", f"执行: {cmd}")
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    except FileNotFoundError as exc:
        raise EngineUnavailable("subprocess", f"命令不存在: {cmd}") from exc

    tail: List[str] = []
    last_progress = 0.0
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            if ctx.should_stop():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise TaskCanceled("任务已取消")
            line = raw.rstrip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 200:
                tail.pop(0)
            if on_line is not None:
                on_line(line)
            match = progress_re.search(line)
            if match:
                try:
                    current, total = int(match.group(1)), int(match.group(2))
                except (IndexError, ValueError):
                    current, total = 0, 0
                if total > 0:
                    ratio = min(0.98, current / total)
                    if ratio > last_progress:
                        last_progress = ratio
                        ctx.report(step, ratio, current, total, line[:160])
                continue
            if len(tail) <= 200 and ("[" in line or "stage" in line.lower() or "step" in line.lower()):
                ctx.log("info", line[:300])
        returncode = proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()

    if returncode != 0:
        raise EngineFailed(cmd.split()[0], f"命令退出码 {returncode}", "\n".join(tail[-20:]))
    ctx.report(step, 1.0, None, None, "完成")
    return returncode


def collect(directory: Path, patterns: tuple[str, ...]) -> List[Path]:
    files: List[Path] = []
    if not directory.exists():
        return files
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in patterns:
            files.append(path)
    return files
