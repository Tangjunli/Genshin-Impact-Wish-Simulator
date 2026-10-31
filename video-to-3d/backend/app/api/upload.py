"""分片上传：支持断点续传、幂等重传、服务端进度真值。"""
from __future__ import annotations

import json
import math
import os
import shutil
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..core.events import utcnow
from ..core.storage import sha256_file, task_dir
from ..db import get_db
from ..models import Task, UploadSession
from ..runtime import events, task_queue
from ..schemas import UploadComplete, UploadInit, task_detail

router = APIRouter()

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(task_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(task_id, threading.Lock())


def _parts_dir(task_id: str) -> Path:
    directory = task_dir(task_id) / "upload" / "parts"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _refresh_from_disk(session: UploadSession, task_id: str) -> None:
    parts = _parts_dir(task_id)
    chunks = sorted(int(p.stem) for p in parts.glob("*.part") if p.stem.isdigit())
    uploaded = [i for i in chunks if 0 <= i < session.total_chunks]
    session.uploaded_chunks = json.dumps(uploaded)
    session.received_bytes = sum((parts / f"{i:06d}.part").stat().st_size for i in uploaded)
    session.updated_at = utcnow()


def _payload(session: UploadSession) -> dict:
    uploaded = json.loads(session.uploaded_chunks or "[]")
    percent = round(session.received_bytes / session.total_bytes, 4) if session.total_bytes else 0
    return {
        "task_id": session.task_id,
        "chunk_size": session.chunk_size,
        "total_bytes": session.total_bytes,
        "total_chunks": session.total_chunks,
        "uploaded_chunks": uploaded,
        "received_bytes": session.received_bytes,
        "percent": percent,
    }


def _task(db: Session, task_id: str, mode: str | None = None) -> Task:
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if mode and task.mode != mode:
        raise HTTPException(status_code=400, detail=f"该任务不是 {mode} 模式")
    return task


@router.post("/tasks/{task_id}/upload/init")
def upload_init(task_id: str, payload: UploadInit, db: Session = Depends(get_db)):
    task = _task(db, task_id, "cloud")
    if task.status in ("completed", "canceled"):
        raise HTTPException(status_code=409, detail=f"任务状态 {task.status} 不可上传")

    total_bytes = int(payload.total_bytes)
    chunk_size = settings.chunk_size
    total_chunks = math.ceil(total_bytes / chunk_size)

    with _lock(task_id):
        session = db.get(UploadSession, task_id)
        if session and session.status == "completed" and task.video_path:
            return _payload(session)
        if session and session.total_bytes != total_bytes:
            raise HTTPException(
                status_code=409,
                detail=f"已存在不同大小的上传会话（{session.total_bytes} bytes），请新建任务",
            )
        if session is None:
            session = UploadSession(
                task_id=task_id,
                chunk_size=chunk_size,
                total_bytes=total_bytes,
                total_chunks=total_chunks,
                uploaded_chunks="[]",
                received_bytes=0,
                status="open",
                updated_at=utcnow(),
            )
            db.add(session)
        _refresh_from_disk(session, task_id)
        if task.source_size != total_bytes:
            task.source_size = total_bytes
        if task.status == "created":
            task.status = "uploading"
        task.updated_at = utcnow()
        db.commit()

    events.emit(
        task_id,
        "upload.progress",
        {
            "received_bytes": session.received_bytes,
            "total_bytes": session.total_bytes,
            "percent": round(session.received_bytes / session.total_bytes, 4),
            "chunk_index": None,
            "total_chunks": session.total_chunks,
        },
    )
    events.emit(
        task_id, "task.status",
        {"status": task.status, "current_step": None, "overall": task.overall_progress, "error": None},
    )
    return _payload(session)


@router.put("/tasks/{task_id}/upload/chunks/{index}")
async def upload_chunk(
    task_id: str, index: int, request: Request, db: Session = Depends(get_db)
):
    session = db.get(UploadSession, task_id)
    if not session:
        raise HTTPException(status_code=404, detail="请先调用 upload/init")
    if session.status == "completed":
        raise HTTPException(status_code=409, detail="上传已完成")
    if index < 0 or index >= session.total_chunks:
        raise HTTPException(status_code=400, detail="分片序号越界")

    offset = index * session.chunk_size
    expected = min(session.chunk_size, session.total_bytes - offset)
    declared = request.headers.get("content-length")
    if declared and int(declared) > expected:
        raise HTTPException(status_code=413, detail={"expected_bytes": expected, "declared": int(declared)})

    body = await request.body()
    if len(body) != expected:
        raise HTTPException(
            status_code=400,
            detail={"expected_bytes": expected, "received_bytes": len(body),
                    "message": "分片大小与声明不一致"},
        )

    target = _parts_dir(task_id) / f"{index:06d}.part"
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(body)
    os.replace(tmp, target)

    with _lock(task_id):
        _refresh_from_disk(session, task_id)
        task = db.get(Task, task_id)
        if task and task.status == "created":
            task.status = "uploading"
        db.commit()
        payload = _payload(session)

    events.emit(
        task_id,
        "upload.progress",
        {
            "received_bytes": payload["received_bytes"],
            "total_bytes": payload["total_bytes"],
            "percent": payload["percent"],
            "chunk_index": index,
            "total_chunks": payload["total_chunks"],
        },
    )
    return payload


@router.get("/tasks/{task_id}/upload/status")
def upload_status(task_id: str, db: Session = Depends(get_db)):
    _task(db, task_id, "cloud")
    session = db.get(UploadSession, task_id)
    if not session:
        raise HTTPException(status_code=404, detail="上传会话不存在")
    _refresh_from_disk(session, task_id)
    db.commit()
    return _payload(session)


@router.post("/tasks/{task_id}/upload/complete")
def upload_complete(task_id: str, payload: UploadComplete, db: Session = Depends(get_db)):
    task = _task(db, task_id, "cloud")
    if task.video_path and Path(task.video_path).exists() and task.status in (
        "queued", "running", "packaging", "completed"
    ):
        return task_detail(task)

    session = db.get(UploadSession, task_id)
    if not session:
        raise HTTPException(status_code=404, detail="请先调用 upload/init")

    with _lock(task_id):
        _refresh_from_disk(session, task_id)
        uploaded = json.loads(session.uploaded_chunks or "[]")
        missing = [i for i in range(session.total_chunks) if i not in set(uploaded)]
        if missing:
            raise HTTPException(
                status_code=409,
                detail={"message": "分片不完整", "missing_chunks": missing[:50],
                        "received_bytes": session.received_bytes},
            )

        input_dir = task_dir(task_id) / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        target = input_dir / "video.mp4"
        tmp = input_dir / "video.mp4.part"
        parts = _parts_dir(task_id)
        try:
            with open(tmp, "wb") as out:
                for i in range(session.total_chunks):
                    with open(parts / f"{i:06d}.part", "rb") as fh:
                        shutil.copyfileobj(fh, out)
            size = tmp.stat().st_size
            if size != session.total_bytes:
                raise HTTPException(
                    status_code=409,
                    detail={"message": "合并后大小不一致", "expected": session.total_bytes,
                            "actual": size},
                )
            if payload.sha256:
                actual_sha = sha256_file(tmp)
                if actual_sha.lower() != payload.sha256.lower():
                    raise HTTPException(
                        status_code=409,
                        detail={"message": "SHA-256 校验失败", "expected": payload.sha256,
                                "actual": actual_sha},
                    )
                task.source_sha256 = actual_sha.lower()
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()

        task.video_path = str(target)
        task.source_size = session.total_bytes
        task.status = "queued"
        task.updated_at = utcnow()
        session.status = "completed"
        session.updated_at = utcnow()
        db.commit()

        # 合并成功后清理分片，释放空间
        for part in parts.glob("*.part"):
            try:
                part.unlink()
            except OSError:
                pass

    events.emit(
        task_id,
        "upload.progress",
        {"received_bytes": session.total_bytes, "total_bytes": session.total_bytes,
         "percent": 1.0, "chunk_index": None, "total_chunks": session.total_chunks},
    )
    events.emit(
        task_id, "task.status",
        {"status": "queued", "current_step": None, "overall": task.overall_progress, "error": None},
    )
    task_queue.enqueue(task_id)
    return task_detail(task)
