import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, apiBase } from "./api/client";
import UploadPage from "./pages/Upload";
import TaskDetailPage from "./pages/TaskDetail";
import HistoryPage from "./pages/History";

export default function App() {
  const [health, setHealth] = useState<{ ok: boolean; engine_mode?: string }>({ ok: false });

  useEffect(() => {
    let alive = true;
    const check = () => {
      api
        .health()
        .then((data) => alive && setHealth({ ok: data.status === "ok", engine_mode: data.engine_mode }))
        .catch(() => alive && setHealth({ ok: false }));
    };
    check();
    const timer = setInterval(check, 30000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>Video → 3D</span>
        </div>
        <nav className="nav">
          <NavLink to="/" end>
            新建任务
          </NavLink>
          <NavLink to="/history">历史记录</NavLink>
        </nav>
        <div className={`health ${health.ok ? "ok" : "off"}`} title={apiBase() || "同源 / Vite 代理"}>
          <span className="health-dot" />
          {health.ok ? `已连接 · ${health.engine_mode ?? "—"}` : "后端未连接"}
        </div>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/tasks/:id" element={<TaskDetailPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
