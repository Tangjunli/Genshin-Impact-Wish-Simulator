"""进程级单例：事件总线、事件服务、任务队列。"""
from __future__ import annotations

from .config import settings
from .core.events import EventBus, EventService
from .db import SessionLocal
from .workers.queue import TaskQueue

bus = EventBus()
events = EventService(SessionLocal, bus)
task_queue = TaskQueue(settings.max_workers, events)
