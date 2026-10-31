"""任务生命周期接口。"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core import control
from ..core.events import utcnow
from ..core.progress import StepTracker
from ..core.storage import ensure_task_dirs, task_dir
from ..db import get_db
from ..models import Artifact, Task, TaskEvent, UploadSession
from ..runtime import events, task_queue
from ..schemas import LocalTaskCreate, TaskCreate, task_detail, task_summary

router = APIRouter()

VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


def _get_task(db: Session, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _new_task(db: Session, **kwargs) -> Task:
    now = utcnow()
    task_id = uuid.uuid4().hex
    ensure_task_dirs(task_id)
    task = Task(
        id=task_id,
        steps_json=StepTracker.from_json("[]").to_json(),
        outputs_json="{}",
        created_at=now,
        updated_at=now,
        **kwargs,
    )
    db.add(task)
    db.commit()
    return task


@router.post("/tasks", status_code=201)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
    if payload.mode == "local":
        raise HTTPException(
            status_code=400,
            detail="本地模式请调用 POST /api/v1/tasks/local 并传入本机绝对路径（视频不上传）",
        )
    task = _new_task(
        db,
        mode="cloud",
        status="created",
        source_name=payload.source_name,
        source_size=payload.source_size,
        person_policy=payload.person_policy,
    )
    events.emit(task.id, "task.status",
                {"status": "created", "current_step": None, "overall": 0.0, "error": None})
    return task_detail(task)


@router.post("/tasks/local", status_code=201)
def create_local_task(payload: LocalTaskCreate, db: Session = Depends(get_db)):
    path = Path(payload.path).expanduser()
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"文件不存在: {payload.path}")
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不支持的视频格式: {path.suffix}")
    resolved = path.resolve()
    task = _new_task(
        db,
        mode="local",
        status="queued",
        source_name=resolved.name,
        source_size=resolved.stat().st_size,
        source_path=str(resolved),
        video_path=str(resolved),
        person_policy=payload.person_policy,
    )
    events.emit(task.id, "task.status",
                {"status": "queued", "current_step": None, "overall": 0.0, "error": None})
    task_queue.enqueue(task.id)
    return task_detail(task)


@router.get("/tasks")
def list_tasks(
    status: Optional[str] = Query(default=None),
    mode: Optional[str] = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Task)
    if status:
        query = query.filter(Task.status == status)
    if mode:
        query = query.filter(Task.mode == mode)
    total = query.count()
    rows = (
        query.order_by(Task.created_at.desc(), Task.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [task_summary(task) for task in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    return task_detail(_get_task(db, task_id))


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if task.status in ("completed", "failed", "canceled"):
        return task_detail(task)
    control.cancel(task_id)
    if task.status in ("created", "uploading", "queued", "interrupted"):
        task.status = "canceled"
        task.finished_at = utcnow()
        task.updated_at = utcnow()
        task.error_json = json.dumps(
            {"code": "canceled", "message": "任务已取消", "retryable": False, "suggestion": None},
            ensure_ascii=False,
        )
        db.commit()
        events.emit(task_id, "task.status",
                    {"status": "canceled", "current_step": task.current_step,
                     "overall": task.overall_progress, "error": json.loads(task.error_json)})
    # running 状态由流水线在下一个检查点自行收尾
    return task_detail(task)


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if task.status not in ("failed", "interrupted", "canceled"):
        raise HTTPException(status_code=409, detail=f"当前状态 {task.status} 不支持重试")
    if task.mode == "cloud":
        video = Path(task.video_path or "")
        if not video.exists():
            raise HTTPException(status_code=409, detail="视频文件已丢失，请新建任务并重新上传")
    control.clear(task_id)
    tracker = StepTracker.from_json(task.steps_json)
    for step in tracker.steps:
        if step["status"] != "completed":
            step["status"] = "pending"
            step["progress"] = 0.0
            step["error"] = None
            step["started_at"] = None
            step["finished_at"] = None
            step["detail"] = {"current": None, "total": None, "unit": None, "message": ""}
    task.steps_json = tracker.to_json()
    task.overall_progress = tracker.overall()
    task.status = "queued"
    task.error_json = None
    task.finished_at = None
    task.retry_count = (task.retry_count or 0) + 1
    task.updated_at = utcnow()
    db.commit()
    events.emit(task_id, "task.status",
                {"status": "queued", "current_step": None,
                 "overall": task.overall_progress, "error": None})
    task_queue.enqueue(task_id)
    return task_detail(task)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if task.status in ("running", "queued", "uploading"):
        raise HTTPException(status_code=409, detail="请先取消任务，再删除")
    control.cancel(task_id)
    db.query(Artifact).filter(Artifact.task_id == task_id).delete()
    db.query(TaskEvent).filter(TaskEvent.task_id == task_id).delete()
    db.query(UploadSession).filter(UploadSession.task_id == task_id).delete()
    db.delete(task)
    db.commit()
    shutil.rmtree(task_dir(task_id), ignore_errors=True)
    return {"deleted": True, "task_id": task_id}
