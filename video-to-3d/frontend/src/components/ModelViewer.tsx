import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { artifactUrl, formatBytes } from "../api/client";
import type { Artifact } from "../api/types";

const VIEWABLE = new Set(["glb", "gltf", "ply"]);

interface Props {
  artifacts: Artifact[];
}

function suffixOf(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

function supportsWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

function fitCamera(camera: THREE.PerspectiveCamera, controls: OrbitControls, object: THREE.Object3D) {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.x, size.y, size.z) * 0.5 || 1;
  camera.near = radius / 100;
  camera.far = radius * 100;
  camera.position.set(center.x + radius * 1.6, center.y + radius * 1.2, center.z + radius * 2.2);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

export default function ModelViewer({ artifacts }: Props) {
  const viewable = useMemo(
    () =>
      artifacts.filter(
        (artifact) =>
          (artifact.kind === "scene" || artifact.kind === "person") &&
          VIEWABLE.has(suffixOf(artifact.filename)),
      ),
    [artifacts],
  );
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected =
    viewable.find((artifact) => artifact.id === selectedId) ?? viewable[0] ?? null;

  useEffect(() => {
    if (!selected || !containerRef.current) return;
    const container = containerRef.current;
    const suffix = suffixOf(selected.filename);
    const url = artifactUrl(selected);

    let disposed = false;
    setLoading(true);
    setError(null);

    if (!supportsWebGL()) {
      setLoading(false);
      setError("当前环境不支持 WebGL，无法内嵌预览。可下载 GLB / PLY 后用本地查看器打开。");
      return;
    }

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setLoading(false);
      setError("WebGL 初始化失败，可下载模型后本地查看。");
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(container.clientWidth || 320, container.clientHeight || 320);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 1000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x2a2d3a, 2.2));
    const light = new THREE.DirectionalLight(0xffffff, 1.8);
    light.position.set(4, 6, 5);
    scene.add(light);

    const resize = () => {
      const width = container.clientWidth || 320;
      const height = container.clientHeight || 320;
      renderer.setSize(width, height);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();

    const onLoaded = (object: THREE.Object3D) => {
      if (disposed) {
        object.traverse((child) => {
          const mesh = child as THREE.Mesh;
          mesh.geometry?.dispose?.();
          const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
          if (Array.isArray(material)) material.forEach((entry) => entry.dispose());
          else material?.dispose?.();
        });
        return;
      }
      scene.add(object);
      fitCamera(camera, controls, object);
      setLoading(false);
    };

    if (suffix === "ply") {
      new PLYLoader().load(
        url,
        (geometry) => {
          geometry.computeBoundingSphere();
          const hasColor = geometry.getAttribute("color") !== undefined;
          const material = new THREE.PointsMaterial({
            size: geometry.boundingSphere ? geometry.boundingSphere.radius / 220 : 0.01,
            vertexColors: hasColor,
            color: hasColor ? undefined : 0xb9b3ff,
            sizeAttenuation: true,
          });
          onLoaded(new THREE.Points(geometry, material));
        },
        undefined,
        () => {
          if (!disposed) {
            setError("PLY 模型加载失败，请尝试下载后本地查看");
            setLoading(false);
          }
        },
      );
    } else {
      new GLTFLoader().load(
        url,
        (gltf) => onLoaded(gltf.scene),
        undefined,
        () => {
          if (!disposed) {
            setError("GLB 模型加载失败，请尝试下载后本地查看");
            setLoading(false);
          }
        },
      );
    }

    renderer.setAnimationLoop(() => {
      controls.update();
      renderer.render(scene, camera);
    });

    return () => {
      disposed = true;
      renderer.setAnimationLoop(null);
      observer.disconnect();
      controls.dispose();
      scene.traverse((child) => {
        const mesh = child as THREE.Mesh;
        mesh.geometry?.dispose?.();
        const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(material)) material.forEach((entry) => entry.dispose());
        else material?.dispose?.();
      });
      renderer.dispose();
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [selected?.id]);

  if (!viewable.length) {
    return (
      <section className="panel">
        <div className="panel-head">
          <h2>3D 预览</h2>
        </div>
        <div className="muted">暂无可预览的 GLB / PLY 模型。任务完成后会在这里显示。</div>
      </section>
    );
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>3D 预览</h2>
        <select
          className="select"
          value={selected?.id ?? ""}
          onChange={(event) => setSelectedId(Number(event.target.value))}
        >
          {viewable.map((artifact) => (
            <option key={artifact.id} value={artifact.id}>
              {artifact.filename}（{formatBytes(artifact.size)}）
            </option>
          ))}
        </select>
      </div>
      <div className="viewer" ref={containerRef}>
        {loading && <div className="viewer-hint">正在加载模型…</div>}
        {error && <div className="viewer-hint error-text">{error}</div>}
      </div>
      <div className="muted viewer-tip">拖拽旋转 · 滚轮缩放 · 右键平移</div>
    </section>
  );
}
