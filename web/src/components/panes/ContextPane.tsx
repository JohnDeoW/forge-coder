// 上下文：选中轮次的上下文窗口占用与构成、逐轮变化、历史压缩、该轮注入的参考资料与系统提示词（默认折叠）

import { Layers } from "lucide-react";
import { useStore } from "../../store";
import { contextSegments, k, partColor } from "../../format";
import { DataTable, Legend, RoundBars, StackBar, type Column } from "../charts";
import { ActionButton, Empty } from "../ui";

export default function ContextPane() {
  const { details, app, settings, contextRound, set, act } = useStore();
  const rounds = details?.rounds ?? [];
  const detail = rounds.find((r) => r.round === contextRound) ?? rounds[rounds.length - 1];
  const ctx = detail?.context ?? app?.context;
  const window = details?.window ?? 128000;

  const columns: Column[] = rounds.filter((r) => r.context).map((r) => ({
    key: String(r.round), label: String(r.round), title: `第 ${r.round} 轮 · ${r.request.slice(0, 24)}`,
    segments: contextSegments(r.context!.breakdown),
  }));

  if (!ctx) {
    return (
      <Empty icon={<Layers size={22} />} title="还没有上下文数据">
        完成一轮对话后，这里显示每次请求的上下文窗口占用、各部分构成与注入的参考资料。
      </Empty>
    );
  }
  const segments = contextSegments(ctx.breakdown);
  return (
    <>
      <div className="section-title">
        上下文窗口{detail ? ` · 第 ${detail.round} 轮` : ""}
        <span className="r"><b className="num">{k(ctx.used)}</b> / {k(window)}（{((ctx.used / window) * 100).toFixed(1)}%）</span>
      </div>
      <StackBar segments={segments} scale={window} height={14} />
      <Legend segments={segments} />

      {columns.length > 1 && (
        <>
          <div className="section-title">逐轮变化<span className="r">点击柱子查看该轮</span></div>
          <RoundBars columns={columns} selected={detail ? String(detail.round) : undefined}
            onSelect={(key) => set({ contextRound: Number(key) })} />
          <DataTable columns={columns} />
        </>
      )}

      <div className="section-title">历史压缩
        <span className="r">
          <ActionButton action="compact" className="sm" disabled={!app?.status.task}
            onClick={() => act("/api/compact", { keep_rounds: 0 }, undefined, { pending: "compact", success: "对话历史已压缩，摘要作为本会话后续的上下文" })}>
            立即压缩
          </ActionButton>
        </span>
      </div>
      <p className="hint">
        对话历史超过上下文窗口的 {Math.round((settings?.compact_threshold ?? 0.8) * 100)}% 时自动压缩，保留最近 {settings?.compact_keep_rounds ?? 2} 轮；
        摘要由辅助 Agent 生成，只作为本会话后续请求的上下文，不写入项目记忆。"立即压缩"会把目前的全部对话压缩为摘要。
      </p>

      {detail && (
        <>
          <div className="section-title">第 {detail.round} 轮注入的参考资料<span className="r">默认折叠</span></div>
          {detail.packets.length === 0 && <p className="hint">本轮没有参考资料（PlanSolve 不注入）。</p>}
          {detail.packets.map((p, i) => (
            <details key={i} className="fold">
              <summary><i className="swatch" style={{ background: partColor(p.source) }} />{p.source}<span className="r">{k(p.tokens)} tokens</span></summary>
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
        <p className="hint">流程细节只保存在本次面板服务的内存中，恢复的会话从下一轮开始记录。</p>
      )}
    </>
  );
}
