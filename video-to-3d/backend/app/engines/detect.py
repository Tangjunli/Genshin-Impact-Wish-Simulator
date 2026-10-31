"""人物存在性检测（MediaPipe Pose，Apache-2.0）。

未安装 mediapipe 时返回 available=False，由调度逻辑决定降级方式；绝不虚构检测结果。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from ..config import settings
from .base import EngineContext, Job

SAMPLE_MIN = 24


def available() -> bool:
    return importlib.util.find_spec("mediapipe") is not None


def detect(job: Job, ctx: EngineContext) -> dict:
    frames = sorted(Path(job.frames_dir).glob("*.jpg"))
    if not frames:
        return {"available": available(), "sampled": 0, "person_frames": 0, "person_ratio": 0.0}

    if settings.engine_mode == "mock":
        ctx.log("info", "[模拟] 人物检测：命中 60%（仅联调模式）")
        return {
            "available": True,
            "sampled": max(SAMPLE_MIN, len(frames) // 12),
            "person_frames": max(1, (max(SAMPLE_MIN, len(frames) // 12) * 6) // 10),
            "person_ratio": 0.6,
            "simulated": True,
        }

    if not available():
        ctx.log("warning", "未安装 mediapipe，跳过人物检测（person_detected=null）")
        return {"available": False, "sampled": 0, "person_frames": 0, "person_ratio": 0.0}

    import mediapipe as mp  # type: ignore
    import numpy as np  # type: ignore
    from PIL import Image  # type: ignore

    sample_target = max(SAMPLE_MIN, min(len(frames), len(frames) // 12))
    stride = max(1, len(frames) // sample_target)
    sampled = frames[::stride][: max(SAMPLE_MIN, sample_target)]

    hits = 0
    ctx.log("info", f"人物检测：抽样 {len(sampled)}/{len(frames)} 帧")
    with mp.solutions.pose.Pose(
        static_image_mode=True, min_detection_confidence=0.5, model_complexity=1
    ) as pose:
        for index, path in enumerate(sampled, start=1):
            ctx.check_cancel()
            try:
                rgb = np.asarray(Image.open(path).convert("RGB"))
                result = pose.process(rgb)
                if result.pose_landmarks is not None:
                    hits += 1
            except Exception as exc:  # 单帧失败不应中断检测
                ctx.log("warning", f"检测帧 {path.name} 失败: {exc}")
            ctx.progress("detect_person", index, len(sampled),
                         f"正在检测人物：{index}/{len(sampled)}")
            if index and index % 12 == 0:
                ctx.log("info", f"已检测 {index} 帧，命中 {hits} 帧")

    ratio = round(hits / max(1, len(sampled)), 3)
    ctx.log("info", f"人物检测完成：{hits}/{len(sampled)} 帧含人体（{ratio:.0%}）")
    return {
        "available": True,
        "sampled": len(sampled),
        "person_frames": hits,
        "person_ratio": ratio,
    }
