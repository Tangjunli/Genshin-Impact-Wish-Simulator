import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatBytes, uploadVideo, type UploadProgressInfo } from "../api/client";
import UploadProgress from "../components/UploadProgress";
import { PERSON_POLICY_LABEL, type PersonPolicy, type Probe, type TaskMode } from "../api/types";

type Phase = "idle" | "creating" | "uploading" | "paused" | "queued" | "error";

const POLICIES: PersonPolicy[] = ["auto", "motion_only", "appearance", "none"];

function isDesktopShell(): boolean {
  if (typeof window === "undefined") return false;
  const scope = window as unknown as Record<string, unknown>;
  return "__TAURI_INTERNALS__" in scope || "__TAURI__" in scope;
}

export default function UploadPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<TaskMode>("cloud");
  const [policy, setPolicy] = useState<PersonPolicy>("auto");
  const [file, setFile] = useState<File | null>(null);
  const [localPath, setLocalPath] = useState("");
  const [probe, setProbe] = useState<Probe | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [uploadInfo, setUploadInfo] = useState<UploadProgressInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const loadProbe = async (force = false) => {
    setProbing(true);
    setProbeError(null);
    try {
      const data = await api.probe(force);
      setProbe(data);
      setMode((current) => (force ? data.recommendation.mode : current));
    } catch (err) {
      setProbeError(err instanceof Error ? err.message : "无法探测本机能力");
    } finally {
      setProbing(false);
    }
  };

  useEffect(() => {
    void loadProbe();
  }, []);

  const pickFile = (next: File | null) => {
    setNotice(null);
    setError(null);
    setUploadInfo(null);
    setTaskId(null);
    setPhase("idle");
    setFile(next);
  };

  const runUpload = async (id: string, target: File) => {
    const controller = new AbortController();
    abortRef.current = controller;
    setPhase("uploading");
    setError(null);
    try {
      const detail = await uploadVideo(id, target, {
        signal: controller.signal,
        onProgress: setUploadInfo,
      });
      setPhase("queued");
      navigate(`/tasks/${detail.id}`);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setPhase("error");
      setError(err instanceof Error ? err.message : "上传失败，请重试");
    }
  };

  const start = async () => {
    setNotice(null);
    setError(null);
    if (mode === "cloud") {
      if (!file) {
        setError("请先选择视频文件");
        return;
      }
      setPhase("creating");
      try {
        const task = await api.createCloudTask({
          source_name: file.name,
          source_size: file.size,
          person_policy: policy,
        });
        setTaskId(task.id);
        await runUpload(task.id, file);
      } catch (err) {
        setPhase("error");
        setError(err instanceof Error ? err.message : "创建任务失败");
      }
      return;
    }

    const path = localPath.trim();
    if (!path) {
      setError("请输入本机视频的绝对路径");
      return;
    }
    setPhase("creating");
    try {
      const task = await api.createLocalTask({ path, person_policy: policy });
      navigate(`/tasks/${task.id}`);
    } catch (err) {
      setPhase("error");
      setError(err instanceof Error ? err.message : "创建本地任务失败");
    }
  };

  const pause = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase("paused");
  };

  const resume = () => {
    if (!taskId || !file) return;
    void runUpload(taskId, file);
  };

  const cancel = async () => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (taskId) {
      try {
        await api.cancelTask(taskId);
      } catch {
        /* 任务可能已进入队列，忽略 */
      }
    }
    setPhase("idle");
    setUploadInfo(null);
    setTaskId(null);
    setNotice("已放弃本次上传，任务记录仍保留在历史记录中。");
  };

  const busy = phase === "uploading" || phase === "paused" || phase === "creating" || phase === "queued";
  const recommend = probe?.recommendation;

  return (
    <div className="page">
      <div className="page-heading">
        <h1>视频生成 3D 模型</h1>
        <p className="muted">
          本地模式视频不上传；云端模式分片上传并实时展示后端真实进度。含人物的视频会自动提取动作或人物模型。
        </p>
      </div>

      <div className="grid-2">
        <section className="panel">
          <div className="panel-head">
            <h2>处理方式</h2>
            {recommend && (
              <button className="btn tiny" onClick={() => setMode(recommend.mode)}>
                使用推荐：{recommend.mode === "local" ? "本地" : "云端"}
              </button>
            )}
          </div>
          <div className="segmented">
            <button
              className={mode === "cloud" ? "active" : ""}
              onClick={() => setMode("cloud")}
              disabled={busy}
            >
              云端处理
            </button>
            <button
              className={mode === "local" ? "active" : ""}
              onClick={() => setMode("local")}
              disabled={busy}
            >
              本地处理
            </button>
          </div>

          {mode === "cloud" ? (
            <div className="field">
              <label>视频文件</label>
              <div
                className={`dropzone ${dragging ? "dragging" : ""} ${file ? "has-file" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  const dropped = event.dataTransfer.files?.[0];
                  if (dropped) pickFile(dropped);
                }}
              >
                <input
                  type="file"
                  accept="video/*,.mp4,.mov,.avi,.mkv,.webm,.m4v"
                  onChange={(event) => pickFile(event.target.files?.[0] ?? null)}
                  disabled={busy}
                />
                {file ? (
                  <div className="dropzone-file">
                    <strong>{file.name}</strong>
                    <span className="muted">{formatBytes(file.size)}</span>
                  </div>
                ) : (
                  <div>
                    <strong>点击或拖拽视频到这里</strong>
                    <div className="muted">MP4 / MOV / AVI / MKV / WEBM</div>
                  </div>
                )}
              </div>
              {file && file.size > 1024 * 1024 * 1024 && (
                <div className="hint">文件超过 1 GB，将自动分片上传；弱网环境请保持页面在前台。</div>
              )}
            </div>
          ) : (
            <div className="field">
              <label>本机视频绝对路径</label>
              <input
                className="input"
                value={localPath}
                onChange={(event) => setLocalPath(event.target.value)}
                placeholder="/Users/me/videos/scene.mp4"
                disabled={busy}
              />
              {!isDesktopShell() && (
                <div className="warn-box">
                  浏览器端无法读取本机文件路径。请在桌面端（Tauri 外壳）中使用本地模式，或改用云端处理。
                </div>
              )}
              <div className="hint">
                本地模式由桌面端 sidecar 直接读取该路径，视频不会上传。若本机不满足条件，可随时切换到云端。
              </div>
            </div>
          )}

          <div className="field">
            <label>人物处理策略</label>
            <select
              className="select wide"
              value={policy}
              onChange={(event) => setPolicy(event.target.value as PersonPolicy)}
              disabled={busy}
            >
              {POLICIES.map((item) => (
                <option key={item} value={item}>
                  {PERSON_POLICY_LABEL[item]}
                </option>
              ))}
            </select>
          </div>

          {error && <div className="error-box">{error}</div>}
          {notice && <div className="notice-box">{notice}</div>}

          <div className="row-actions">
            <button className="btn primary" onClick={start} disabled={busy}>
              {phase === "creating" ? "正在创建任务…" : mode === "cloud" ? "创建任务并上传" : "开始本地处理"}
            </button>
            {phase === "error" && taskId && file && (
              <button className="btn" onClick={resume}>
                重试上传（跳过已上传分片）
              </button>
            )}
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>本机能力探测</h2>
            <button className="btn tiny" onClick={() => void loadProbe(true)} disabled={probing}>
              {probing ? "检测中…" : "重新检测"}
            </button>
          </div>
          {probeError && <div className="error-box">{probeError}</div>}
          {!probe && !probeError && <div className="muted">正在读取硬件与引擎能力…</div>}
          {probe && (
            <div className="probe">
              <div className="kv">
                <span>GPU</span>
                <span>
                  {probe.device.gpu
                    ? `${probe.device.gpu.name}${probe.device.gpu.vram_gb ? ` · ${probe.device.gpu.vram_gb} GB` : ""}`
                    : "未检测到 NVIDIA GPU"}
                </span>
              </div>
              <div className="kv">
                <span>内存 / CPU</span>
                <span>
                  {probe.device.ram_gb ? `${probe.device.ram_gb} GB` : "—"} · {probe.device.cpu_count ?? "—"} 核
                </span>
              </div>
              <div className="kv">
                <span>运行模式</span>
                <span className={recommend?.mode === "local" ? "text-ok" : "text-warn"}>
                  {recommend?.mode === "local" ? "可本地处理" : "建议云端处理"}
                </span>
              </div>
              <div className="chips">
                {Object.entries(probe.tools).map(([tool, ok]) => (
                  <span key={tool} className={`chip ${ok ? "chip-ok" : "chip-off"}`}>
                    {tool}
                  </span>
                ))}
              </div>
              {recommend?.blockers.length ? (
                <ul className="blockers">
                  {recommend.blockers.map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ul>
              ) : (
                <div className="text-ok">本机满足本地处理条件。</div>
              )}
              {recommend?.mode === "cloud" && mode === "local" && (
                <div className="warn-box">
                  当前选择本地模式，但探测到阻塞项。迁移到云端可获得稳定 GPU 与更完整的人物重建能力。
                </div>
              )}
              {probe.engines.length > 0 && (
                <div className="engine-list">
                  {probe.engines.map((engine, index) => (
                    <div key={`${engine.name}-${engine.capability}-${index}`} className="kv">
                      <span>
                        {engine.name} · {engine.capability}
                      </span>
                      <span className={engine.available ? "text-ok" : "muted"}>
                        {engine.available ? "可用" : "未配置"}
                        {engine.local_only || engine.location === "local" ? " · 本地" : ""}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      </div>

      {uploadInfo && file && (phase === "uploading" || phase === "paused" || phase === "error") && (
        <UploadProgress
          info={uploadInfo}
          fileName={file.name}
          paused={phase === "paused"}
          error={phase === "error" ? error : null}
          onPause={pause}
          onResume={resume}
          onCancel={cancel}
        />
      )}
    </div>
  );
}
