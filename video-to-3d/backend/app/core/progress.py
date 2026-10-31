"""步骤状态机：整体进度的唯一计算来源。"""
from __future__ import annotations

import json
from typing import Any, Dict, List

STEP_SPECS: List[Dict[str, Any]] = [
    {"key": "prepare", "title": "任务校验与媒体解析", "weight": 4},
    {"key": "extract_frames", "title": "视频抽帧", "weight": 8},
    {"key": "detect_person", "title": "人物检测", "weight": 5},
    {"key": "camera_pose", "title": "相机位姿估计", "weight": 18},
    {"key": "scene_reconstruct", "title": "场景重建", "weight": 30},
    {"key": "person_motion", "title": "人物动作提取", "weight": 12},
    {"key": "person_appearance", "title": "人物外观重建", "weight": 15},
    {"key": "texture_optimize", "title": "纹理优化", "weight": 5},
    {"key": "export", "title": "模型导出与格式转换", "weight": 5},
    {"key": "package", "title": "打包下载文件", "weight": 3},
]

ACTIVE_STATUSES = {"pending", "running", "completed", "failed"}


def _blank_step(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": spec["key"],
        "title": spec["title"],
        "weight": spec["weight"],
        "status": "pending",
        "progress": 0.0,
        "detail": {"current": None, "total": None, "unit": None, "message": ""},
        "error": None,
        "started_at": None,
        "finished_at": None,
    }


def default_steps() -> List[Dict[str, Any]]:
    return [_blank_step(spec) for spec in STEP_SPECS]


class StepTracker:
    def __init__(self, steps: List[Dict[str, Any]]):
        self.steps = steps

    @classmethod
    def from_json(cls, raw: str | None) -> "StepTracker":
        try:
            data = json.loads(raw or "[]")
        except ValueError:
            data = []
        if not data:
            return cls(default_steps())
        # 兼容旧结构：缺失字段补齐
        known = {s["key"]: s for s in default_steps()}
        merged = []
        for item in data:
            base = dict(known.get(item.get("key"), {}))
            base.update({k: v for k, v in item.items() if v is not None or k == "person_detected"})
            base.setdefault("detail", {"current": None, "total": None, "unit": None, "message": ""})
            merged.append(base)
        return cls(merged)

    def get(self, key: str) -> Dict[str, Any]:
        for step in self.steps:
            if step["key"] == key:
                return step
        raise KeyError(key)

    def update(
        self,
        key: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        detail: Dict[str, Any] | None = None,
        error: str | None = None,
        timestamp: str | None = None,
    ) -> Dict[str, Any]:
        step = self.get(key)
        if status and status != step["status"]:
            step["status"] = status
            if status == "running" and not step["started_at"]:
                step["started_at"] = timestamp
            if status in ("completed", "skipped", "failed", "canceled"):
                step["finished_at"] = timestamp
                if status == "completed":
                    step["progress"] = 1.0
        if progress is not None:
            # 单步内严格单调，避免回退闪烁
            step["progress"] = max(step["progress"], min(1.0, max(0.0, float(progress)))
                                   ) if status != "pending" else 0.0
        if detail:
            merged = dict(step.get("detail") or {})
            merged.update(detail)
            step["detail"] = merged
        if error is not None:
            step["error"] = error
        return step

    def skip(self, key: str, reason: str, timestamp: str | None = None) -> Dict[str, Any]:
        step = self.get(key)
        step["status"] = "skipped"
        step["progress"] = 0.0
        step["finished_at"] = timestamp
        step["detail"] = {
            "current": None,
            "total": None,
            "unit": None,
            "message": reason,
            "skipped_reason": reason,
        }
        return step

    def overall(self) -> float:
        total_weight = 0.0
        weighted = 0.0
        for step in self.steps:
            if step["status"] == "skipped":
                continue
            weight = float(step.get("weight") or 0)
            total_weight += weight
            weighted += weight * float(step.get("progress") or 0)
        if total_weight <= 0:
            return 0.0
        return round(min(1.0, max(0.0, weighted / total_weight)), 4)

    def current(self) -> str | None:
        for step in self.steps:
            if step["status"] == "running":
                return step["key"]
        return None

    def to_json(self) -> str:
        return json.dumps(self.steps, ensure_ascii=False)
