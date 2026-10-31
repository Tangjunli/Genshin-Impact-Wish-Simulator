"""请求体模型与响应序列化（API 契约与 DESIGN.md 第 5、6 节一致）。"""
from __future__ import annotations

import json
import os
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .models import Artifact, Task


class TaskCreate(BaseModel):
    mode: Literal["local", "cloud"] = "cloud"
    source_name: str = Field(min_length=1, max_length=512)
    source_size: int = Field(default=0, ge=0)
    person_policy: Literal["auto", "motion_only", "appearance", "none"] = "auto"


class LocalTaskCreate(BaseModel):
    path: str = Field(min_length=1)
    person_policy: Literal["auto", "motion_only", "appearance", "none"] = "auto"


class UploadInit(BaseModel):
    total_bytes: int = Field(gt=0)


class UploadComplete(BaseModel):
    sha256: Optional[str] = None


def _json(raw: Optional[str], default):
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


def _error(task: Task):
    return _json(task.error_json, None)


def step_list(task: Task):
    return _json(task.steps_json, [])


def task_summary(task: Task) -> dict:
    return {
        "id": task.id,
        "mode": task.mode,
        "status": task.status,
        "source_name": task.source_name,
        "source_size": task.source_size,
        "person_policy": task.person_policy,
        "person_detected": None if task.person_detected is None else bool(task.person_detected),
        "current_step": task.current_step,
        "overall_progress": task.overall_progress,
        "error": _error(task),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def task_detail(task: Task) -> dict:
    data = task_summary(task)
    data.update(
        {
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "retry_count": task.retry_count,
            "source": {
                "name": task.source_name,
                "size": task.source_size,
                "sha256": task.source_sha256,
                "path": task.source_path if task.mode == "local" else None,
            },
            "steps": step_list(task),
            "outputs": _json(task.outputs_json, {}),
        }
    )
    return data


def artifact_item(artifact: Artifact) -> dict:
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "filename": artifact.filename,
        "size": artifact.size,
        "mime": artifact.mime,
        "sha256": artifact.sha256,
        "download_url": f"/api/v1/tasks/{artifact.task_id}/artifacts/{artifact.id}",
    }


def file_mime(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".ply"):
        return "application/octet-stream"
    if lower.endswith(".glb"):
        return "model/gltf-binary"
    if lower.endswith(".gltf"):
        return "model/gltf+json"
    if lower.endswith(".zip"):
        return "application/zip"
    if lower.endswith(".mp4"):
        return "video/mp4"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".html"):
        return "text/html"
    if lower.endswith(".csv"):
        return "text/csv"
    if lower.endswith(".bvh") or lower.endswith(".fbx"):
        return "application/octet-stream"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".log") or lower.endswith(".txt"):
        return "text/plain"
    return "application/octet-stream"


def guess_kind(path: str) -> str:
    name = os.path.basename(path).lower()
    if name.endswith(".zip"):
        return "package"
    if "/scene/" in path or name.startswith("scene"):
        return "scene"
    if "/person/" in path or name.startswith("person"):
        return "person"
    if "/motion/" in path or name.startswith("motion"):
        return "motion"
    if "/preview/" in path:
        return "preview"
    return "misc"
