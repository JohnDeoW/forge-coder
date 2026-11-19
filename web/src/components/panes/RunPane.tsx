// 运行：本轮用量与来源构成、逐轮用量、预算状态、运行轨迹（时间轴 + TraceLogger 完整报告）、本会话累计

import { useState } from "react";
import { Activity, ChevronRight, ExternalLink } from "lucide-react";
import { useStore } from "../../store";
import { SOURCES, fmtSeconds, k, sourceSegments } from "../../format";
import type { Item, Round, UsageData } from "../../types";
import { DataTable, Legend, RoundBars, StackBar, type Column } from "../charts";
import { Empty } from "../ui";

function Metrics({ usage }: { usage: UsageData }) {
  return (
    <div className="metrics">
      <div className="metric"><div className="k">tokens</div><div className="v">{k(usage.total_tokens)}</div></div>
      <div className="metric"><div className="k">模型调用</div><div className="v">{usage.model_calls}</div></div>
      <div className="metric"><div className="k">耗时</div><div className="v">{fmtSeconds(usage.elapsed_s)}</div></div>
    </div>
  );
}

function Sources({ usage }: { usage: UsageData }) {
  const segments = sourceSegments(usage.by_agent);
  return (
    <>
      <StackBar segments={segments} />
      <Legend segments={segments} />
    </>
  );
}

/** 时间轴中各 Agent 的颜色：与来源构成图一致，颜色跟随执行者（主 Agent / 各子 Agent） */
const AGENT_COLOR: Record<string, string> = Object.fromEntries(SOURCES.map((src) => [src.key, src.color]));
const AGENT_LABEL: Record<string, string> = Object.fromEntries(SOURCES.map((src) => [src.key, src.label]));

interface Row { key: string; label: string; start: number; end: number; color: string; children?: Row[]; kind: "agent" | "tool" | "wait" }

/** 由事件时间戳生成时间轴树：执行者 → 工具调用；子 Agent 的 Task 作为主 Agent 下的分组，包含它自己的工具调用 */
function buildRows(round: Round): Row[] {
  const t0 = round.startTs ?? 0;
  const now = Date.now() / 1000;
  const end = (round.endTs ?? now) - t0;
  const walk = (items: Item[], agent: string, prefix: string): Row[] => items.flatMap((item, i): Row[] => {
    const key = `${prefix}.${i}`;
    if (item.kind === "tool" && item.ts) {
      return [{ key, kind: "tool", label: `${item.name} ${item.args?.path ?? item.args?.command ?? item.args?.pattern ?? ""}`,
        start: item.ts - t0, end: (item.endTs ?? now) - t0, color: AGENT_COLOR[agent] ?? "var(--faint)" }];
    }
    if (item.kind === "sub" && item.ts) {
      return [{ key, kind: "agent", label: `${item.role} 子 Agent`, start: item.ts - t0, end: (item.endTs ?? now) - t0,
        color: AGENT_COLOR[item.role] ?? "var(--faint)", children: walk(item.items, item.role, key) }];
    }
    if (item.kind === "approval" && item.ts && !item.auto) {
      return [{ key, kind: "wait", label: `等待确认 ${item.command}`, start: item.ts - t0, end: (item.endTs ?? item.ts) - t0, color: "var(--warn)" }];
    }
    return [];
  });
  const owner = round.review ? "reviewer" : "forge-coder";
  return [{ key: "root", kind: "agent", label: AGENT_LABEL[owner] ?? owner, start: 0, end, color: AGENT_COLOR[owner], children: walk(round.items, owner, "r") }];
}

function Timeline({ rows, total, depth, open, toggle }: {
  rows: Row[]; total: number; depth: number; open: Set<string>; toggle: (key: string) => void;
}) {
  return (
    <>
      {rows.map((r) => {
        const expandable = !!r.children?.length;
        const isOpen = open.has(r.key);
        return (
          <div key={r.key}>
            <div className={`wf ${r.kind}${expandable ? " expandable" : ""}`} onClick={() => expandable && toggle(r.key)}>
              <span className="lbl" style={{ paddingLeft: depth * 14 }} title={r.label}>
                {expandable ? <ChevronRight size={12} className={`chev${isOpen ? " open" : ""}`} /> : <span className="chev-space" />}
                {r.kind === "agent" && <i className="swatch" style={{ background: r.color }} />}
                {r.label}
                {expandable && !isOpen && <span className="tiny"> · {r.children!.length} 步</span>}
              </span>
              <div className="lane">
                <span style={{ left: `${(r.start / total) * 100}%`, width: `${((r.end - r.start) / total) * 100}%`, background: r.color, opacity: r.kind === "agent" ? 0.35 : 1 }} />
              </div>
              <span className="t">{fmtSeconds(r.end - r.start)}</span>
            </div>
            {expandable && isOpen && <Timeline rows={r.children!} total={total} depth={depth + 1} open={open} toggle={toggle} />}
          </div>
        );
      })}
    </>
  );
}

export default function RunPane() {
  const { app, timeline, details } = useStore();
  const last = app?.usage.last;
  const session = app?.usage.session;
  const limit = app?.budget.limit ?? undefined;
  const live = [...timeline].reverse().find((e): e is Round => e.kind === "round" && !e.restored);
  const rows = live ? buildRows(live) : [];
  const total = Math.max(1, rows[0]?.end ?? 1);
  const [open, setOpen] = useState<Set<string>>(new Set(["root"])); // 主 Agent 展开，子 Agent 默认折叠
  const toggle = (key: string) => setOpen((prev) => { const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next; });
  const used = new Set<string>();
  let hasWait = false;
  const scan = (list: Row[], agent: string) => list.forEach((r) => {
    if (r.kind === "wait") hasWait = true;
    const who = r.kind === "agent" ? SOURCES.find((src) => src.color === r.color)?.key ?? agent : agent;
    used.add(who);
    if (r.children) scan(r.children, who);
  });
  scan(rows, "forge-coder");
  const traceIndex = details ? details.rounds.map((r) => !!r.trace).lastIndexOf(true) : -1;
  const columns: Column[] = (details?.rounds ?? []).filter((r) => r.usage).map((r) => ({
    key: String(r.round), label: String(r.round), title: `第 ${r.round} 轮 · ${r.request.slice(0, 24)}`,
    segments: sourceSegments(r.usage!.by_agent),
  }));

  if (!last && !session?.model_calls) {
    return <Empty icon={<Activity size={22} />} title="还没有运行数据">完成一轮对话后，这里显示用量、各 Agent 的占比、预算与运行轨迹。</Empty>;
  }
  return (
    <>
      {last && (
        <>
          <div className="section-title">本轮</div>
          <Metrics usage={last} />
          <div className="section-title">本轮来源构成<span className="r">各 Agent 自身的 token</span></div>
          <Sources usage={last} />
        </>
      )}

      {columns.length > 1 && (
        <>
          <div className="section-title">逐轮用量<span className="r">按来源堆叠</span></div>
          <RoundBars columns={columns} limit={limit} />
          <DataTable columns={columns} />
        </>
      )}

      <div className="section-title">预算<span className="r">.env 中 FORGE_ROUND_TOKEN_LIMIT</span></div>
      {limit ? (
        <>
          <div className="track"><div className="fill" style={{ width: `${Math.min(100, ((last?.total_tokens ?? 0) / limit) * 100)}%`, background: (last?.total_tokens ?? 0) > limit * 0.8 ? "var(--warn)" : "var(--ok)" }} /></div>
          <p className="hint">本轮 {k(last?.total_tokens)} / {k(limit)} tokens{(last?.total_tokens ?? 0) > limit * 0.8 ? " · 接近上限" : " · 正常"}</p>
        </>
      ) : <p className="hint">未设置单轮上限，只统计不限制。</p>}

      <div className="section-title">运行轨迹
        <span className="r">
          {traceIndex >= 0 && <a className="link" href={`/api/trace/${traceIndex}`} target="_blank" rel="noreferrer">TraceLogger 报告 <ExternalLink size={11} /></a>}
        </span>
      </div>
      {!live ? <p className="hint">本次面板中运行的最新一轮会在这里显示主 Agent 与子 Agent 的时间轴。</p> : (
        <>
          <div className="wf-legend">
            {SOURCES.filter((src) => used.has(src.key)).map((src) => <span key={src.key}><i style={{ background: src.color }} />{src.label}</span>)}
            {hasWait && <span><i style={{ background: "var(--warn)" }} />等待确认</span>}
          </div>
          <Timeline rows={rows} total={total} depth={0} open={open} toggle={toggle} />
          <p className="hint">浅色长条为执行者的整体时间，深色为各次工具调用；空白处为模型思考时间。子 Agent 默认折叠，点击展开；完整的模型输出见 TraceLogger 报告。</p>
        </>
      )}

      <div className="section-title">本会话累计<span className="r">从会话开始起，恢复后继续累计</span></div>
      {session && session.model_calls > 0 ? (
        <>
          <Metrics usage={session} />
          <div className="section-title">本会话来源构成</div>
          <Sources usage={session} />
          <p className="hint">总量另含会话摘要、历史压缩等辅助调用，因此可能大于各来源之和。</p>
        </>
      ) : <p className="hint">本会话还没有模型调用。</p>}
    </>
  );
}
