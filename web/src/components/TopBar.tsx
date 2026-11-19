// 顶栏：项目与当前会话、工作模式、待确认提示、上下文窗口占用、会话 token、主题与右栏开关

import { Archive, Moon, PanelRight, Sun } from "lucide-react";
import { useStore } from "../store";
import { CONTEXT_PARTS, k, partColor } from "../format";
import type { Mode } from "../types";

const MODES: [Mode, string][] = [
  ["plan", "只读规划：阅读代码、澄清需求、输出方案"],
  ["auto", "辅助 Agent 自动审批白名单外的操作"],
  ["manual", "白名单外的操作由你确认"],
];

export function ContextStack({ breakdown, window, big }: { breakdown: Record<string, number>; window: number; big?: boolean }) {
  return (
    <div className={big ? "stack big" : "stack"} style={big ? undefined : { width: 120 }}>
      {CONTEXT_PARTS.filter(([label]) => breakdown[label]).map(([label]) => (
        <span key={label} title={`${label} ${k(breakdown[label])}`} style={{ width: `${(breakdown[label] / window) * 100}%`, background: partColor(label) }} />
      ))}
    </div>
  );
}

export default function TopBar() {
  const { app, projects, timeline, busy, theme, ended, set, setMode, openContext, archive } = useStore();
  const status = app?.status;
  const project = projects.find((p) => p.current);
  const title = project?.active?.title;
  const pending = timeline.reduce((n, e) => n + (e.kind === "round" ? e.items.filter((i) => i.kind === "approval" && i.state === "pending").length : 0), 0);
  const ctx = app?.context;

  return (
    <header className="topbar">
      <div className="brand"><span className="logo">F</span>ForgeCoder</div>
      <div className="crumb">
        <b>{status?.repo ?? "…"}</b>
        {status?.task ? (
          <>
            <span className="faint">/</span><span className="title">{title}</span>
            <span className="mono faint">{status.branch}</span>
            <span className="tag plain">第 {status.rounds} 轮</span>
            {!ended && (
              <button className="ghost sm" disabled={busy} title="总结整个会话写入项目记忆，对话保存为只读存档" onClick={() => void archive()}>
                <Archive size={13} />归档会话
              </button>
            )}
          </>
        ) : (
          <span className="faint">· 新会话（发送需求后创建任务分支）</span>
        )}
      </div>
      <div className="seg" title={busy ? "本轮进行中，结束后可切换" : "切换后下一轮生效"}>
        {MODES.map(([mode, tip]) => (
          <button key={mode} title={tip} disabled={busy} className={`${mode} ${status?.mode === mode ? "on" : ""}`} onClick={() => setMode(mode)}>
            {mode}
          </button>
        ))}
      </div>
      <div className="spacer" />
      {pending > 0 && (
        <span className="tag warn" style={{ cursor: "pointer" }} title="跳到待确认的操作"
          onClick={() => document.getElementById("pending-approval")?.scrollIntoView({ behavior: "smooth", block: "center" })}>
          ● {pending} 项待确认
        </span>
      )}
      {ctx && (
        <div className="meter" title="上下文窗口：最近一次请求的占用，点击查看分布" onClick={() => openContext(null)}>
          <span>上下文</span>
          <ContextStack breakdown={ctx.breakdown} window={ctx.window} />
          <span>{k(ctx.used)} / {k(ctx.window)}</span>
        </div>
      )}
      <span className="stat" title="本会话累计 token（含子 Agent 与辅助 Agent）">会话 <b>{k(app?.usage.session.total_tokens)}</b> tokens</span>
      <button className="ghost icon" title="切换浅色 / 深色" onClick={() => set({ theme: theme === "dark" ? "light" : "dark" })}>
        {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
      </button>
      <button className="ghost icon" title="收起 / 展开右侧检查器" onClick={() => set({ rightCollapsed: !useStore.getState().rightCollapsed })}>
        <PanelRight size={16} />
      </button>
    </header>
  );
}
