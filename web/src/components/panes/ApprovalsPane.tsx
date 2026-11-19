// 审批：本任务的审批记录；auto 模式的记录可展开查看发给辅助 Agent 的完整对话

import { useStore } from "../../store";

export default function ApprovalsPane() {
  const records = useStore((s) => s.approvals);
  if (!records.length) return <div className="empty-hint">本任务还没有审批记录。白名单外的命令在 manual 模式由你确认，auto 模式由辅助 Agent 决定。</div>;
  return (
    <>
      <div className="section-title">本任务的审批记录<span className="r">auto 由辅助 Agent 决定 · manual 由你确认</span></div>
      {[...records].reverse().map((r, i) => (
        <details key={i} className="appr">
          <summary>
            <span className={`tag ${r.approved ? "ok" : "danger"}`}>{r.approved ? "允许" : "拒绝"}</span>
            <span className="cmdtxt" title={r.operation}>{r.operation}</span>
            <span className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{r.mode} · {r.by === "utility" ? "辅助 Agent" : r.by}</span>
          </summary>
          <div className="inner">
            <div className="muted">{r.time} · {r.tool}{r.request ? ` · 需求：${r.request}` : ""}</div>
            {r.risk && <div>风险 <span className={`tag ${r.risk === "high" ? "danger" : r.risk === "low" ? "ok" : "warn"}`}>{r.risk}</span> {r.summary}</div>}
            {(r.decision_reason || r.reason) && <div className="muted">理由：{r.decision_reason || r.reason}</div>}
            {r.exchange && (
              <details className="fold">
                <summary>发给辅助 Agent 的完整对话</summary>
                <pre>{r.exchange.messages.map((m) => `[${m.role}]\n${m.content}`).join("\n\n")}{`\n\n[assistant]\n${r.exchange.response ?? "（无回复）"}`}</pre>
              </details>
            )}
          </div>
        </details>
      ))}
    </>
  );
}
