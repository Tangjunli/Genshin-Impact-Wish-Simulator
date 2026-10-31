#!/usr/bin/env python3
"""端到端冒烟测试：真实 ffmpeg 抽帧 + 分片上传 + SSE + ZIP 打包 + Range 下载。

运行：V3D_ENGINE_MODE=mock python3 tests/smoke_e2e.py
说明：模型产物由 mock 引擎生成（manifest.simulated=true），用于验证链路而非算法效果。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("V3D_TEST_PORT", "8799"))
BASE = f"http://127.0.0.1:{PORT}"


def http(method: str, path: str, data: bytes | None = None, headers: dict | None = None,
         timeout: float = 60):
    request = urllib.request.Request(BASE + path, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read(), dict(response.headers)


def as_json(method: str, path: str, payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    status, raw, _ = http(method, path, body, {"Content-Type": "application/json"})
    return status, json.loads(raw or b"{}")


def wait_health(timeout: float = 40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, raw, _ = http("GET", "/health", timeout=2)
            if status == 200:
                return json.loads(raw)
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.3)
    raise RuntimeError("服务未在预期时间内启动")


def wait_task(task_id: str, timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, task = as_json("GET", f"/api/v1/tasks/{task_id}")
        if task["status"] in ("completed", "failed", "canceled", "interrupted"):
            return task
        time.sleep(0.4)
    raise RuntimeError(f"任务超时未完成: {task_id}")


class SSECollector(threading.Thread):
    def __init__(self, task_id: str):
        super().__init__(daemon=True)
        self.task_id = task_id
        self.events: list[dict] = []
        self.stop = threading.Event()

    def run(self):
        url = f"{BASE}/api/v1/tasks/{self.task_id}/events"
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                event_type = None
                for raw in response:
                    if self.stop.is_set():
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event_type:
                        try:
                            payload = json.loads(line.split(":", 1)[1].strip())
                        except ValueError:
                            payload = {}
                        self.events.append({"event": event_type, "data": payload})
                        event_type = None
        except Exception:  # 连接在测试结束时被关闭
            pass


def make_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=4:size=640x360:rate=25",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )


def upload_chunks(task_id: str, video: Path) -> None:
    blob = video.read_bytes()
    _, init = as_json("POST", f"/api/v1/tasks/{task_id}/upload/init",
                      {"total_bytes": len(blob)})
    chunk_size = init["chunk_size"]
    total_chunks = init["total_chunks"]
    print(f"  分片: {total_chunks} × {chunk_size} bytes")

    for index in range(init["total_chunks"]):
        start = index * chunk_size
        chunk = blob[start:start + chunk_size]
        status, raw, _ = http(
            "PUT",
            f"/api/v1/tasks/{task_id}/upload/chunks/{index}",
            chunk,
            {"Content-Type": "application/octet-stream"},
        )
        assert status == 200, raw
        if index in (0, 2, total_chunks - 1):
            print(f"  已上传分片 {index}: {json.loads(raw)['percent']:.0%}")

    _, status_payload = as_json("GET", f"/api/v1/tasks/{task_id}/upload/status")
    assert status_payload["received_bytes"] == len(blob), status_payload
    assert status_payload["total_chunks"] == total_chunks

    digest = hashlib.sha256(blob).hexdigest()
    status, task = as_json("POST", f"/api/v1/tasks/{task_id}/upload/complete",
                           {"sha256": digest})
    assert status == 200, task
    print(f"  合并完成，状态: {task['status']}")


def run_cloud_flow(video: Path) -> dict:
    print("[1] 云端任务")
    size = video.stat().st_size
    _, task = as_json("POST", "/api/v1/tasks", {
        "mode": "cloud", "source_name": video.name, "source_size": size,
        "person_policy": "auto",
    })
    task_id = task["id"]
    print(f"  task_id={task_id}")

    collector = SSECollector(task_id)
    collector.start()
    time.sleep(0.2)
    upload_chunks(task_id, video)

    final = wait_task(task_id)
    print(f"  完成: status={final['status']} overall={final['overall_progress']}")
    assert final["status"] == "completed", final
    assert final["overall_progress"] == 1.0
    assert final["person_detected"] is True
    for step in final["steps"]:
        assert step["status"] == "completed", step

    outputs = final["outputs"]
    for key in ("scene", "person", "motion", "preview", "package"):
        assert outputs.get(key), f"缺少产物 {key}: {outputs}"

    time.sleep(0.8)
    collector.stop.set()
    types = {event["event"] for event in collector.events}
    print(f"  SSE 事件类型: {sorted(types)}")
    assert "snapshot" in types
    assert "step.update" in types
    assert "task.status" in types
    assert "log" in types

    # ZIP 完整性
    status, blob, headers = http("GET", f"/api/v1/tasks/{task_id}/download.zip")
    assert status == 200
    names = zipfile.ZipFile(__import__("io").BytesIO(blob)).namelist()
    for expected in ("scene/scene.ply", "scene/scene.glb", "person/person.glb",
                     "motion/motion.csv", "preview/viewer.html", "manifest.json"):
        assert f"{task_id}/{expected}" in names, names
    manifest = json.loads(zipfile.ZipFile(__import__("io").BytesIO(blob))
                          .read(f"{task_id}/manifest.json"))
    assert manifest["engine"]["simulated"] is True
    print(f"  ZIP: {len(names)} 个条目, {len(blob) // 1024} KB")

    # Range 断点续传
    status, part, headers = http("GET", f"/api/v1/tasks/{task_id}/download.zip",
                                 headers={"Range": "bytes=0-99"})
    assert status == 206, (status, headers)
    assert part == blob[:100]
    assert headers.get("content-range", "").startswith("bytes 0-99/")
    print(f"  Range 续传正常: {headers.get('content-range')}")
    return final


def run_local_flow(video: Path) -> dict:
    print("[2] 本地任务（视频不上传）")
    _, task = as_json("POST", "/api/v1/tasks/local",
                      {"path": str(video), "person_policy": "auto"})
    task_id = task["id"]
    assert task["source"]["path"] == str(video)
    final = wait_task(task_id)
    assert final["status"] == "completed", final
    assert final["outputs"]["package"], final["outputs"]
    print(f"  完成: {task_id} -> {final['outputs']['package']}")
    return final


def run_cancel_flow(video: Path) -> None:
    print("[3] 取消任务")
    _, task = as_json("POST", "/api/v1/tasks/local",
                      {"path": str(video), "person_policy": "auto"})
    task_id = task["id"]
    status, canceled = as_json("POST", f"/api/v1/tasks/{task_id}/cancel")
    assert status == 200, canceled
    assert canceled["status"] in ("canceled", "running", "failed"), canceled
    final = wait_task(task_id, timeout=60)
    assert final["status"] in ("canceled", "failed"), final
    print(f"  取消后状态: {final['status']}")


def main() -> int:
    if not shutil.which("ffmpeg"):
        print("缺少 ffmpeg", file=sys.stderr)
        return 2

    data_dir = Path(tempfile.mkdtemp(prefix="v3d-smoke-"))
    video = data_dir / "sample.mp4"
    make_video(video)

    env = {
        **os.environ,
        "V3D_DATA_DIR": str(data_dir),
        "V3D_ENGINE_MODE": "mock",
        "V3D_CHUNK_SIZE": str(256 * 1024),
        "V3D_WORKERS": "2",
        "V3D_AUTO_RESUME": "true",
        "V3D_CORS_ORIGINS": "*",
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        info = wait_health()
        print(f"服务就绪: {info}")
        status, probe, _ = http("GET", "/api/v1/system/probe")
        assert status == 200
        probe = json.loads(probe)
        assert probe["recommendation"]["mode"] in ("local", "cloud")
        print(f"[0] 探测: {probe['recommendation']}")

        cloud = run_cloud_flow(video)
        local = run_local_flow(video)
        run_cancel_flow(video)

        _, history = as_json("GET", "/api/v1/tasks?limit=10")
        ids = {item["id"] for item in history["items"]}
        assert cloud["id"] in ids and local["id"] in ids

        _, detail = as_json("GET", f"/api/v1/tasks/{cloud['id']}")
        _, logs = as_json("GET", f"/api/v1/tasks/{cloud['id']}/logs")
        assert logs["items"], "日志为空"
        _, artifacts = as_json("GET", f"/api/v1/tasks/{cloud['id']}/artifacts")
        assert len(artifacts["items"]) >= 5

        print("\n冒烟测试全部通过")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
