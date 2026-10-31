import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  artifactUrl,
  formatBytes,
  zipUrl,
} from "../api/client";
import { subscribeTask, type StreamState } from "../api/stream";
import LogPanel from "../components/LogPanel";
import ModelViewer from "../components/ModelViewer";
import StepTimeline from "../components/StepTimeline";
import {
  PERSON_POLICY_LABEL,
  TASK_STATUS_LABEL,
  type Artifact,
  type LogEntry,
  type Step,
  type StepStatus,
  type TaskDetail,
  type TaskStatus,
} from "../api/types";

const ACTIVE_STATUSES: TaskStatus[] = ["created", "uploading", "queued", "running", "packaging"];
const TERMINAL_STATUSES: TaskStatus[] = ["completed", "failed", "canceled", "interrupted"];

const KIND_LABEL: Record<string, string> = {
  scene: "场景模型",
  person: "人物模型",
  motion: "动作数据",
  preview: "预览文件",
  package: "打包文件",
  misc: "其他",
};

interface UploadState {
  received: number;
  total: number;
  percent: number;
  chunks: number;
  totalChunks: number;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export default function TaskDetailPage() {
  const { id = "" } = useParams();
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [streamState, setStreamState] = useState<StreamState>("connecting");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [uploadState, setUploadState] = useState<UploadState | null>(null);
  const refreshedRef = useRef(false);

  const reload = async () => {
    try {
      const [detail, list, logPage] = await Promise.all([
        api.getTask(id),
        api.listArtifacts(id),
        api.getLogs(id, 0, 500),
      ]);
      setTask(detail);
      setArtifacts(list.items);
      setLogs((previous) => {
        const merged = new Map<string, LogEntry>();
        logPage.items.forEach((entry) => merged.set(`db-${entry.id}`, entry));
        previous.forEach((entry) => {
          const key = entry.id ? `db-${entry.id}` : `ev-${entry.ts}-${entry.message}`;
          if (!merged.has(key)) merged.set(key, entry);
        });
        return [...merged.values()].sort((a, b) => (a.ts < b.ts ? -1 : 1));
      });
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "任务加载失败");
    }
  };

  useEffect(() => {
    refreshedRef.current = false;
    setTask(null);
    setArtifacts([]);
    setLogs([]);
    setUploadState(null);
    void reload();
  }, [id]);

  useEffect(() => {
    if (!id) return;
    const close = subscribeTask(id, {
      onState: setStreamState,
      onSnapshot: ({ task: snapshotTask, logs: snapshotLogs }) => {
        setTask(snapshotTask);
        setLogs((previous) => {
          if (previous.length === 0) return snapshotLogs;
          const merged = new Map<string, LogEntry>();
          snapshotLogs.forEach((entry) => merged.set(`db-${entry.id}`, entry));
          previous.forEach((entry) => {
            const key = entry.id ? `db-${entry.id}` : `ev-${entry.ts}-${entry.message}`;
            if (!merged.has(key)) merged.set(key, entry);
          });
          return [...merged.values()].sort((a, b) => (a.ts < b.ts ? -1 : 1));
        });
        if (TERMINAL_STATUSES.includes(snapshotTask.status) && !refreshedRef.current) {
          refreshedRef.current = true;
          void reload();
        }
      },
      onEvent: (type, data) => {
        if (type === "log") {
          const entry: LogEntry = {
            id: typeof data.__event_id === "string" ? Number(data.__event_id) : undefined,
            ts: String(data.ts ?? new Date().toISOString()),
            level: String(data.level ?? "info"),
            step: data.step ? String(data.step) : null,
            message: String(data.message ?? ""),
          };
          setLogs((previous) =>
            entry.id && previous.some((item) => item.id === entry.id)
              ? previous
              : [...previous, entry],
          );
          return;
        }
        if (type === "step.update") {
          setTask((previous) => {
            if (!previous) return previous;
            const key = String(data.key ?? "");
            const nextSteps: Step[] = previous.steps.map((step) =>
              step.key === key
                ? {
                    ...step,
                    status: (data.status as StepStatus) ?? step.status,
                    progress: Number(data.progress ?? step.progress),
                    detail: (data.detail as Step["detail"]) ?? step.detail,
                    error: (data.error as string | null) ?? step.error,
                  }
                : step,
            );
            return {
              ...previous,
              steps: nextSteps,
              overall_progress: Number(data.overall ?? previous.overall_progress),
              current_step: key,
            };
          });
          return;
        }
        if (type === "task.status") {
          const status = data.status as TaskStatus;
          setTask((previous) =>
            previous
              ? {
                  ...previous,
                  status,
                  current_step: (data.current_step as string | null) ?? previous.current_step,
                  overall_progress: Number(data.overall ?? previous.overall_progress),
                  error: (data.error as TaskDetail["error"]) ?? null,
                }
              : previous,
          );
          if (TERMINAL_STATUSES.includes(status) && !refreshedRef.current) {
            refreshedRef.current = true;
            void reload();
          }
          return;
        }
        if (type === "task.error") {
          setTask((previous) =>
            previous
              ? {
                  ...previous,
                  error: {
                    code: String(data.code ?? "error"),
                    message: String(data.message ?? "处理失败"),
                    retryable: Boolean(data.retryable),
                    suggestion: data.suggestion ? String(data.suggestion) : null,
                  },
                }
              : previous,
          );
          return;
        }
        if (type === "upload.progress") {
          setUploadState({
            received: Number(data.received_bytes ?? 0),
            total: Number(data.total_bytes ?? 0),
            percent: Number(data.percent ?? 0),
            chunks: Number(data.chunk_index ?? -1) + 1,
            totalChunks: Number(data.total_chunks ?? 0),
          });
          return;
        }
        if (type === "artifact.ready") {
          void api.listArtifacts(id).then((list) => setArtifacts(list.items)).catch(() => undefined);
        }
      },
    });
    return close;
  }, [id]);

  const terminal = task ? TERMINAL_STATUSES.includes(task.status) : false;
  const active = task ? ACTIVE_STATUSES.includes(task.status) : false;
  const preview = useMemo(
    () =>
      artifacts.find(
        (artifact) =>
          artifact.kind === "preview" && /\.(jpg|jpeg|png|webp)$/i.test(artifact.filename),
      ) ?? null,
    [artifacts],
  );
  const viewerHtml = useMemo(
    () => artifacts.find((artifact) => artifact.filename.toLowerCase().endsWith(".html")) ?? null,
    [artifacts],
  );

  const runAction = async (action: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await action();
      await reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "操作失败");
    }
  };

  if (loadError && !task) {
    return (
      <div className="page">
        <div className="error-box">{loadError}</div>
        <Link className="btn" to="/history">
          返回历史记录
        </Link>
      </div>
    );
  }

  if (!task) {
    return (
      <div className="page">
        <div className="muted">任务加载中…</div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-heading detail-heading">
        <div>
          <Link className="back-link" to="/history">
            ← 历史记录
          </Link>
          <h1 className="detail-title">{task.source.name || task.id}</h1>
          <div className="meta-row">
            <span className={`status-pill status-${task.status}`}>{TASK_STATUS_LABEL[task.status]}</span>
            <span className="chip">{task.mode === "local" ? "本地处理" : "云端处理"}</span>
            <span className="chip">{PERSON_POLICY_LABEL[task.person_policy]}</span>
            {task.person_detected !== null && (
              <span className={`chip ${task.person_detected ? "chip-ok" : "chip-off"}`}>
                {task.person_detected ? "检测到人物" : "未检测到人物"}
              </span>
            )}
            <span className="muted">更新于 {formatTime(task.updated_at)}</span>
          </div>
        </div>
        <div className="row-actions">
          {task.status === "completed" && (
            <a className="btn primary" href={zipUrl(task.id)} download>
              下载 ZIP
            </a>
          )}
          {active && (
            <button className="btn danger-ghost" onClick={() => runAction(() => api.cancelTask(task.id))}>
              取消任务
            </button>
          )}
          {(task.status === "failed" || task.status === "interrupted" || task.status === "canceled") && (
            <button className="btn" onClick={() => runAction(() => api.retryTask(task.id))}>
              继续处理
            </button>
          )}
          <button className="btn" onClick={() => void reload()}>
            刷新
          </button>
        </div>
      </div>

      {task.error && (
        <div className="error-box">
          <strong>{task.error.message}</strong>
          {task.error.suggestion && <div className="muted">{task.error.suggestion}</div>}
        </div>
      )}
      {actionError && <div className="error-box">{actionError}</div>}

      {task.status === "uploading" && (
        <section className="panel">
          <div className="panel-head">
            <h2>视频上传中</h2>
            <span className="muted">
              {uploadState ? `${formatBytes(uploadState.received)} / ${formatBytes(uploadState.total)}` : "等待进度"}
            </span>
          </div>
          <div className="progress-track">
            <span style={{ width: `${(uploadState?.percent ?? 0) * 100}%` }} />
          </div>
          <div className="hint">
            上传进度由服务端落盘字节数推送。若页面关闭，回到新建任务页重新选择同一个文件即可从已上传分片续传。
          </div>
        </section>
      )}

      {task.status === "created" && (
        <div className="notice-box">任务已创建，尚未开始上传。请回到新建任务页选择文件继续。</div>
      )}

      <div className="detail-grid">
        <StepTimeline steps={task.steps} overall={task.overall_progress} status={task.status} />
        <LogPanel logs={logs} streamState={streamState} />
      </div>

      <div className="detail-grid">
        <div className="stack">
          {preview && (
            <section className="panel">
              <div className="panel-head">
                <h2>预览缩略图</h2>
              </div>
              <img className="preview-image" src={artifactUrl(preview)} alt="任务缩略图" />
            </section>
          )}
          <section className="panel">
            <div className="panel-head">
              <h2>产物文件</h2>
              <span className="muted">{artifacts.length} 个</span>
            </div>
            {artifacts.length === 0 && <div className="muted">任务完成后会列出场景模型、人物模型、动作数据与预览文件。</div>}
            <ul className="artifact-list">
              {artifacts.map((artifact) => (
                <li key={artifact.id}>
                  <div>
                    <a href={artifactUrl(artifact)} download>
                      {artifact.filename}
                    </a>
                    <div className="muted">
                      {KIND_LABEL[artifact.kind] ?? artifact.kind} · {formatBytes(artifact.size)}
                    </div>
                  </div>
                  <a className="btn tiny" href={artifactUrl(artifact)} download>
                    下载
                  </a>
                </li>
              ))}
            </ul>
            {viewerHtml && (
              <a className="btn" href={artifactUrl(viewerHtml)} target="_blank" rel="noreferrer">
                打开原生 3D Viewer
              </a>
            )}
          </section>
        </div>
        <div className="stack">
          <ModelViewer artifacts={artifacts} />
          <section className="panel">
            <div className="panel-head">
              <h2>任务信息</h2>
            </div>
            <div className="kv">
              <span>任务 ID</span>
              <span className="mono">{task.id}</span>
            </div>
            <div className="kv">
              <span>视频大小</span>
              <span>{formatBytes(task.source.size)}</span>
            </div>
            <div className="kv">
              <span>创建时间</span>
              <span>{formatTime(task.created_at)}</span>
            </div>
            <div className="kv">
              <span>开始 / 结束</span>
              <span>
                {formatTime(task.started_at)} / {formatTime(task.finished_at)}
              </span>
            </div>
            <div className="kv">
              <span>重试次数</span>
              <span>{task.retry_count}</span>
            </div>
            {task.mode === "local" && task.source.path && (
              <div className="kv">
                <span>本地路径</span>
                <span className="mono ellipsis" title={task.source.path}>
                  {task.source.path}
                </span>
              </div>
            )}
            {terminal && (
              <div className="kv">
                <span>打包结果</span>
                <span>
                  {task.outputs.package ? (
                    <a href={zipUrl(task.id)}>下载 {task.id}.zip</a>
                  ) : (
                    <span className="muted">无</span>
                  )}
                </span>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
