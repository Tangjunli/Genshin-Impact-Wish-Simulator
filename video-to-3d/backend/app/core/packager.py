"""打包：manifest + viewer + README + 许可证声明 + 产物与日志，统一输出为 ZIP。"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..models import Task
from .progress import StepTracker
from .storage import sha256_file

OUTPUT_DIRS = {
    "scene": "output/scene",
    "person": "output/person",
    "motion": "output/motion",
    "preview": "output/preview",
}

LICENSE_NOTICES = """# 第三方组件与许可声明

- FreeMoCap：AGPL-3.0。本项目仅通过独立进程调用，未链接其代码；如随附分发，需同时提供其源码与许可。
- gsplat：Apache-2.0。原始 3D Gaussian Splatting 参考实现为研究/非商业许可，勿混用。
- MediaPipe：Apache-2.0。
- COLMAP：BSD-3-Clause。
- OOOSplat / LingBot-Map / GauHuman：采用/分发前请核实各自仓库许可证。
- SMPL / SMPL-X 体模：需向 Max Planck 取得授权；GauHuman 路径默认关闭。
"""

README_TEXT = """VideoTo3D 结果包

目录说明：
  scene/    场景 3DGS 模型（.ply / .glb / .splat）
  person/   人物模型（.glb / .fbx / .obj）
  motion/   动作数据（.bvh / .fbx / .csv）
  preview/  缩略图与 viewer.html（双击或拖入浏览器查看；viewer 需联网加载 three.js）
  logs/     处理日志
  metadata/ 步骤状态快照

字段含义与许可证见 manifest.json 与 LICENSE_NOTICES.md。
"""

VIEWER_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>VideoTo3D 预览 · __TASK_ID__</title>
<style>
  html,body{margin:0;height:100%;background:#121214;color:#e8e8ea;font-family:system-ui,-apple-system,"PingFang SC",sans-serif;overflow:hidden}
  #hud{position:fixed;left:16px;top:16px;font-size:13px;line-height:1.6;background:rgba(24,24,27,.82);padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.12)}
  #hud b{font-weight:600}
  #hint{position:fixed;right:16px;bottom:16px;font-size:12px;color:#9b9ba4}
  #msg{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;font-size:14px;color:#9b9ba4;text-align:center;padding:24px}
  body.ready #msg{display:none}
</style>
</head>
<body>
<div id="hud"><b>VideoTo3D</b><br/>任务 __TASK_ID__<br/><span style="color:#9b9ba4">生成于 __GENERATED__</span></div>
<div id="msg">正在加载模型…</div>
<div id="hint">拖拽旋转 · 滚轮缩放</div>
<script type="importmap">
{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x121214);
const camera = new THREE.PerspectiveCamera(50, innerWidth/innerHeight, 0.01, 1000);
camera.position.set(2.6, 2.0, 3.4);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
scene.add(new THREE.HemisphereLight(0xffffff, 0x2a2a33, 1.6));
scene.add(new THREE.AmbientLight(0xffffff, 0.35));
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

function done(object) {
  const box = new THREE.Box3().setFromObject(object);
  const center = box.getCenter(new THREE.Vector3());
  object.position.sub(center);
  scene.add(object);
  const size = box.getSize(new THREE.Vector3()).length() || 3;
  camera.position.setLength(size * 1.4);
  controls.target.set(0,0,0);
  controls.update();
  document.body.classList.add('ready');
}

async function loadGLB(url) {
  return new Promise((resolve, reject) => new GLTFLoader().load(url, (g) => resolve(g.scene), undefined, reject));
}
async function loadPLY(url) {
  return new Promise((resolve, reject) => new PLYLoader().load(url, (geo) => {
    geo.computeVertexNormals();
    const mat = new THREE.PointsMaterial({ size: 0.012, vertexColors: geo.hasAttribute('color') });
    resolve(new THREE.Points(geo, mat));
  }, undefined, reject));
}

(async () => {
  try {
    done(await loadGLB('../scene/scene.glb'));
  } catch (e1) {
    try {
      done(await loadPLY('../scene/scene.ply'));
    } catch (e2) {
      document.getElementById('msg').textContent = '未找到 scene.glb / scene.ply。若模型在子目录，请手动调整 viewer.html 中的路径。';
    }
  }
})();

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function animate(){ requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); })();
</script>
</body>
</html>
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_viewer(preview_dir: Path, task_id: str) -> Path:
    preview_dir.mkdir(parents=True, exist_ok=True)
    target = preview_dir / "viewer.html"
    html = (
        VIEWER_TEMPLATE.replace("__TASK_ID__", task_id)
        .replace("__GENERATED__", _now()[:19].replace("T", " "))
    )
    target.write_text(html, encoding="utf-8")
    return target


def _artifacts_in(workspace: Path) -> List[Tuple[str, Path]]:
    items: List[Tuple[str, Path]] = []
    for kind, rel in OUTPUT_DIRS.items():
        directory = workspace / rel
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.name != "viewer.html":
                items.append((kind, path))
    return items


def build_package(
    task: Task,
    workspace: Path,
    tracker: StepTracker,
    engine_meta: Dict[str, Any],
) -> Tuple[Path, Dict[str, Any], int]:
    package_dir = workspace / "package"
    package_dir.mkdir(parents=True, exist_ok=True)
    zip_path = package_dir / f"{task.id}.zip"

    viewer = write_viewer(workspace / "output" / "preview", task.id)
    files = _artifacts_in(workspace)
    log_path = workspace / "logs" / "pipeline.log"

    manifest_files: List[Dict[str, Any]] = []
    for kind, path in files:
        rel = path.relative_to(workspace).as_posix()
        archive_path = rel.replace("output/", "")
        manifest_files.append(
            {
                "path": archive_path,
                "kind": kind,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    steps_summary = [
        {
            "key": step["key"],
            "title": step["title"],
            "status": step["status"],
            "progress": step["progress"],
            "skipped_reason": (step.get("detail") or {}).get("skipped_reason"),
        }
        for step in tracker.steps
    ]

    manifest = {
        "schema_version": "1.0",
        "created_at": _now(),
        "task": {
            "id": task.id,
            "mode": task.mode,
            "status": task.status,
            "source": {
                "name": task.source_name,
                "size": task.source_size,
                "sha256": task.source_sha256,
            },
        },
        "engine": {
            "profile": task.engine_profile,
            "scene": engine_meta.get("scene"),
            "motion": engine_meta.get("motion"),
            "appearance": engine_meta.get("appearance"),
            "simulated": bool(engine_meta.get("simulated")),
        },
        "person": {
            "detected": None
            if task.person_detected is None
            else bool(task.person_detected),
            "policy": task.person_policy,
            "sources": [src for src in ("motion", "appearance") if engine_meta.get(src)],
        },
        "artifacts": manifest_files,
        "steps": steps_summary,
        "licenses": [
            {"component": "FreeMoCap", "license": "AGPL-3.0"},
            {"component": "gsplat", "license": "Apache-2.0"},
            {"component": "MediaPipe", "license": "Apache-2.0"},
            {"component": "COLMAP", "license": "BSD-3-Clause"},
            {"component": "SMPL/SMPL-X", "license": "需单独授权（默认关闭）"},
        ],
    }

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr(f"{task.id}/manifest.json",
                         json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr(f"{task.id}/README.txt", README_TEXT)
        archive.writestr(f"{task.id}/LICENSE_NOTICES.md", LICENSE_NOTICES)
        for _kind, path in files:
            rel = path.relative_to(workspace).as_posix().replace("output/", "")
            archive.write(path, f"{task.id}/{rel}")
        archive.write(viewer, f"{task.id}/preview/viewer.html")
        if log_path.exists():
            archive.write(log_path, f"{task.id}/logs/pipeline.log")
        archive.writestr(
            f"{task.id}/metadata/steps.json",
            json.dumps(tracker.steps, ensure_ascii=False, indent=2),
        )

    return zip_path, manifest, len(files)
