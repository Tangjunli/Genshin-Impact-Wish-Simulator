"""进程内任务队列。

生产环境替换为 Celery/RQ：保留 `enqueue(task_id)` 与 `run_pipeline(task_id, events)` 两个契约即可，
把 run_pipeline 包成 Celery task，GPU worker 池按队列名路由。
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("v3d.queue")


class TaskQueue:
    def __init__(self, workers: int, events):
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v3d-worker")
        self._events = events
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    def enqueue(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._pending:
                return False
            self._pending.add(task_id)
        self._executor.submit(self._run, task_id)
        return True

    def _run(self, task_id: str) -> None:
        # 延迟导入，避免 api/pipeline 之间的循环依赖
        from ..core.pipeline import run_pipeline

        try:
            run_pipeline(task_id, self._events)
        except Exception:  # noqa: BLE001 - 后台任务不能静默消失
            logger.exception("任务执行崩溃: %s", task_id)
        finally:
            with self._lock:
                self._pending.discard(task_id)

    def is_pending(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._pending

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
