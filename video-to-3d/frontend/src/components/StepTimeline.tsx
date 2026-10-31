import { STEP_STATUS_LABEL, type Step, type TaskStatus } from "../api/types";

interface Props {
  steps: Step[];
  overall: number;
  status: TaskStatus;
}

function detailText(step: Step): string {
  const detail = step.detail ?? {};
  if (step.status === "skipped") return detail.skipped_reason ?? detail.message ?? "已跳过";
  if (step.status === "failed" && step.error) return step.error;
  if (detail.current != null && detail.total != null) {
    return `${detail.message ?? "处理中"}（${detail.current}/${detail.total}${
      detail.unit ? ` ${detail.unit}` : ""
    }）`;
  }
  return detail.message ?? "";
}

export default function StepTimeline({ steps, overall, status }: Props) {
  const percent = Math.round(Math.max(0, Math.min(1, overall)) * 100);
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>处理流程</h2>
        <span className="muted">
          {status === "completed" ? "全部完成" : `总体进度 ${percent}%`}
        </span>
      </div>
      <div className="overall-bar" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
        <span style={{ width: `${percent}%` }} />
      </div>
      <ol className="timeline">
        {steps.map((step) => {
          const stepPercent = Math.round(Math.max(0, Math.min(1, step.progress)) * 100);
          return (
            <li key={step.key} className={`timeline-item status-${step.status}`}>
              <div className="timeline-dot" aria-hidden="true" />
              <div className="timeline-body">
                <div className="timeline-title">
                  <span>{step.title}</span>
                  <span className={`badge badge-${step.status}`}>{STEP_STATUS_LABEL[step.status]}</span>
                </div>
                {step.status !== "pending" && step.status !== "skipped" && (
                  <div className="mini-bar">
                    <span style={{ width: `${stepPercent}%` }} />
                  </div>
                )}
                <div className="timeline-detail">
                  {detailText(step) || (step.status === "pending" ? "等待上游步骤" : "")}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
