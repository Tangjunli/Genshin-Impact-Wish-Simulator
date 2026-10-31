"""本地 OOOSplat 工作流适配（桌面端运行）。

注意：OOOSplat 目前是桌面工具，不是移动端 SDK。这里只以子进程调用其脚本：
  V3D_OOOSPLAT_POSE_CMD  -> 抽帧已完成后的 COLMAP 位姿阶段
  V3D_OOOSPLAT_TRAIN_CMD -> 3DGS 训练与导出阶段
模板可用占位符：{frames} {work} {scene} {video} {task_id}
"""
from .command_scene import CommandSceneEngine


class OoosplatEngine(CommandSceneEngine):
    def __init__(self, pose_cmd: str | None, train_cmd: str | None) -> None:
        super().__init__(
            "ooosplat",
            pose_cmd,
            train_cmd,
            suffix=(".ply", ".splat", ".glb"),
            license_note="使用前请核实 OOOSplat 仓库许可证；COLMAP 为 BSD",
        )

    @property
    def local_only(self) -> bool:
        return True
