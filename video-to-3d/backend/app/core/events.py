"""进程内事件总线 + 事件持久化。

云端多副本部署时，把 EventBus 换成 Redis Pub/Sub 即可，EventService 接口保持不变。
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List

from ..models import TaskEvent


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: Dict[str, List[asyncio.Queue]] = {}
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        with self._lock:
            self._subs.setdefault(task_id, []).append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            queues = self._subs.get(task_id)
            if not queues:
                return
            if queue in queues:
                queues.remove(queue)
            if not queues:
                self._subs.pop(task_id, None)

    def publish(self, task_id: str, event: Dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._subs.get(task_id) or ())
        for queue in queues:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._put, queue, event)
            else:  # 测试/同步环境兜底
                self._put(queue, event)

    @staticmethod
    def _put(queue: asyncio.Queue, event: Dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # 客户端过慢：丢弃最旧事件，保证最新状态可达
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except asyncio.QueueEmpty:
                pass


class EventService:
    def __init__(self, session_factory, bus: EventBus | None = None) -> None:
        self._session_factory = session_factory
        self.bus = bus or EventBus()

    def emit(self, task_id: str, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        ts = utcnow()
        with self._session_factory() as session:
            row = TaskEvent(
                task_id=task_id,
                type=event_type,
                payload=json.dumps(data, ensure_ascii=False, default=str),
                created_at=ts,
            )
            session.add(row)
            session.commit()
            event_id = row.id
        event = {
            "id": event_id,
            "type": event_type,
            "task_id": task_id,
            "ts": ts,
            "data": data,
        }
        self.bus.publish(task_id, event)
        return event

    def history(self, task_id: str, after_id: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        with self._session_factory() as session:
            rows = (
                session.query(TaskEvent)
                .filter(TaskEvent.task_id == task_id, TaskEvent.id > after_id)
                .order_by(TaskEvent.id.asc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": row.id,
                    "type": row.type,
                    "task_id": row.task_id,
                    "ts": row.created_at,
                    "data": json.loads(row.payload),
                }
                for row in rows
            ]

    def recent_logs(self, task_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._session_factory() as session:
            rows = (
                session.query(TaskEvent)
                .filter(TaskEvent.task_id == task_id, TaskEvent.type == "log")
                .order_by(TaskEvent.id.desc())
                .limit(limit)
                .all()
            )
            rows.reverse()
            return [
                {
                    "id": row.id,
                    "ts": row.created_at,
                    **json.loads(row.payload),
                }
                for row in rows
            ]
