export type TaskMode = "local" | "cloud";
export type PersonPolicy = "auto" | "motion_only" | "appearance" | "none";

export type TaskStatus =
  | "created"
  | "uploading"
  | "queued"
  | "running"
  | "packaging"
  | "completed"
  | "failed"
  | "canceled"
  | "interrupted";

export type StepStatus =
  | "pending"
  | "running"
  | "completed"
  | "skipped"
  | "failed"
  | "canceled";

export interface StepDetail {
  current?: number | null;
  total?: number | null;
  unit?: string | null;
  message?: string;
  skipped_reason?: string;
}

export interface Step {
  key: string;
  title: string;
  weight: number;
  status: StepStatus;
  progress: number;
  detail: StepDetail;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface TaskError {
  code: string;
  message: string;
  retryable: boolean;
  suggestion?: string | null;
}

export interface TaskSummary {
  id: string;
  mode: TaskMode;
  status: TaskStatus;
  source_name: string;
  source_size: number;
  person_policy: PersonPolicy;
  person_detected: boolean | null;
  current_step: string | null;
  overall_progress: number;
  error: TaskError | null;
  created_at: string;
  updated_at: string;
}

export interface TaskDetail extends TaskSummary {
  started_at: string | null;
  finished_at: string | null;
  retry_count: number;
  source: { name: string; size: number; sha256: string | null; path: string | null };
  steps: Step[];
  outputs: Record<string, string | null>;
}

export interface Artifact {
  id: number;
  kind: "scene" | "person" | "motion" | "preview" | "package" | "misc";
  filename: string;
  size: number;
  mime: string | null;
  sha256: string | null;
  download_url: string;
}

export interface LogEntry {
  id?: number;
  ts: string;
  level: string;
  step?: string | null;
  message: string;
}

export interface ProbeEngine {
  name: string;
  capability: string;
  location: string;
  available: boolean;
  local_only?: boolean;
  license?: string;
  note?: string;
}

export interface Probe {
  device: {
    platform: string;
    python: string;
    cpu_count: number | null;
    ram_gb: number | null;
    gpu: { name: string; vram_gb: number | null } | null;
    disk_free_gb: number | null;
  };
  tools: Record<string, boolean>;
  engines: ProbeEngine[];
  recommendation: { mode: TaskMode; blockers: string[]; reason: string };
  engine_mode: string;
  probed_at: string;
}

export interface UploadPayload {
  task_id: string;
  chunk_size: number;
  total_bytes: number;
  total_chunks: number;
  uploaded_chunks: number[];
  received_bytes: number;
  percent: number;
}

export interface TaskListResponse {
  items: TaskSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface StreamEnvelope {
  id?: number;
  type: string;
  task_id?: string;
  ts?: string;
  data?: Record<string, unknown>;
}

export const STEP_STATUS_LABEL: Record<StepStatus, string> = {
  pending: "等待中",
  running: "进行中",
  completed: "已完成",
  skipped: "已跳过",
  failed: "失败",
  canceled: "已取消",
};

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  created: "已创建",
  uploading: "上传中",
  queued: "排队中",
  running: "处理中",
  packaging: "打包中",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
  interrupted: "已中断",
};

export const PERSON_POLICY_LABEL: Record<PersonPolicy, string> = {
  auto: "自动判断人物提取",
  motion_only: "仅提取动作数据",
  appearance: "人物外观 + 动作",
  none: "不提取人物",
};
