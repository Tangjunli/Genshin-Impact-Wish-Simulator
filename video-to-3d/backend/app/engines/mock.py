"""开发/联调用的模拟引擎。

重要：仅在 V3D_ENGINE_MODE=mock 时启用，产物会在 manifest 中标记 simulated=true。
它不是“假进度”：进度由模拟计算循环的真实耗时驱动；但产物是占位几何，不能用于生产交付。
"""
from __future__ import annotations

import csv
import json
import struct
import time
from pathlib import Path

from .base import EngineContext, EngineResult, Job

STEPS = 24


def _simulate(ctx: EngineContext, step: str, total: int, seconds: float, label: str) -> None:
    for index in range(1, STEPS + 1):
        ctx.check_cancel()
        time.sleep(seconds / STEPS)
        current = int(total * index / STEPS)
        ctx.progress(step, current, total, f"{label} {current}/{total}")


def write_ply(path: Path, points: int = 4096) -> Path:
    """生成一个可被 3DGS 查看器打开的占位点云（真实 PLY 格式，内容为模拟）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "ply",
        "format ascii 1.0",
        "comment simulated placeholder by video-to-3d mock engine",
        f"element vertex {points}",
        "property float x",
        "property float y",
        "property float z",
        "property uchar red",
        "property uchar green",
        "property uchar blue",
        "end_header",
    ]
    for i in range(points):
        x = (i % 32) / 32 - 0.5
        y = ((i // 32) % 32) / 32 - 0.5
        z = (i // 1024) / 4 - 0.5
        lines.append(f"{x:.4f} {y:.4f} {z:.4f} 90 120 220")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_glb(path: Path) -> Path:
    """写入一个合法的最小 GLB（单个三角形），用于验证预览与打包链路。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    vertices = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (0.0, 1.0, 0.0)]
    binary = b"".join(struct.pack("<3f", *vertex) for vertex in vertices)
    gltf = {
        "asset": {"version": "2.0", "generator": "video-to-3d mock"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "placeholder"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 4}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(binary), "target": 34962}
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "max": [1.0, 1.0, 0.0],
                "min": [-1.0, -1.0, 0.0],
            }
        ],
    }
    payload = json.dumps(gltf, separators=(",", ":")).encode()
    payload += b" " * ((4 - len(payload) % 4) % 4)
    total = 12 + 8 + len(payload) + 8 + len(binary)
    with open(path, "wb") as fh:
        fh.write(b"glTF" + struct.pack("<II", 2, total))
        fh.write(struct.pack("<I", len(payload)) + b"JSON" + payload)
        fh.write(struct.pack("<I", len(binary)) + b"BIN\x00" + binary)
    return path


class MockSceneEngine:
    name = "mock"
    simulated = True

    def available(self) -> bool:
        return True

    def estimate_poses(self, job: Job, ctx: EngineContext) -> EngineResult:
        ctx.log("info", "[模拟] 相机位姿估计：输出到 work/colmap/poses.json")
        _simulate(ctx, "camera_pose", 120, 1.0, "COLMAP 注册图像")
        (job.work_dir / "colmap").mkdir(parents=True, exist_ok=True)
        (job.work_dir / "colmap" / "poses.json").write_text(
            json.dumps(
                {
                    "simulated": True,
                    "cameras": [{"frame": i, "q": [1, 0, 0, 0], "t": [i * 0.02, 0, 0]}
                                for i in range(24)],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return EngineResult(meta={"engine": self.name, "simulated": True})

    def train(self, job: Job, ctx: EngineContext) -> EngineResult:
        ctx.log("info", "[模拟] 3DGS 训练：输出 scene.ply / scene.glb")
        _simulate(ctx, "scene_reconstruct", 7000, 1.5, "3DGS 训练迭代")
        ply = write_ply(job.scene_dir() / "scene.ply")
        glb = write_glb(job.scene_dir() / "scene.glb")
        return EngineResult(
            artifacts=[ply, glb], meta={"engine": self.name, "simulated": True}, simulated=True
        )


class MockMotionEngine:
    name = "mock-motion"
    simulated = True

    def available(self) -> bool:
        return True

    def extract(self, job: Job, ctx: EngineContext) -> EngineResult:
        ctx.log("info", "[模拟] 动作提取：输出 motion.csv")
        _simulate(ctx, "person_motion", 240, 0.6, "姿态帧")
        target = job.motion_dir() / "motion.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["frame", "time", "joint", "x", "y", "z", "confidence"])
            for frame in range(24):
                for joint in range(3):
                    writer.writerow(
                        [frame, round(frame / 24, 3), joint,
                         0.1 * joint, 1.6, 0.02 * frame, 0.9]
                    )
        return EngineResult(
            artifacts=[target], meta={"engine": self.name, "simulated": True}, simulated=True
        )


class MockAppearanceEngine:
    name = "mock-appearance"
    simulated = True

    def available(self) -> bool:
        return True

    def reconstruct(self, job: Job, ctx: EngineContext, motion_file: str | None) -> EngineResult:
        ctx.log("info", "[模拟] 人物外观重建：输出 person.glb")
        _simulate(ctx, "person_appearance", 300, 0.8, "人物训练轮次")
        glb = write_glb(job.person_dir() / "person.glb")
        return EngineResult(
            artifacts=[glb], meta={"engine": self.name, "simulated": True}, simulated=True
        )
