// 上下文：按轮查看最后一次模型请求的上下文窗口分布、历史压缩、注入的参考资料与系统提示词（默认折叠）

import { useStore } from "../../store";
import { CONTEXT_PARTS, k, partColor } from "../../format";
import { ContextStack } from "../TopBar";

export default function ContextPane() {
  const { details, app, settings, contextRound, busy, set, act } = useStore();
  const rounds = details?.rounds ?? [];
  const detail = rounds.find((r) => r.round === contextRound) ?? rounds[rounds.length - 1];
  const ctx = detail?.context ?? app?.context;
  const window = details?.window ?? 128000;

  return (
    <>
      {rounds.length > 0 && (
        <div className="picker">
          {rounds.map((r) => (
            <button key={r.round} className={`sm${r === detail ? " primary" : ""}`} title={r.request} onClick={() => set({ contextRound: r.round })}>第 {r.round} 轮</button>
          ))}
          <span className="muted" style={{ fontSize: 12 }}>显示该轮最后一次模型请求</span>
        </div>
      )}
      <div className="section-title">上下文窗口
        {ctx && <span className="r"><b style={{ color: "var(--text)" }}>{k(ctx.used)}</b> / {k(window)}（{((ctx.used / window) * 100).toFixed(1)}%）</span>}
      </div>
      {ctx ? (
        <>
          <ContextStack breakdown={ctx.breakdown} window={window} big />
          <div className="legend">
            {CONTEXT_PARTS.filter(([label]) => ctx.breakdown[label]).map(([label]) => (
              <div key={label}><i style={{ background: partColor(label) }} />{label}<b>{k(ctx.breakdown[label])}</b></div>
            ))}
          </div>
        </>
      ) : <div className="empty-hint">完成一轮对话后显示各部分的 token 分布。</div>}

      <div className="section-title">历史压缩
        <span className="r"><button className="sm" disabled={busy || !app?.status.task} onClick={() => act("/api/compact", { keep_rounds: 0 })}>立即压缩</button></span>
      </div>
      <div style={{ fontSize: 12.5 }}>
        对话历史超过上下文窗口的 {Math.round((settings?.compact_threshold ?? 0.8) * 100)}% 时自动压缩，保留最近 {settings?.compact_keep_rounds ?? 2} 轮；
        摘要由辅助 Agent 生成并写入项目记忆。<span className="muted">"立即压缩"会把目前的全部对话压缩为摘要。</span>
      </div>

      {detail && (
        <>
          <div className="section-title">第 {detail.round} 轮注入的参考资料<span className="r">默认折叠</span></div>
          {detail.packets.length === 0 && <div className="faint" style={{ fontSize: 12.5 }}>本轮没有参考资料（PlanSolve 不注入）</div>}
          {detail.packets.map((p, i) => (
            <details key={i} className="fold">
              <summary><i style={{ width: 9, height: 9, borderRadius: 2, background: partColor(p.source) }} />{p.source}<span className="r">{k(p.tokens)} tokens</span></summary>
              <pre>{p.content}</pre>
            </details>
          ))}
          <details className="fold">
            <summary>本轮主 Agent 系统提示词<span className="r">{detail.mode} 模式{detail.paradigm === "plansolve" ? " · PlanSolve 执行器" : ""}</span></summary>
            <pre>{detail.system_prompt}</pre>
          </details>
        </>
      )}

      <div className="section-title">系统提示词<span className="r">当前模式</span></div>
      {Object.entries(details?.prompts ?? {}).map(([name, text]) => (
        <details key={name} className="fold"><summary>{name}</summary><pre>{text}</pre></details>
      ))}
      {rounds.length === 0 && app?.status.task && (
        <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>流程细节只保存在本次面板服务的内存中，恢复的会话从下一轮开始记录。</div>
      )}
    </>
  );
}
