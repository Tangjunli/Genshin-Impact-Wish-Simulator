"""LingBot-Map 流式重建适配（云端 GPU）。

契约见 http_job.py。为支持“位姿先就绪、场景随后完成”的流式特性，服务端在作业状态里
提供 poses_ready 标志；本适配器在 camera_pose 阶段等到该标志，在 scene_reconstruct
阶段继续等到 completed 并下载产物。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .base import EngineContext, EngineResult, EngineUnavailable, Job, collect
from .http_job import HttpJobClient, HttpJobError


class LingbotEngine:
    name = "lingbot-map"

    def __init__(self, endpoint: str | None):
        self.client = HttpJobClient(endpoint) if endpoint else None
        self._state_file = "lingbot_job.json"

    def available(self) -> bool:
        return self.client is not None

    def _require(self) -> HttpJobClient:
        if not self.client:
            raise EngineUnavailable(
                self.name,
                "未配置 LingBot-Map 服务地址",
                "设置 V3D_LINGBOT_ENDPOINT，或切换到本地 OOOSplat / gsplat 处理",
            )
        return self.client

    def _job_file(self, job: Job) -> Path:
        return job.work_dir / self._state_file

    def estimate_poses(self, job: Job, ctx: EngineContext) -> EngineResult:
        client = self._require()
        try:
            job_id = client.submit(
                {
                    "task_id": job.task_id,
                    "video_path": str(job.video),
                    "frames_dir": str(job.frames_dir),
                    "out_dir": str(job.out_dir),
                    "mode": "stream",
                }
            )
        except HttpJobError as exc:
            raise EngineUnavailable(self.name, str(exc), "检查云端推理服务状态后重试") from exc
        job.work_dir.mkdir(parents=True, exist_ok=True)
        self._job_file(job).write_text(json.dumps({"job_id": job_id}), encoding="utf-8")

        while True:
            ctx.check_cancel()
            try:
                state = client.status(job_id)
            except HttpJobError as exc:
                raise EngineUnavailable(self.name, str(exc), "推理服务中断，可重试任务") from exc
            if state.get("status") == "failed":
                raise EngineUnavailable(self.name, state.get("error") or "流式重建失败",
                                        "可重试任务，或改用 gsplat 批处理")
            ratio = float(state.get("progress") or 0)
            stage = state.get("stage") or "running"
            ctx.progress("camera_pose", ratio, 1.0, f"流式重建：{stage}（{ratio:.0%}）")
            if state.get("poses_ready") or state.get("status") == "completed":
                ctx.log("info", "相机位姿与稀疏几何已就绪")
                return EngineResult(meta={"engine": self.name, "job_id": job_id, "stream": True})
            time.sleep(2.0)

    def train(self, job: Job, ctx: EngineContext) -> EngineResult:
        client = self._require()
        info = {}
        if self._job_file(job).exists():
            info = json.loads(self._job_file(job).read_text(encoding="utf-8"))
        job_id = info.get("job_id")
        if not job_id:
            raise EngineUnavailable(self.name, "找不到流式重建作业，请先运行位姿阶段",
                                    "重试任务")
        state = client.wait(
            job_id,
            on_update=lambda s: ctx.progress(
                "scene_reconstruct",
                float(s.get("progress") or 0),
                1.0,
                f"场景重建：{s.get('stage') or 'running'}",
            ),
            check_cancel=ctx.check_cancel,
            interval=2.0,
        )
        if state.get("status") != "completed":
            raise EngineUnavailable(self.name, state.get("error") or "场景重建失败", "可重试任务")
        job.scene_dir().mkdir(parents=True, exist_ok=True)
        for artifact in state.get("artifacts") or []:
            url = artifact.get("url")
            filename = Path(artifact.get("path") or artifact.get("filename") or "artifact.bin").name
            if url:
                client.download(url, job.scene_dir() / filename)
        artifacts = collect(job.scene_dir(), (".ply", ".splat", ".glb"))
        return EngineResult(artifacts=artifacts, meta={"engine": self.name, "job_id": job_id})
