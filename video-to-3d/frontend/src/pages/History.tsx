import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, formatBytes, zipUrl } from "../api/client";
import {
  TASK_STATUS_LABEL,
  type TaskStatus,
  type TaskSummary,
  type TaskMode,
} from "../api/types";

type Group = "all" | "active" | "done" | "failed";

const ACTIVE: TaskStatus[] = ["created", "uploading", "queued", "running", "packaging"];
const FAILED: TaskStatus[] = ["failed", "interrupted", "canceled"];

const STEP_TITLE: Record<string, string> = {
  prepare: "校验视频与媒体信息",
  extract_frames: "抽帧",
  detect_person: "人物检测",
  camera_pose: "相机位姿估计",
  scene_reconstruct: "场景重建",
  person_motion: "人物动作提取",
  person_appearance: "人物外观重建",
  texture_optimize: "纹理优化",
  export: "模型导出",
  package: "打包",
};

const GROUPS: { key: Group; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "done", label: "已完成" },
  { key: "failed", label: "失败/中断" },
];

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export default function HistoryPage() {
  const [items, setItems] = useState<TaskSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [group, setGroup] = useState<Group>("all");
  const [mode, setMode] = useState<TaskMode | "all">("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = async (nextMode = mode, append = false) => {
    setLoading(true);
    try {
      const page = await api.listTasks({
        mode: nextMode === "all" ? undefined : nextMode,
        limit: 50,
        offset: append ? items.length : 0,
      });
      setItems((previous) => {
        if (!append) return page.items;
        const known = new Set(previous.map((item) => item.id));
        return [...previous, ...page.items.filter((item) => !known.has(item.id))];
      });
      setTotal(page.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load(mode, false);
  }, [mode]);

  const hasActive = useMemo(() => items.some((item) => ACTIVE.includes(item.status)), [items]);

  useEffect(() => {
    if (!hasActive) return;
    const timer = setInterval(() => {
      void api
        .listTasks({ mode: mode === "all" ? undefined : mode, limit: 50 })
        .then((page) => {
          setItems((previous) => {
            const extra = previous.slice(50);
            return [...page.items, ...extra];
          });
          setTotal(page.total);
        })
        .catch(() => undefined);
    }, 5000);
    return () => clearInterval(timer);
  }, [hasActive, mode]);

  const filtered = items.filter((item) => {
    if (group === "all") return true;
    if (group === "active") return ACTIVE.includes(item.status);
    if (group === "done") return item.status === "completed";
    return FAILED.includes(item.status);
  });

  const act = async (action: () => Promise<unknown>, message?: string) => {
    setNotice(null);
    setError(null);
    try {
      await action();
      if (message) setNotice(message);
      await load(mode, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  };

  const remove = (task: TaskSummary) => {
    if (!window.confirm(`删除任务 ${task.source_name || task.id}？工作区文件也会被清理。`)) return;
    void act(() => api.deleteTask(task.id), "任务已删除");
  };

  return (
    <div className="page">
      <div className="page-heading">
        <h1>历史记录</h1>
        <p className="muted">所有任务保存在服务端 / 本地数据库中，刷新、退出或重新登录后仍可恢复进度与产物。</p>
      </div>

      <section className="panel">
        <div className="filters">
          <div className="tab-group">
            {GROUPS.map((item) => (
              <button
                key={item.key}
                className={group === item.key ? "active" : ""}
                onClick={() => setGroup(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <select
            className="select"
            value={mode}
            onChange={(event) => setMode(event.target.value as TaskMode | "all")}
          >
            <option value="all">全部模式</option>
            <option value="local">本地</option>
            <option value="cloud">云端</option>
          </select>
          <button className="btn tiny" onClick={() => void load(mode, false)} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </button>
        </div>

        {error && <div className="error-box">{error}</div>}
        {notice && <div className="notice-box">{notice}</div>}

        {filtered.length === 0 && !loading && (
          <div className="empty">
            <div>还没有任务</div>
            <Link className="btn primary" to="/">
              新建任务
            </Link>
          </div>
        )}

        <div className="task-list">
          {filtered.map((task) => {
            const percent = Math.round(Math.max(0, Math.min(1, task.overall_progress)) * 100);
            return (
              <article key={task.id} className="task-card">
                <div className="task-card-main">
                  <div className="task-card-title">
                    <Link to={`/tasks/${task.id}`}>{task.source_name || task.id}</Link>
                    <span className={`status-pill status-${task.status}`}>
                      {TASK_STATUS_LABEL[task.status]}
                    </span>
                  </div>
                  <div className="muted task-card-meta">
                    {task.mode === "local" ? "本地" : "云端"} · {formatBytes(task.source_size)} ·{" "}
                    {formatTime(task.created_at)}
                    {task.current_step && ACTIVE.includes(task.status)
                      ? ` · 当前：${STEP_TITLE[task.current_step] ?? task.current_step}`
                      : ""}
                  </div>
                  {ACTIVE.includes(task.status) && (
                    <div className="progress-track slim">
                      <span style={{ width: `${percent}%` }} />
                    </div>
                  )}
                  {task.error && <div className="task-error">{task.error.message}</div>}
                </div>
                <div className="task-card-actions">
                  <Link className="btn tiny" to={`/tasks/${task.id}`}>
                    查看
                  </Link>
                  {task.status === "completed" && (
                    <a className="btn tiny primary" href={zipUrl(task.id)} download>
                      下载
                    </a>
                  )}
                  {FAILED.includes(task.status) && (
                    <button className="btn tiny" onClick={() => act(() => api.retryTask(task.id), "已重新入队")}>
                      重试
                    </button>
                  )}
                  {ACTIVE.includes(task.status) && (
                    <button
                      className="btn tiny danger-ghost"
                      onClick={() => act(() => api.cancelTask(task.id), "已取消")}
                    >
                      取消
                    </button>
                  )}
                  {!ACTIVE.includes(task.status) && (
                    <button className="btn tiny danger-ghost" onClick={() => remove(task)}>
                      删除
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>

        {items.length > 0 && items.length < total && (
          <div className="row-actions center">
            <button className="btn" onClick={() => void load(mode, true)} disabled={loading}>
              加载更多（{items.length}/{total}）
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
