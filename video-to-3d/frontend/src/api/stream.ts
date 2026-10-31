import { apiUrl } from "./client";
import type { LogEntry, TaskDetail } from "./types";

export interface SnapshotPayload {
  task: TaskDetail;
  logs: LogEntry[];
}

export type StreamState = "connecting" | "open" | "retrying" | "closed";

export interface TaskStreamHandlers {
  onSnapshot?: (snapshot: SnapshotPayload) => void;
  onEvent?: (type: string, data: Record<string, unknown>) => void;
  onState?: (state: StreamState) => void;
}

const env = import.meta.env as Record<string, string | undefined>;

interface ParsedEvent {
  type: string;
  id: string | null;
  data: unknown;
}

function parseBlock(block: string): ParsedEvent | null {
  let type = "message";
  let id: string | null = null;
  const dataLines: string[] = [];
  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue;
    const colon = rawLine.indexOf(":");
    const field = colon === -1 ? rawLine : rawLine.slice(0, colon);
    const value = colon === -1 ? "" : rawLine.slice(colon + 1).replace(/^ /, "");
    if (field === "event") type = value;
    else if (field === "data") dataLines.push(value);
    else if (field === "id") id = value;
  }
  if (!dataLines.length) return id ? { type: "id", id, data: null } : null;
  const raw = dataLines.join("\n");
  let data: unknown = raw;
  try {
    data = JSON.parse(raw);
  } catch {
    /* 保留原始字符串 */
  }
  return { type, id, data };
}

/**
 * SSE：fetch + ReadableStream 手写解析，支持 Last-Event-ID 回放、心跳与自动重连。
 * 断线 2s 后带 lastId 重连；服务端会先回放历史事件，再进入实时推送。
 */
export function subscribeTask(taskId: string, handlers: TaskStreamHandlers): () => void {
  let closed = false;
  let lastId: string | null = null;
  let attempts = 0;
  const controller = new AbortController();
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  const emitState = (state: StreamState) => handlers.onState?.(state);

  const dispatch = (event: ParsedEvent) => {
    if (event.id) lastId = event.id;
    if (event.type === "snapshot") {
      const payload = event.data as SnapshotPayload;
      if (payload?.task) handlers.onSnapshot?.(payload);
      return;
    }
    if (!event.data || typeof event.data !== "object") return;
    const envelope = event.data as { data?: Record<string, unknown>; type?: string };
    const data = (envelope.data ?? event.data) as Record<string, unknown>;
    if (event.id) data.__event_id = event.id;
    handlers.onEvent?.(event.type, data);
  };

  const open = async (): Promise<void> => {
    if (closed) return;
    emitState(attempts === 0 ? "connecting" : "retrying");
    const query = lastId ? `?last_event_id=${encodeURIComponent(lastId)}` : "";
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (env.VITE_API_TOKEN) headers.Authorization = `Bearer ${env.VITE_API_TOKEN}`;

    let response: Response;
    try {
      response = await fetch(apiUrl(`/api/v1/tasks/${taskId}/events${query}`), {
        headers,
        signal: controller.signal,
        cache: "no-store",
      });
    } catch (error) {
      if (!closed) scheduleRetry();
      return;
    }
    if (!response.ok || !response.body) {
      scheduleRetry();
      return;
    }

    attempts = 0;
    emitState("open");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (!closed) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        while (true) {
          const boundary = buffer.indexOf("\n\n");
          if (boundary === -1) break;
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const event = parseBlock(block);
          if (event) dispatch(event);
        }
      }
    } catch {
      /* 连接被中断，进入重连 */
    }
    if (!closed) scheduleRetry();
  };

  const scheduleRetry = () => {
    attempts += 1;
    emitState("retrying");
    retryTimer = setTimeout(() => {
      retryTimer = null;
      void open();
    }, Math.min(1000 * 2 ** (attempts - 1), 10000));
  };

  void open();

  return () => {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    controller.abort();
    emitState("closed");
  };
}
