// 运行：本轮与会话累计用量、各来源占比、预算状态、运行轨迹（时间轴 + TraceLogger 完整报告）

import { ExternalLink } from "lucide-react";
import { useStore } from "../../store";
import { ROLE_LABEL, fmtSeconds, k } from "../../format";
import type { Item, Round, UsageData } from "../../types";

const SOURCE_COLOR: Record<string, string> = {
  "forge-coder": "var(--accent)", explorer: "var(--c-seen)", tester: "var(--c-map)", reviewer: "var(--c-pref)", utility: "var(--sub)",
};

function Metrics({ usage }: { usage: UsageData }) {
  return (
    <div className="metrics">
      <div className="metric"><div className="k">tokens</div><div className="v">{k(usage.total_tokens)}</div></div>
      <div className="metric"><div className="k">模型调用</div><div className="v">{usage.model_calls}</div></div>
      <div className="metric"><div className="k">耗时</div><div className="v">{fmtSeconds(usage.elapsed_s)}</div></div>
    </div>
  );
}

/** 各来源（主 Agent / 子 Agent / 辅助 Agent）自身 token 的占比 */
function Shares({ usage }: { usage: UsageData }) {
  const shares = Object.entries(usage.by_agent).sort((a, b) => b[1].tokens - a[1].tokens);
  const total = shares.reduce((n, [, a]) => n + a.tokens, 0) || 1;
  if (!shares.length) return <div className="faint" style={{ fontSize: 12.5 }}>暂无数据</div>;
  return (
    <>
      {shares.map(([name, a]) => (
        <div key={name} className="share" title={`${a.tokens} tokens · ${a.model_calls} 次调用 · 运行 ${a.runs} 次`}>
          <span>{ROLE_LABEL[name] ?? name}</span>
          <div className="track"><div className="fill" style={{ width: `${(a.tokens / total) * 100}%`, background: SOURCE_COLOR[name] ?? "var(--faint)" }} /></div>
          <b>{Math.round((a.tokens / total) * 100)}%</b>
        </div>
      ))}
    </>
  );
}

interface Span { label: string; start: number; end: number; color: string; sub: boolean }

/** 由事件时间戳生成时间轴：工具调用、子 Agent 任务及其内部步骤、审批等待 */
function spans(round: Round): Span[] {
  const out: Span[] = [];
  const t0 = round.startTs ?? 0;
  const walk = (items: Item[], sub: boolean) => {
    for (const item of items) {
      if (item.kind === "tool" && item.ts) {
        out.push({ label: `${item.name} ${item.args?.path ?? item.args?.command ?? ""}`, start: item.ts - t0, end: (item.endTs ?? Date.now() / 1000) - t0, color: "var(--c-result)", sub });
      } else if (item.kind === "sub" && item.ts) {
        out.push({ label: `Task ${item.role}`, start: item.ts - t0, end: (item.endTs ?? Date.now() / 1000) - t0, color: "var(--c-map)", sub });
        walk(item.items, true);
      } else if (item.kind === "approval" && item.ts && !item.auto) {
        out.push({ label: `审批 ${item.command}`, start: item.ts - t0, end: (item.endTs ?? item.ts) - t0, color: "var(--warn)", sub });
      }
    }
  };
  walk(round.items, false);
  return out;
}

export default function RunPane() {
  const { app, timeline, details } = useStore();
  const last = app?.usage.last;
  const session = app?.usage.session;
  const limit = app?.budget.limit;
  const live = [...timeline].reverse().find((e): e is Round => e.kind === "round" && !e.restored);
  const rows = live ? spans(live) : [];
  const total = Math.max(1, ...rows.map((r) => r.end), (live?.endTs ?? 0) - (live?.startTs ?? 0));
  const traceIndex = details ? details.rounds.map((r) => !!r.trace).lastIndexOf(true) : -1;

  return (
    <>
      <div className="section-title">本轮</div>
      {last ? <Metrics usage={last} /> : <div className="empty-hint">完成一轮对话后显示用量。</div>}
      {last && (
        <>
          <div className="section-title">本轮来源占比<span className="r">各 Agent 自身的 token</span></div>
          <Shares usage={last} />
        </>
      )}
      <div className="section-title">预算<span className="r">.env 中 FORGE_ROUND_TOKEN_LIMIT</span></div>
      {limit ? (
        <>
          <div className="track" style={{ height: 10 }}>
            <div className="fill" style={{ width: `${Math.min(100, ((last?.total_tokens ?? 0) / limit) * 100)}%`, background: (last?.total_tokens ?? 0) > limit * 0.8 ? "var(--warn)" : "var(--ok)" }} />
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>本轮 {k(last?.total_tokens)} / {k(limit)} tokens{(last?.total_tokens ?? 0) > limit * 0.8 ? " · 接近上限" : " · 正常"}</div>
        </>
      ) : <div style={{ fontSize: 12.5 }}>未设置单轮上限，只统计不限制。</div>}

      <div className="section-title">运行轨迹
        <span className="r">
          {traceIndex >= 0 && <a className="link" href={`/api/trace/${traceIndex}`} target="_blank" rel="noreferrer">TraceLogger 报告 <ExternalLink size={11} /></a>}
        </span>
      </div>
      {rows.length === 0 ? <div className="empty-hint">本次面板中运行的最新一轮会在这里显示工具与子 Agent 的时间轴。</div> : (
        <>
          {rows.map((r, i) => (
            <div key={i} className="wf">
              <span className={`lbl${r.sub ? " sub" : ""}`} title={r.label}>{r.label}</span>
              <div className="lane"><span style={{ left: `${(r.start / total) * 100}%`, width: `${((r.end - r.start) / total) * 100}%`, background: r.color }} /></div>
              <span className="t">{fmtSeconds(r.end - r.start)}</span>
            </div>
          ))}
          <div className="faint" style={{ fontSize: 11.5, marginTop: 4 }}>空白处为模型思考时间；完整的模型输出与工具调用见 TraceLogger 报告。</div>
        </>
      )}
      <div className="section-title">本会话累计<span className="r">从会话开始起，恢复会话后继续累计</span></div>
      {session && session.model_calls > 0 ? (
        <>
          <Metrics usage={session} />
          <div className="section-title">本会话来源占比</div>
          <Shares usage={session} />
          <div className="faint" style={{ fontSize: 11.5, marginTop: 4 }}>总量另含会话摘要、历史压缩等辅助调用，因此可能大于各来源之和。</div>
        </>
      ) : <div className="empty-hint">本会话还没有模型调用。</div>}
    </>
  );
}
