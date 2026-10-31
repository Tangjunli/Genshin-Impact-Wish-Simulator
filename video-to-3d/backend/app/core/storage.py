"""工作区路径与文件工具。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from ..config import settings

SUBDIRS = (
    "input",
    "upload/parts",
    "frames",
    "work",
    "output/scene",
    "output/person",
    "output/motion",
    "output/preview",
    "logs",
    "package",
)


def task_dir(task_id: str) -> Path:
    return settings.task_root() / task_id


def ensure_task_dirs(task_id: str) -> Path:
    root = task_dir(task_id)
    for name in SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
