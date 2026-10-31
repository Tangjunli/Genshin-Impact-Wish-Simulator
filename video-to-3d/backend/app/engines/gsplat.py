"""云端 gsplat 训练适配（CUDA GPU）。

  V3D_GSPLAT_POSE_CMD  -> 通常为 COLMAP 命令行
  V3D_GSPLAT_TRAIN_CMD -> gsplat 训练脚本（输出 .ply / .splat / .glb）
gsplat 为 Apache-2.0；原始 3DGS 参考实现为研究/非商用许可，注意区分。
"""
from .command_scene import CommandSceneEngine


class GsplatEngine(CommandSceneEngine):
    def __init__(self, pose_cmd: str | None, train_cmd: str | None) -> None:
        super().__init__(
            "gsplat",
            pose_cmd,
            train_cmd,
            suffix=(".ply", ".splat", ".glb"),
            license_note="gsplat 为 Apache-2.0；请勿混用非商用许可的 3DGS 实现",
        )
