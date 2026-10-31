"""HTTP 作业客户端（LingBot-Map / GauHuman 等服务共用）。

约定后端推理服务提供：
  POST {base}/jobs        -> {"job_id": "..."}
  GET  {base}/jobs/{id}   -> {"status": "queued|running|completed|failed",
                              "progress": 0..1, "stage": "pose|train|render",
                              "error": "...", "artifacts": [{"path": "...", "url": "..."}]}
  GET  {base}/files?path= -> 文件内容
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class HttpJobError(RuntimeError):
    pass


class HttpJobClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.URLError as exc:
            raise HttpJobError(f"推理服务不可达: {exc}") from exc

    def submit(self, payload: dict) -> str:
        body = self._request("POST", "/jobs", payload)
        job_id = body.get("job_id")
        if not job_id:
            raise HttpJobError("推理服务未返回 job_id")
        return str(job_id)

    def status(self, job_id: str) -> dict:
        return self._request("GET", f"/jobs/{job_id}")

    def wait(
        self,
        job_id: str,
        *,
        on_update,
        check_cancel,
        interval: float = 2.0,
        max_seconds: float = 3600,
    ) -> dict:
        started = time.monotonic()
        while True:
            check_cancel()
            state = self.status(job_id)
            on_update(state)
            if state.get("status") in ("completed", "failed", "canceled"):
                return state
            if time.monotonic() - started > max_seconds:
                raise HttpJobError("推理作业超时")
            time.sleep(interval)

    def download(self, url: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        full = url if url.startswith("http") else f"{self.base}{url}"
        with urllib.request.urlopen(full, timeout=self.timeout) as resp:
            target.write_bytes(resp.read())
        return target
