"""FastAPI 应用装配：CORS、可选 API Token、启动恢复、路由注册。"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import artifacts, events as events_api, system, tasks, upload
from .config import settings
from .core.events import utcnow
from .db import SessionLocal, init_db
from .models import Task
from .runtime import bus, events, task_queue

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("v3d")


def _recover_tasks() -> None:
    """重启后：运行中的任务标记为 interrupted（等待用户继续），排队中的任务自动重新入队。"""
    with SessionLocal() as db:
        stuck = db.query(Task).filter(Task.status.in_(("running", "packaging"))).all()
        for task in stuck:
            task.status = "interrupted"
            task.updated_at = utcnow()
        db.commit()
        for task in stuck:
            events.emit(
                task.id,
                "task.status",
                {
                    "status": "interrupted",
                    "current_step": task.current_step,
                    "overall": task.overall_progress,
                    "error": None,
                },
            )
            logger.info("任务中断，等待继续: %s", task.id)
        queued = [task.id for task in db.query(Task).filter(Task.status == "queued").all()]

    if settings.auto_resume_queued:
        for task_id in queued:
            task_queue.enqueue(task_id)
        if queued:
            logger.info("自动恢复 %d 个排队任务", len(queued))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bus.bind_loop(asyncio.get_running_loop())
    _recover_tasks()
    logger.info("VideoTo3D API 就绪 · mode=%s · data=%s", settings.engine_mode, settings.data_dir)
    yield
    task_queue.shutdown()


app = FastAPI(
    title="VideoTo3D API",
    version="0.1.0",
    description="视频 → 3D 场景 / 人物模型 / 动作数据（本地 sidecar 与云端同一套契约）",
    lifespan=lifespan,
)

_origins = [item.strip() for item in os.getenv("V3D_CORS_ORIGINS", "*").split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"],
)


@app.middleware("http")
async def api_token_guard(request: Request, call_next):
    """本地 sidecar 也建议配置 token，防止本机浏览器跨站访问。"""
    if settings.api_token and request.method != "OPTIONS" and request.url.path.startswith("/api/"):
        if request.headers.get("x-api-token") != settings.api_token:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


app.include_router(tasks.router, prefix="/api/v1")
app.include_router(upload.router, prefix="/api/v1")
app.include_router(events_api.router, prefix="/api/v1")
app.include_router(artifacts.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok", "engine_mode": settings.engine_mode, "version": "0.1.0"}


@app.get("/")
def root():
    return {"service": "video-to-3d", "docs": "/docs", "api": "/api/v1"}
