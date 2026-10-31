"""流水线编排：步骤状态机、真实进度、取消、失败归类、断点续跑。

同一份代码在本地 sidecar 与云端 worker 上运行；差异只在引擎选择与存储实现。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import settings
from ..db import SessionLocal
from ..engines import (
    EngineContext,
    EngineUnavailable,
    Job,
    appearance_engine,
    detect,
    frames,
    motion_engine,
    scene_engine,
)
from ..engines.base import EngineFailed
from ..models import Artifact, Task
from ..schemas import file_mime, task_detail
from . import control, packager
from .events import EventService, utcnow
from .progress import StepTracker
from .storage import append_log, atomic_write_json, ensure_task_dirs, sha256_file, task_dir

STEP_UNITS = {
    "extract_frames": "帧",
    "detect_person": "帧",
    "camera_pose": "张",
    "scene_reconstruct": "iter",
    "person_motion": "帧",
    "person_appearance": "轮",
}
FINAL_STATUSES = {"completed", "failed", "skipped", "canceled"}


class TaskFailed(Exception):
    def __init__(self, code: str, message: str, suggestion: Optional[str] = None,
                 retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.suggestion = suggestion
        self.retryable = retryable


class SkipStep(Exception):
    """步骤不适用（非错误）：例如画面中无人、引擎按策略关闭。"""

    def __init__(self, reason: str, warning: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.warning = warning


class Reporter:
    """把步骤状态、整体进度、日志同步到 DB 与 SSE（节流 250ms）。"""

    throttle = 0.25

    def __init__(self, session, task: Task, tracker: StepTracker, events: EventService):
        self.session = session
        self.task = task
        self.tracker = tracker
        self.events = events
        self.task_id = task.id
        self.log_path = task_dir(task.id) / "logs" / "pipeline.log"
        self._last_publish = 0.0

    # ---- 持久化 ----
    def persist(self) -> None:
        self.task.steps_json = self.tracker.to_json()
        self.task.updated_at = utcnow()
        self.task.overall_progress = max(
            float(self.task.overall_progress or 0.0), self.tracker.overall()
        )
        self.task.current_step = self.tracker.current() or self.task.current_step
        self.session.commit()
        # 本地可拷走的快照：与任务表字段同构，断点排查不依赖 DB
        try:
            atomic_write_json(
                task_dir(self.task_id) / "task.json",
                {**task_detail(self.task), "snapshot_at": utcnow()},
            )
        except (OSError, TypeError, ValueError):
            pass

    def _emit_step(self, key: str, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_publish < self.throttle:
            return
        self._last_publish = now
        step = self.tracker.get(key)
        self.events.emit(
            self.task_id,
            "step.update",
            {
                "key": key,
                "title": step["title"],
                "status": step["status"],
                "progress": round(float(step["progress"]), 4),
                "detail": step["detail"],
                "overall": round(float(self.task.overall_progress or 0.0), 4),
            },
        )

    def step(
        self,
        key: str,
        *,
        status: Optional[str] = None,
        progress: Optional[float] = None,
        detail: Optional[dict] = None,
        error: Optional[str] = None,
        message: Optional[str] = None,
        force: bool = False,
    ) -> None:
        if message is not None:
            detail = dict(detail or {})
            detail["message"] = message
        self.tracker.update(
            key, status=status, progress=progress, detail=detail, error=error,
            timestamp=utcnow(),
        )
        force = force or status in FINAL_STATUSES
        self.persist()
        self._emit_step(key, force)

    def skip(self, key: str, reason: str, warning: bool = False) -> None:
        self.tracker.skip(key, reason, timestamp=utcnow())
        self.persist()
        self._emit_step(key, force=True)
        self.log("warning" if warning else "info", f"跳过 {key}：{reason}", step=key)

    def status(self, status: str, error: Optional[dict] = None) -> None:
        self.task.status = status
        self.task.updated_at = utcnow()
        if status == "running" and not self.task.started_at:
            self.task.started_at = utcnow()
        if status in ("completed", "failed", "canceled"):
            self.task.finished_at = utcnow()
        self.task.error_json = json.dumps(error, ensure_ascii=False) if error else None
        if status == "completed":
            self.task.overall_progress = 1.0
            self.task.current_step = None
        self.session.commit()
        self.events.emit(
            self.task_id,
            "task.status",
            {
                "status": status,
                "current_step": self.task.current_step,
                "overall": round(float(self.task.overall_progress or 0.0), 4),
                "error": error,
            },
        )

    def log(self, level: str, message: str, step: Optional[str] = None) -> None:
        append_log(self.log_path, f"[{utcnow()}] [{level}] {message}")
        self.events.emit(
            self.task_id,
            "log",
            {"level": level, "step": step, "message": message, "ts": utcnow()},
        )

    def engine_report(
        self, step: str, ratio: float, current: Optional[int], total: Optional[int], message: str
    ) -> None:
        self.step(
            step,
            progress=ratio,
            detail={
                "current": current,
                "total": total,
                "unit": STEP_UNITS.get(step),
                "message": message,
            },
        )

    def fail(self, code: str, message: str, suggestion: Optional[str], retryable: bool = True) -> None:
        current = self.tracker.current()
        if current:
            self.step(current, status="failed", error=message, force=True)
        self.log("error", message)
        self.status(
            "failed",
            error={
                "code": code,
                "message": message,
                "retryable": retryable,
                "suggestion": suggestion,
            },
        )


def _first(directory: Path, suffixes: tuple[str, ...]) -> Optional[Path]:
    if not directory.exists():
        return None
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in suffixes:
            return path
    return None


def _run_step(
    rep: Reporter, key: str, fn: Callable[[], Optional[str]], *, skip_warning: bool = False
) -> None:
    step = rep.tracker.get(key)
    if step["status"] in ("completed", "skipped"):
        return
    control.raise_if_canceled(rep.task_id)
    rep.step(key, status="running", progress=step["progress"], message="", force=True)
    try:
        message = fn()
    except SkipStep as exc:
        rep.skip(key, exc.reason, warning=exc.warning or skip_warning)
        return
    rep.step(key, status="completed", progress=1.0, message=message or "完成", force=True)


def _make_context(rep: Reporter, job: Job) -> EngineContext:
    return EngineContext(
        task_id=job.task_id,
        workspace=task_dir(job.task_id),
        report=rep.engine_report,
        log=rep.log,
        stop=control.control_event(job.task_id),
    )


def _execute(task: Task, tracker: StepTracker, rep: Reporter, job: Job, ws: Path, session) -> None:
    rep.status("running", error=None)
    engine_meta: dict[str, Any] = {"simulated": False}

    if task.person_policy == "none":
        for key in ("detect_person", "person_motion", "person_appearance"):
            rep.skip(key, "用户选择不提取人物（person_policy=none）")

    def step_prepare() -> str:
        if not job.video.exists():
            raise TaskFailed("video_missing", f"找不到视频文件: {job.video}", "重新上传视频")
        meta = frames.probe_video(job.video)
        atomic_write_json(ws / "work" / "media.json", meta)
        rep.log("info", f"媒体信息: {meta}")
        return (
            f"{meta['width']}×{meta['height']} · {meta['duration']:.1f}s · "
            f"{meta['fps']:.2f}fps"
        )

    def step_frames() -> str:
        ctx = _make_context(rep, job)
        result = frames.extract_frames(job, ctx, settings.sample_fps, settings.max_frames)
        frames.make_thumbnail(job)
        return f"抽取 {result.meta.get('frames', 0)} 帧"

    def step_detect() -> str:
        ctx = _make_context(rep, job)
        result = detect.detect(job, ctx)
        if not result["available"]:
            for key in ("person_motion", "person_appearance"):
                rep.skip(key, "人物检测不可用，无法确认是否有人物", warning=True)
            if task.person_policy in ("motion_only", "appearance"):
                raise TaskFailed(
                    "person_detector_missing",
                    "未安装 mediapipe，无法执行人物检测",
                    "安装 mediapipe，或选择“不提取人物”，或切换云端处理",
                )
            raise SkipStep("未安装 mediapipe，跳过人物检测", warning=True)
        ratio = float(result["person_ratio"])
        present = ratio >= 0.30
        task.person_detected = 1 if present else 0
        session.commit()
        if not present:
            for key in ("person_motion", "person_appearance"):
                rep.skip(key, "视频中未检测到人物")
        return f"{result['person_frames']}/{result['sampled']} 帧含人体（{ratio:.0%}）"

    def step_pose() -> str:
        engine = scene_engine(task.mode)
        engine_meta["scene"] = engine.name
        engine_meta["simulated"] = bool(getattr(engine, "simulated", False))
        ctx = _make_context(rep, job)
        engine.estimate_poses(job, ctx)
        return f"{engine.name} 完成相机位姿估计"

    def step_train() -> str:
        engine = scene_engine(task.mode)
        engine_meta["scene"] = engine.name
        ctx = _make_context(rep, job)
        result = engine.train(job, ctx)
        engine_meta["simulated"] = engine_meta.get("simulated") or bool(result.simulated)
        return f"{engine.name} 完成场景重建（{len(result.artifacts)} 个文件）"

    def step_motion() -> str:
        if not task.person_detected:
            raise SkipStep("未检测到人物，跳过动作提取")
        engine = motion_engine(task.mode)
        if not engine.available():
            if task.person_policy == "motion_only":
                raise TaskFailed(
                    "motion_engine_unavailable",
                    "动作提取引擎不可用",
                    "配置 FreeMoCap（注意 AGPL 合规），或切换云端处理",
                )
            raise SkipStep("FreeMoCap 未配置，跳过动作提取", warning=True)
        ctx = _make_context(rep, job)
        result = engine.extract(job, ctx)
        engine_meta["motion"] = engine.name
        engine_meta["simulated"] = engine_meta.get("simulated") or bool(result.simulated)
        return f"输出 {len(result.artifacts)} 个动作文件"

    def step_appearance() -> str:
        if not task.person_detected:
            raise SkipStep("未检测到人物，跳过人物外观重建")
        if task.person_policy == "motion_only":
            raise SkipStep("按策略仅提取动作数据")
        engine = appearance_engine(task.mode)
        if not engine.available():
            if task.person_policy == "appearance":
                raise TaskFailed(
                    "appearance_engine_unavailable",
                    "人物外观重建不可用",
                    "需云端 GPU + 已取得的 SMPL 体模授权；或改选“仅动作数据”",
                )
            raise SkipStep("人物外观重建未启用，跳过", warning=True)
        motion_file = _first(job.motion_dir(), (".bvh", ".fbx", ".csv"))
        ctx = _make_context(rep, job)
        result = engine.reconstruct(job, ctx, str(motion_file) if motion_file else None)
        engine_meta["appearance"] = engine.name
        engine_meta["simulated"] = engine_meta.get("simulated") or bool(result.simulated)
        return f"输出 {len(result.artifacts)} 个人物模型文件"

    def step_export() -> str:
        scene_files = list(job.scene_dir().glob("*")) if job.scene_dir().exists() else []
        if not scene_files:
            raise TaskFailed(
                "missing_scene",
                "没有可导出的场景模型",
                "查看 logs/pipeline.log，或改用其他场景引擎",
            )
        formats = ", ".join(sorted({p.suffix.lstrip(".") for p in scene_files if p.is_file()}))
        return f"场景格式: {formats or 'n/a'}"

    def step_package() -> str:
        rep.status("packaging")
        zip_path, manifest, count = packager.build_package(task, ws, tracker, engine_meta)
        _register_artifacts(session, task, ws, zip_path)
        task.outputs_json = json.dumps(_collect_outputs(ws), ensure_ascii=False)
        task.status = "completed"
        task.finished_at = utcnow()
        rep.persist()
        for artifact in session.query(Artifact).filter(Artifact.task_id == task.id).all():
            rep.events.emit(
                task.id,
                "artifact.ready",
                {
                    "artifact_id": artifact.id,
                    "kind": artifact.kind,
                    "filename": artifact.filename,
                    "size": artifact.size,
                    "download_url": f"/api/v1/tasks/{task.id}/artifacts/{artifact.id}",
                },
            )
        rep.step("package", status="completed", progress=1.0,
                 message=f"已打包 {count} 个文件", force=True)
        rep.status("completed")
        return f"{zip_path.name}（{count} 个文件）"

    _run_step(rep, "prepare", step_prepare)
    _run_step(rep, "extract_frames", step_frames)
    _run_step(rep, "detect_person", step_detect)
    _run_step(rep, "camera_pose", step_pose)
    _run_step(rep, "scene_reconstruct", step_train)
    _run_step(rep, "person_motion", step_motion)
    _run_step(rep, "person_appearance", step_appearance)
    _run_step(rep, "texture_optimize", lambda: "使用重建引擎内置的颜色/纹理优化")
    _run_step(rep, "export", step_export)
    _run_step(rep, "package", step_package)


def _collect_outputs(ws: Path) -> dict:
    def rel(path: Optional[Path]) -> Optional[str]:
        if not path or not path.exists():
            return None
        try:
            return path.relative_to(ws).as_posix()
        except ValueError:
            return path.name

    return {
        "scene": rel(_first(ws / "output" / "scene", (".ply", ".glb", ".splat"))),
        "person": rel(_first(ws / "output" / "person", (".glb", ".fbx", ".obj"))),
        "motion": rel(_first(ws / "output" / "motion", (".bvh", ".fbx", ".csv"))),
        "preview": rel(_first(ws / "output" / "preview", (".jpg", ".png", ".mp4", ".html"))),
        "package": rel(ws / "package" / f"{ws.name}.zip"),
    }


def _register_artifacts(session, task: Task, ws: Path, zip_path: Path) -> None:
    session.query(Artifact).filter(Artifact.task_id == task.id).delete()
    session.flush()
    entries: list[tuple[str, Path]] = []
    for kind in ("scene", "person", "motion", "preview"):
        directory = ws / "output" / kind
        if directory.exists():
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    entries.append((kind, path))
    entries.append(("package", zip_path))
    for kind, path in entries:
        session.add(
            Artifact(
                task_id=task.id,
                kind=kind,
                filename=path.name,
                path=str(path),
                mime=file_mime(str(path)),
                size=path.stat().st_size,
                sha256=sha256_file(path) if path.stat().st_size < 512 * 1024 * 1024 else None,
                created_at=utcnow(),
            )
        )
    session.commit()


def run_pipeline(task_id: str, events: EventService) -> None:
    session = SessionLocal()
    try:
        task = session.get(Task, task_id)
        if not task or task.status in ("completed", "canceled", "created", "uploading"):
            return
        control.clear(task_id)
        ws = ensure_task_dirs(task_id)
        tracker = StepTracker.from_json(task.steps_json)
        job = Job(
            task_id=task_id,
            video=Path(task.video_path or ""),
            frames_dir=ws / "frames",
            work_dir=ws / "work",
            out_dir=ws / "output",
            person_policy=task.person_policy,
        )
        rep = Reporter(session, task, tracker, events)
        try:
            _execute(task, tracker, rep, job, ws, session)
        except control.TaskCanceled:
            current = tracker.current()
            if current:
                rep.step(current, status="canceled", force=True, message="已取消")
            rep.status("canceled")
            rep.log("warning", "任务已取消")
        except SkipStep as exc:  # 兜底：步骤外抛出
            rep.log("warning", exc.reason)
        except TaskFailed as exc:
            rep.fail(exc.code, str(exc), exc.suggestion, exc.retryable)
        except EngineUnavailable as exc:
            rep.fail("engine_unavailable", str(exc), exc.suggestion, retryable=True)
        except EngineFailed as exc:
            rep.log("error", exc.tail or str(exc))
            rep.fail("engine_failed", str(exc), "查看日志后重试，或切换引擎/云端", retryable=True)
        except Exception as exc:  # noqa: BLE001 - 需要把未知异常收敛成任务失败
            rep.log("error", f"未预期异常: {exc}")
            rep.fail("internal_error", f"内部错误: {exc}", "重试任务；若持续失败请联系支持",
                     retryable=False)
    finally:
        session.close()
