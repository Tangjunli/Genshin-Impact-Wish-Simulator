"""任务控制信号（取消 / 中断）。进程内实现；云端由队列的 revoke 机制承担。"""
from __future__ import annotations

import threading
from typing import Dict


class TaskCanceled(RuntimeError):
    pass


_registry: Dict[str, threading.Event] = {}
_lock = threading.Lock()


def control_event(task_id: str) -> threading.Event:
    with _lock:
        ev = _registry.get(task_id)
        if ev is None:
            ev = threading.Event()
            _registry[task_id] = ev
        return ev


def cancel(task_id: str) -> None:
    control_event(task_id).set()


def is_canceled(task_id: str) -> bool:
    ev = _registry.get(task_id)
    return bool(ev and ev.is_set())


def clear(task_id: str) -> None:
    with _lock:
        _registry.pop(task_id, None)


def raise_if_canceled(task_id: str) -> None:
    if is_canceled(task_id):
        raise TaskCanceled("任务已取消")
