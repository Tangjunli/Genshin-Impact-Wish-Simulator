"""运行期配置。所有配置项均可用环境变量覆盖，便于本地 sidecar 与云端复用同一套代码。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_url: str
    chunk_size: int
    max_workers: int
    heartbeat_seconds: int
    engine_mode: str  # auto | real | mock
    auto_resume_queued: bool
    api_token: str | None
    sample_fps: float
    max_frames: int

    # 本地引擎
    ooosplat_pose_cmd: str | None
    ooosplat_train_cmd: str | None
    freemocap_cmd: str | None
    # 云端引擎
    cloud_scene_engine: str  # gsplat | lingbot
    gsplat_pose_cmd: str | None
    gsplat_train_cmd: str | None
    lingbot_endpoint: str | None
    gauhuman_endpoint: str | None
    enable_smpl: bool

    def task_root(self) -> Path:
        return self.data_dir / "tasks"


def load_settings() -> Settings:
    data_dir = Path(os.getenv("V3D_DATA_DIR", PROJECT_ROOT / "runtime")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=data_dir,
        db_url=os.getenv("V3D_DB_URL", f"sqlite:///{data_dir / 'app.db'}"),
        chunk_size=_int("V3D_CHUNK_SIZE", 4 * 1024 * 1024),
        max_workers=_int("V3D_WORKERS", 2),
        heartbeat_seconds=_int("V3D_SSE_HEARTBEAT", 15),
        engine_mode=os.getenv("V3D_ENGINE_MODE", "auto").lower(),
        auto_resume_queued=_bool("V3D_AUTO_RESUME", True),
        api_token=os.getenv("V3D_API_TOKEN") or None,
        sample_fps=float(os.getenv("V3D_SAMPLE_FPS", "2")),
        max_frames=_int("V3D_MAX_FRAMES", 360),
        ooosplat_pose_cmd=os.getenv("V3D_OOOSPLAT_POSE_CMD") or None,
        ooosplat_train_cmd=os.getenv("V3D_OOOSPLAT_TRAIN_CMD") or None,
        freemocap_cmd=os.getenv("V3D_FREEMOCAP_CMD") or None,
        cloud_scene_engine=os.getenv("V3D_CLOUD_SCENE", "gsplat").lower(),
        gsplat_pose_cmd=os.getenv("V3D_GSPLAT_POSE_CMD") or None,
        gsplat_train_cmd=os.getenv("V3D_GSPLAT_TRAIN_CMD") or None,
        lingbot_endpoint=os.getenv("V3D_LINGBOT_ENDPOINT") or None,
        gauhuman_endpoint=os.getenv("V3D_GAUHUMAN_ENDPOINT") or None,
        enable_smpl=_bool("V3D_ENABLE_SMPL", False),
    )


settings = load_settings()
