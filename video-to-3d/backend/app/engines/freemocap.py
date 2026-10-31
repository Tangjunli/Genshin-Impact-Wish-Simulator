"""FreeMoCap 动作提取适配（本地/云端均可）。

合规提示：FreeMoCap 使用 AGPL-3.0。本项目仅通过独立可执行文件（或独立容器）调用，
以文件与命令行交换数据；不链接其代码、不将其打包进闭源安装包。分发时需同时提供
其源码与许可证声明，建议作为“可选插件”由用户单独安装。

  V3D_FREEMOCAP_CMD=freemocap --path {frames} --output {motion} --task {task_id}
"""
from __future__ import annotations

from .base import EngineContext, EngineResult, EngineUnavailable, Job, collect, expand, run_command

MOTION_SUFFIXES = (".bvh", ".fbx", ".csv", ".npy", ".blend")


class FreemocapEngine:
    name = "freemocap"

    def __init__(self, cmd: str | None):
        self.cmd = cmd

    def available(self) -> bool:
        return bool(self.cmd)

    def extract(self, job: Job, ctx: EngineContext) -> EngineResult:
        if not self.cmd:
            raise EngineUnavailable(
                self.name,
                "未配置 FreeMoCap 命令",
                "安装 FreeMoCap 并设置 V3D_FREEMOCAP_CMD；注意 AGPL-3.0 合规",
            )
        job.motion_dir().mkdir(parents=True, exist_ok=True)
        ctx.log("warning", "FreeMoCap 以 AGPL-3.0 分发，本项目仅通过独立进程调用")
        run_command(expand(self.cmd, job), ctx=ctx, step="person_motion", timeout=None)
        artifacts = collect(job.motion_dir(), MOTION_SUFFIXES)
        if not artifacts:
            raise EngineUnavailable(
                self.name,
                "FreeMoCap 未产出动作文件",
                "检查命令模板的输出目录是否指向 {motion}",
            )
        preferred = [p for p in artifacts if p.suffix == ".bvh"] or artifacts
        return EngineResult(
            artifacts=artifacts,
            meta={"engine": self.name, "license": "AGPL-3.0", "motion": str(preferred[0])},
        )
