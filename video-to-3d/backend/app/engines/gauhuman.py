"""GauHuman 人物外观重建适配（仅云端 GPU）。

合规提示：
  1) GauHuman 仓库自身许可需在采用前核实（学术项目通常限制商用）；
  2) 其依赖 SMPL/SMPL-X 体模，授权来自 Max Planck，多为非商用研究用途。
因此默认关闭，必须显式设置 V3D_ENABLE_SMPL=true，并在 UI 上提示用户自行取得授权。
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import EngineContext, EngineResult, EngineUnavailable, Job, collect
from .http_job import HttpJobClient, HttpJobError


class GauhumanEngine:
    name = "gauhuman"

    def __init__(self, endpoint: str | None, enabled: bool):
        self.client = HttpJobClient(endpoint, timeout=60) if endpoint else None
        self.enabled = enabled

    def available(self) -> bool:
        return bool(self.client and self.enabled)

    def _require(self) -> HttpJobClient:
        if not self.enabled:
            raise EngineUnavailable(
                self.name,
                "SMPL/SMPL-X 体模授权未开启，人物外观重建已禁用",
                "取得体模商用授权后设置 V3D_ENABLE_SMPL=true；否则仅提取动作数据",
            )
        if not self.client:
            raise EngineUnavailable(
                self.name,
                "未配置 GauHuman 推理服务地址",
                "设置 V3D_GAUHUMAN_ENDPOINT，或只保留动作提取",
            )
        return self.client

    def reconstruct(self, job: Job, ctx: EngineContext, motion_file: str | None) -> EngineResult:
        client = self._require()
        try:
            job_id = client.submit(
                {
                    "task_id": job.task_id,
                    "video_path": str(job.video),
                    "motion_path": motion_file,
                    "frames_dir": str(job.frames_dir),
                    "out_dir": str(job.person_dir()),
                }
            )
        except HttpJobError as exc:
            raise EngineUnavailable(self.name, str(exc), "检查云端 GPU 服务后重试") from exc

        state = client.wait(
            job_id,
            on_update=lambda s: ctx.progress(
                "person_appearance",
                float(s.get("progress") or 0),
                1.0,
                f"人物外观重建：{s.get('stage') or 'training'}（{float(s.get('progress') or 0):.0%}）",
            ),
            check_cancel=ctx.check_cancel,
            interval=2.0,
            max_seconds=7200,
        )
        if state.get("status") != "completed":
            raise EngineUnavailable(self.name, state.get("error") or "人物外观重建失败",
                                    "可重试，或改为仅提取动作数据")

        job.person_dir().mkdir(parents=True, exist_ok=True)
        for artifact in state.get("artifacts") or []:
            url = artifact.get("url")
            filename = Path(artifact.get("path") or artifact.get("filename") or "person.glb").name
            if url:
                client.download(url, job.person_dir() / filename)
        artifacts = collect(job.person_dir(), (".glb", ".fbx", ".obj"))
        (job.work_dir / "gauhuman.json").write_text(
            json.dumps({"job_id": job_id}, ensure_ascii=False), encoding="utf-8"
        )
        return EngineResult(
            artifacts=artifacts,
            meta={"engine": self.name, "job_id": job_id, "license": "research/SMPL"},
        )
