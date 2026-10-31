"""引擎装配：根据运行模式/算力选择实现。"""
from __future__ import annotations

from ..config import settings
from . import detect, frames
from .base import EngineUnavailable, EngineContext, EngineResult, Job
from .freemocap import FreemocapEngine
from .gauhuman import GauhumanEngine
from .gsplat import GsplatEngine
from .lingbot import LingbotEngine
from .mock import MockAppearanceEngine, MockMotionEngine, MockSceneEngine
from .ooosplat import OoosplatEngine

__all__ = [
    "EngineUnavailable",
    "EngineContext",
    "EngineResult",
    "Job",
    "detect",
    "frames",
    "scene_engine",
    "motion_engine",
    "appearance_engine",
    "capabilities",
]


def _mock_mode() -> bool:
    return settings.engine_mode == "mock"


def scene_engine(mode: str):
    if _mock_mode():
        return MockSceneEngine()
    if mode == "local":
        return OoosplatEngine(settings.ooosplat_pose_cmd, settings.ooosplat_train_cmd)
    if settings.cloud_scene_engine == "lingbot":
        return LingbotEngine(settings.lingbot_endpoint)
    return GsplatEngine(settings.gsplat_pose_cmd, settings.gsplat_train_cmd)


def motion_engine(mode: str):
    if _mock_mode():
        return MockMotionEngine()
    return FreemocapEngine(settings.freemocap_cmd)


def appearance_engine(mode: str):
    if _mock_mode():
        return MockAppearanceEngine()
    return GauhumanEngine(settings.gauhuman_endpoint, settings.enable_smpl)


def capabilities() -> list[dict]:
    scene = scene_engine("local")
    cloud_scene = scene_engine("cloud")
    motion = motion_engine("local")
    appearance = appearance_engine("cloud")
    simulated = _mock_mode()
    return [
        {
            "name": "ffmpeg",
            "capability": "抽帧 / 缩略图",
            "location": "local",
            "available": bool(__import__("shutil").which("ffmpeg")),
            "license": "LGPL/GPL（按构建选项）",
        },
        {
            "name": "mediapipe",
            "capability": "人物检测",
            "location": "local",
            "available": detect.available(),
            "license": "Apache-2.0",
        },
        {
            "name": scene.name,
            "capability": "场景重建（当前模式）",
            "location": "local" if simulated else mode_location("local"),
            "available": bool(getattr(scene, "available", lambda: True)()),
            "license": getattr(scene, "license_note", "需核实"),
        },
        {
            "name": cloud_scene.name,
            "capability": "场景重建（云端）",
            "location": "cloud",
            "available": bool(getattr(cloud_scene, "available", lambda: True)()),
            "license": getattr(cloud_scene, "license_note", "需核实"),
        },
        {
            "name": motion.name,
            "capability": "人物动作提取",
            "location": "local/cloud",
            "available": bool(motion.available()),
            "license": "AGPL-3.0（FreeMoCap）" if motion.name == "freemocap" else "mock",
        },
        {
            "name": appearance.name,
            "capability": "人物外观重建",
            "location": "cloud",
            "available": bool(appearance.available()),
            "license": "研究许可 + SMPL 体模授权",
        },
        {
            "name": "simulated",
            "capability": "当前产物是否为模拟",
            "location": "n/a",
            "available": simulated,
            "license": "-",
        },
    ]


def mode_location(mode: str) -> str:
    return "local" if mode == "local" else "cloud"
