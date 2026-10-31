"""SSE 进度流与日志回放。"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..core.events import utcnow
from ..db import get_db
from ..models import Task, TaskEvent
from ..runtime import events
from ..schemas import task_detail

router = APIRouter()


def _chunk(event_id: Optional[int], event_type: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event_type}\ndata: {payload}\n\n"


@router.get("/tasks/{task_id}/events")
async def stream_events(
    task_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    last_event_id_query: Optional[str] = Query(default=None, alias="last_event_id"),
    db: Session = Depends(get_db),
):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    resume_raw = last_event_id_query or last_event_id
    resume_from: Optional[int] = None
    if resume_raw is not None and str(resume_raw).isdigit():
        resume_from = int(resume_raw)

    queue = events.bus.subscribe(task_id)

    if resume_from is None:
        max_id = (
            db.query(func.max(TaskEvent.id)).filter(TaskEvent.task_id == task_id).scalar() or 0
        )
        prefix = [
            _chunk(
                int(max_id) or None,
                "snapshot",
                {
                    "task": task_detail(task),
                    "logs": events.recent_logs(task_id, 50),
                    "ts": utcnow(),
                },
            )
        ]
        last_sent = int(max_id)
    else:
        prefix = [
            _chunk(event["id"], event["type"], event)
            for event in events.history(task_id, after_id=resume_from, limit=500)
        ]
        last_sent = resume_from

    async def generator():
        nonlocal last_sent
        try:
            for item in prefix:
                yield item
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=settings.heartbeat_seconds
                    )
                except asyncio.TimeoutError:
                    yield f": heartbeat {utcnow()}\n\n"
                    continue
                if event["id"] <= last_sent:
                    continue
                last_sent = event["id"]
                yield _chunk(event["id"], event["type"], event)
        finally:
            events.bus.unsubscribe(task_id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/tasks/{task_id}/logs")
def task_logs(
    task_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    if not db.get(Task, task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    entries = events.history(task_id, after_id=after, limit=limit)
    return {
        "items": [entry for entry in entries if entry["type"] == "log"],
        "next_after": entries[-1]["id"] if entries else after,
    }
