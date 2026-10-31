"""产物列表、单文件下载、ZIP 断点续传下载。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..core.storage import task_dir
from ..db import get_db
from ..models import Artifact, Task
from ..schemas import artifact_item

router = APIRouter()

CHUNK = 1024 * 1024


def _iter_range(path: Path, start: int, length: int):
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            data = fh.read(min(CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


@router.get("/tasks/{task_id}/artifacts")
def list_artifacts(task_id: str, db: Session = Depends(get_db)):
    if not db.get(Task, task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    rows = (
        db.query(Artifact)
        .filter(Artifact.task_id == task_id)
        .order_by(Artifact.id.asc())
        .all()
    )
    return {"items": [artifact_item(row) for row in rows]}


@router.get("/tasks/{task_id}/artifacts/{artifact_id}")
def download_artifact(task_id: str, artifact_id: int, db: Session = Depends(get_db)):
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.task_id != task_id:
        raise HTTPException(status_code=404, detail="产物不存在")
    path = Path(artifact.path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="产物文件已被清理")
    return FileResponse(
        str(path),
        filename=artifact.filename,
        media_type=artifact.mime or "application/octet-stream",
    )


@router.get("/tasks/{task_id}/download.zip")
def download_zip(task_id: str, request: Request, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status != "completed":
        raise HTTPException(status_code=409, detail=f"任务尚未完成（{task.status}）")

    path = task_dir(task_id) / "package" / f"{task_id}.zip"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="ZIP 包不存在")

    size = path.stat().st_size
    filename = f"{task_id}.zip"
    range_header = request.headers.get("range")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "application/zip",
    }

    if not range_header:
        return StreamingResponse(
            _iter_range(path, 0, size),
            media_type="application/zip",
            headers={**headers, "Content-Length": str(size)},
        )

    try:
        unit, _, spec = range_header.partition("=")
        if unit.strip().lower() != "bytes":
            raise ValueError("unsupported unit")
        start_raw, _, end_raw = spec.partition("-")
        start = int(start_raw) if start_raw else 0
        end = int(end_raw) if end_raw else size - 1
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="无效的 Range 头") from exc

    if start >= size or start > end:
        return StreamingResponse(
            iter(()),
            status_code=416,
            headers={"Content-Range": f"bytes */{size}"},
        )
    end = min(end, size - 1)
    length = end - start + 1
    return StreamingResponse(
        _iter_range(path, start, length),
        status_code=206,
        media_type="application/zip",
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )
