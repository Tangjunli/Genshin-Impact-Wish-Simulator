import type {
  Artifact,
  PersonPolicy,
  Probe,
  TaskDetail,
  TaskListResponse,
  TaskSummary,
  UploadPayload,
} from "./types";

declare global {
  interface Window {
    __V3D_API_BASE__?: string;
  }
}

const env = import.meta.env as Record<string, string | undefined>;

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export function apiBase(): string {
  if (typeof window !== "undefined" && window.__V3D_API_BASE__) {
    return window.__V3D_API_BASE__.replace(/\/$/, "");
  }
  return (env.VITE_API_BASE ?? "").replace(/\/$/, "");
}

function authHeaders(): Record<string, string> {
  const token = env.VITE_API_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function apiUrl(path: string): string {
  return `${apiBase()}${path.startsWith("/") ? path : `/${path}`}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...authHeaders(),
      ...(init.headers ?? {}),
    },
  });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail ?? payload;
    const message =
      typeof detail === "string"
        ? detail
        : ((detail as { message?: string } | null)?.message ?? `请求失败（${response.status}）`);
    throw new ApiError(response.status, message, detail);
  }
  return payload as T;
}

export const api = {
  health: () => request<{ status: string; engine_mode: string; version: string }>("/health"),
  probe: (force = false) => request<Probe>(`/api/v1/system/probe${force ? "?force=true" : ""}`),
  createCloudTask: (payload: {
    source_name: string;
    source_size: number;
    person_policy: PersonPolicy;
  }) =>
    request<TaskDetail>("/api/v1/tasks", {
      method: "POST",
      body: JSON.stringify({ mode: "cloud", ...payload }),
    }),
  createLocalTask: (payload: { path: string; person_policy: PersonPolicy }) =>
    request<TaskDetail>("/api/v1/tasks/local", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listTasks: (params: { status?: string; mode?: string; limit?: number; offset?: number } = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") query.set(key, String(value));
    });
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request<TaskListResponse>(`/api/v1/tasks${suffix}`);
  },
  getTask: (id: string) => request<TaskDetail>(`/api/v1/tasks/${id}`),
  cancelTask: (id: string) => request<TaskDetail>(`/api/v1/tasks/${id}/cancel`, { method: "POST" }),
  retryTask: (id: string) => request<TaskDetail>(`/api/v1/tasks/${id}/retry`, { method: "POST" }),
  deleteTask: (id: string) =>
    request<{ deleted: boolean }>(`/api/v1/tasks/${id}`, { method: "DELETE" }),
  listArtifacts: (id: string) => request<{ items: Artifact[] }>(`/api/v1/tasks/${id}/artifacts`),
  getLogs: (id: string, after = 0, limit = 300) =>
    request<{ items: { id: number; ts: string; level: string; step?: string; message: string }[] }>(
      `/api/v1/tasks/${id}/logs?after=${after}&limit=${limit}`,
    ),
  initUpload: (id: string, totalBytes: number) =>
    request<UploadPayload>(`/api/v1/tasks/${id}/upload/init`, {
      method: "POST",
      body: JSON.stringify({ total_bytes: totalBytes }),
    }),
  uploadStatus: (id: string) => request<UploadPayload>(`/api/v1/tasks/${id}/upload/status`),
  completeUpload: (id: string, sha256?: string) =>
    request<TaskDetail>(`/api/v1/tasks/${id}/upload/complete`, {
      method: "POST",
      body: JSON.stringify(sha256 ? { sha256 } : {}),
    }),
};

export function artifactUrl(artifact: Artifact): string {
  return apiUrl(artifact.download_url);
}

export function zipUrl(taskId: string): string {
  return apiUrl(`/api/v1/tasks/${taskId}/download.zip`);
}

export function logStreamUrl(taskId: string): string {
  return apiUrl(`/api/v1/tasks/${taskId}/events`);
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${value >= 100 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.ceil(seconds)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return `${minutes} 分 ${rest} 秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

export interface UploadProgressInfo {
  phase: "init" | "uploading" | "verifying" | "queued";
  uploadedBytes: number;
  totalBytes: number;
  percent: number;
  speedBps: number;
  etaSeconds: number | null;
  uploadedChunks: number;
  totalChunks: number;
  currentChunk: number | null;
}

interface UploadOptions {
  onProgress?: (info: UploadProgressInfo) => void;
  signal?: AbortSignal;
  concurrency?: number;
}

async function sha256Of(file: File): Promise<string | undefined> {
  if (file.size > 64 * 1024 * 1024) return undefined;
  if (!globalThis.crypto?.subtle) return undefined;
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function putChunk(
  taskId: string,
  index: number,
  blob: Blob,
  onLoaded: (loaded: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", apiUrl(`/api/v1/tasks/${taskId}/upload/chunks/${index}`));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    const token = env.VITE_API_TOKEN;
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    const cleanup = () => signal?.removeEventListener("abort", abort);
    const abort = () => xhr.abort();

    xhr.upload.onprogress = (event) => onLoaded(Math.min(event.loaded, blob.size));
    xhr.onload = () => {
      cleanup();
      if (xhr.status >= 200 && xhr.status < 300) {
        onLoaded(blob.size);
        resolve();
        return;
      }
      let message = `分片 ${index} 上传失败（${xhr.status}）`;
      try {
        const payload = JSON.parse(xhr.responseText) as { detail?: unknown };
        if (typeof payload.detail === "string") message = payload.detail;
        else if (payload.detail && typeof payload.detail === "object") {
          const detail = payload.detail as { message?: string };
          if (detail.message) message = detail.message;
        }
      } catch {
        /* 保留默认错误信息 */
      }
      reject(new ApiError(xhr.status, message));
    };
    xhr.onerror = () => {
      cleanup();
      reject(new ApiError(0, "网络错误，分片未送达"));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("上传已取消", "AbortError"));
    };

    if (signal?.aborted) {
      reject(new DOMException("上传已取消", "AbortError"));
      return;
    }
    signal?.addEventListener("abort", abort, { once: true });
    xhr.send(blob);
  });
}

/**
 * 分片上传：分片 byte 进度来自 XHR upload 事件，已确认字节来自服务端响应；
 * 两者都来自真实链路，不做动画式假进度。
 */
export async function uploadVideo(
  taskId: string,
  file: File,
  options: UploadOptions = {},
): Promise<TaskDetail> {
  const { onProgress, signal } = options;
  const concurrency = Math.max(1, Math.min(4, options.concurrency ?? 3));
  const totalBytes = file.size;

  const init = await api.initUpload(taskId, totalBytes);
  const chunkSize = init.chunk_size;
  const totalChunks = init.total_chunks;

  const loaded = new Map<number, number>();
  init.uploaded_chunks.forEach((index) => {
    const start = index * chunkSize;
    loaded.set(index, Math.min(chunkSize, totalBytes - start));
  });

  let speed = 0;
  let lastSampleTime = performance.now();
  let lastSampleBytes = 0;
  let currentChunk: number | null = null;

  const report = (phase: UploadProgressInfo["phase"]) => {
    const uploadedBytes = [...loaded.values()].reduce((sum, value) => sum + value, 0);
    const now = performance.now();
    if (now - lastSampleTime >= 400) {
      const deltaBytes = uploadedBytes - lastSampleBytes;
      const deltaSeconds = (now - lastSampleTime) / 1000;
      if (deltaSeconds > 0 && deltaBytes >= 0) {
        const instant = deltaBytes / deltaSeconds;
        speed = speed === 0 ? instant : speed * 0.7 + instant * 0.3;
      }
      lastSampleTime = now;
      lastSampleBytes = uploadedBytes;
    }
    const percent = totalBytes ? uploadedBytes / totalBytes : 0;
    const etaSeconds = speed > 0 ? (totalBytes - uploadedBytes) / speed : null;
    onProgress?.({
      phase,
      uploadedBytes,
      totalBytes,
      percent,
      speedBps: speed,
      etaSeconds,
      uploadedChunks: [...loaded.keys()].filter(
        (index) => loaded.get(index) === Math.min(chunkSize, totalBytes - index * chunkSize),
      ).length,
      totalChunks,
      currentChunk,
    });
  };

  report("uploading");

  const pending = Array.from({ length: totalChunks }, (_, index) => index).filter(
    (index) => !loaded.has(index),
  );

  const worker = async () => {
    while (pending.length) {
      if (signal?.aborted) throw new DOMException("上传已取消", "AbortError");
      const index = pending.shift();
      if (index === undefined) return;
      const start = index * chunkSize;
      const blob = file.slice(start, Math.min(start + chunkSize, totalBytes));
      currentChunk = index;
      loaded.set(index, 0);
      report("uploading");
      await withRetry(() =>
        putChunk(
          taskId,
          index,
          blob,
          (value) => {
            loaded.set(index, value);
            report("uploading");
          },
          signal,
        ),
      );
      currentChunk = null;
      report("uploading");
    }
  };

  await Promise.all(Array.from({ length: concurrency }, () => worker()));

  report("verifying");
  const status = await api.uploadStatus(taskId);
  if (status.received_bytes !== totalBytes) {
    throw new ApiError(409, `服务端仅确认 ${formatBytes(status.received_bytes)}，请重试`);
  }

  const digest = await sha256Of(file);
  const detail = await api.completeUpload(taskId, digest);
  onProgress?.({
    phase: "queued",
    uploadedBytes: totalBytes,
    totalBytes,
    percent: 1,
    speedBps: speed,
    etaSeconds: 0,
    uploadedChunks: totalChunks,
    totalChunks,
    currentChunk: null,
  });
  return detail;
}

async function withRetry<T>(fn: () => Promise<T>, attempts = 3): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, attempt * 600));
    }
  }
  throw lastError;
}

export async function fetchResultList(task: TaskSummary): Promise<Artifact[]> {
  return (await api.listArtifacts(task.id)).items;
}
