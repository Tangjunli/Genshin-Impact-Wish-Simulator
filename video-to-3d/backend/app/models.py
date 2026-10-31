"""ORM 模型：任务、事件（兼 SSE 回放）、上传会话、产物。"""
from __future__ import annotations

from sqlalchemy import Column, Float, Index, Integer, String, Text

from .db import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(32), primary_key=True)
    mode = Column(String(16), nullable=False, default="cloud")  # local | cloud
    status = Column(String(16), nullable=False, default="created", index=True)

    source_name = Column(String(512), nullable=False)
    source_size = Column(Integer, nullable=False, default=0)
    source_sha256 = Column(String(64))
    source_path = Column(Text)  # 本地模式的原始路径
    video_path = Column(Text)  # 实际处理用的文件（本地=原路径；云端=合并后的文件）

    person_policy = Column(String(16), nullable=False, default="auto")
    person_detected = Column(Integer)  # NULL 未检测
    current_step = Column(String(64))
    overall_progress = Column(Float, nullable=False, default=0.0)
    steps_json = Column(Text, nullable=False, default="[]")
    outputs_json = Column(Text, nullable=False, default="{}")
    error_json = Column(Text)

    engine_profile = Column(String(32), nullable=False, default="auto")
    retry_count = Column(Integer, nullable=False, default=0)

    created_at = Column(String(32), nullable=False)
    updated_at = Column(String(32), nullable=False)
    started_at = Column(String(32))
    finished_at = Column(String(32))


class TaskEvent(Base):
    """事件日志：既用于日志面板，也用于 SSE Last-Event-ID 回放。"""

    __tablename__ = "task_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(32), nullable=False)
    type = Column(String(32), nullable=False)
    payload = Column(Text, nullable=False)
    created_at = Column(String(32), nullable=False)

    __table_args__ = (Index("idx_task_events_task", "task_id", "id"),)


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    task_id = Column(String(32), primary_key=True)
    chunk_size = Column(Integer, nullable=False)
    total_bytes = Column(Integer, nullable=False)
    total_chunks = Column(Integer, nullable=False)
    uploaded_chunks = Column(Text, nullable=False, default="[]")
    received_bytes = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="open")
    updated_at = Column(String(32), nullable=False)


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(32), nullable=False, index=True)
    kind = Column(String(16), nullable=False)  # scene|person|motion|preview|package|log
    filename = Column(String(256), nullable=False)
    path = Column(Text, nullable=False)
    mime = Column(String(128))
    size = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64))
    created_at = Column(String(32), nullable=False)
