"""通用命令行场景引擎：把开源工作流的脚本以子进程方式接入，按真实输出解析进度。

OOOSplat / gsplat 都通过它适配：应用本身不重写 COLMAP 与 3DGS 训练。
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

from .base import EngineContext, EngineResult, EngineUnavailable, Job, collect, expand, run_command


class CommandSceneEngine:
    def __init__(
        self,
        name: str,
        pose_cmd: str | None,
        train_cmd: str | None,
        *,
        suffix: Tuple[str, ...] = (".ply", ".splat", ".glb"),
        license_note: str = "",
    ) -> None:
        self.name = name
        self.pose_cmd = pose_cmd
        self.train_cmd = train_cmd
        self.suffix = suffix
        self.license_note = license_note

    def available(self) -> bool:
        return bool(self.pose_cmd and self.train_cmd)

    def _missing(self) -> EngineUnavailable:
        return EngineUnavailable(
            self.name,
            f"{self.name} 未配置：缺少位姿或训练命令",
            "在设置中填写引擎命令（见 docs/DESIGN.md 第 0.1 节），或切换到云端处理",
        )

    def estimate_poses(self, job: Job, ctx: EngineContext) -> EngineResult:
        if not self.available():
            raise self._missing()
        cmd = expand(self.pose_cmd or "", job)
        ctx.log("info", f"{self.name} 相机位姿估计（{self.license_note or '见仓库许可'}）")
        run_command(cmd, ctx=ctx, step="camera_pose", timeout=None)
        if not (job.work_dir / "colmap").exists():
            (job.work_dir / "colmap").mkdir(parents=True, exist_ok=True)
        return EngineResult(meta={"engine": self.name, "stage": "pose"})

    def train(self, job: Job, ctx: EngineContext) -> EngineResult:
        if not self.available():
            raise self._missing()
        job.scene_dir().mkdir(parents=True, exist_ok=True)
        cmd = expand(self.train_cmd or "", job)
        run_command(cmd, ctx=ctx, step="scene_reconstruct", timeout=None)
        artifacts = collect(job.scene_dir(), self.suffix)
        if not artifacts:
            raise EngineUnavailable(
                self.name,
                f"{self.name} 训练完成但未找到模型文件（{', '.join(self.suffix)}）",
                "检查引擎输出目录参数，或查看 logs/pipeline.log",
            )
        return EngineResult(artifacts=artifacts, meta={"engine": self.name})
