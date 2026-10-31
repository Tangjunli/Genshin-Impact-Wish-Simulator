import { useEffect, useRef, useState } from "react";
import type { LogEntry } from "../api/types";
import type { StreamState } from "../api/stream";

interface Props {
  logs: LogEntry[];
  streamState?: StreamState;
}

const STATE_TEXT: Record<StreamState, string> = {
  connecting: "连接中",
  open: "实时",
  retrying: "重连中",
  closed: "已断开",
};

function clock(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

export default function LogPanel({ logs, streamState }: Props) {
  const [autoScroll, setAutoScroll] = useState(true);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!autoScroll) return;
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [logs, autoScroll]);

  return (
    <section className="panel log-panel">
      <div className="panel-head">
        <h2>生成日志</h2>
        <div className="log-tools">
          {streamState && (
            <span className={`stream-dot stream-${streamState}`}>{STATE_TEXT[streamState]}</span>
          )}
          <label className="checkbox">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(event) => setAutoScroll(event.target.checked)}
            />
            自动滚动
          </label>
        </div>
      </div>
      <div className="log-box" ref={boxRef}>
        {logs.length === 0 && <div className="muted">暂无日志</div>}
        {logs.map((entry, index) => (
          <div key={entry.id ?? `${entry.ts}-${index}`} className={`log-line level-${entry.level}`}>
            <span className="log-time">{clock(entry.ts)}</span>
            {entry.step && <span className="log-step">{entry.step}</span>}
            <span className="log-message">{entry.message}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
