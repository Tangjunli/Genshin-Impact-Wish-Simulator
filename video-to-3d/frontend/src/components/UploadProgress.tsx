import { formatBytes, formatDuration, type UploadProgressInfo } from "../api/client";

interface Props {
  info: UploadProgressInfo;
  fileName: string;
  paused: boolean;
  error?: string | null;
  onPause: () => void;
  onResume: () => void;
  onCancel: () => void;
}

const PHASE_TEXT: Record<UploadProgressInfo["phase"], string> = {
  init: "初始化上传会话",
  uploading: "上传视频分片",
  verifying: "校验文件完整性",
  queued: "已进入处理队列",
};

export default function UploadProgress({
  info,
  fileName,
  paused,
  error,
  onPause,
  onResume,
  onCancel,
}: Props) {
  const percent = Math.round(info.percent * 1000) / 10;
  return (
    <section className="panel upload-progress">
      <div className="panel-head">
        <h2>{PHASE_TEXT[info.phase]}</h2>
        <span className="muted">{percent.toFixed(1)}%</span>
      </div>
      <div className="progress-track" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
        <span style={{ width: `${Math.min(100, percent)}%` }} />
      </div>
      <div className="metrics">
        <div className="metric">
          <span className="metric-label">已上传 / 总大小</span>
          <span className="metric-value">
            {formatBytes(info.uploadedBytes)} / {formatBytes(info.totalBytes)}
          </span>
        </div>
        <div className="metric">
          <span className="metric-label">当前速度</span>
          <span className="metric-value">{info.speedBps ? `${formatBytes(info.speedBps)}/s` : "—"}</span>
        </div>
        <div className="metric">
          <span className="metric-label">预计剩余</span>
          <span className="metric-value">
            {info.phase === "queued" ? "已上传" : formatDuration(info.etaSeconds)}
          </span>
        </div>
        <div className="metric">
          <span className="metric-label">分片</span>
          <span className="metric-value">
            {info.uploadedChunks}/{info.totalChunks}
            {info.currentChunk != null ? ` · 当前 #${info.currentChunk}` : ""}
          </span>
        </div>
      </div>
      <div className="upload-file muted" title={fileName}>
        {fileName}
      </div>
      {error && <div className="error-box">{error}</div>}
      {info.phase !== "queued" && (
        <div className="row-actions">
          {paused ? (
            <button className="btn primary" onClick={onResume}>
              继续上传
            </button>
          ) : (
            <button className="btn" onClick={onPause} disabled={info.phase === "verifying"}>
              暂停
            </button>
          )}
          <button className="btn danger-ghost" onClick={onCancel}>
            放弃本次上传
          </button>
        </div>
      )}
    </section>
  );
}
