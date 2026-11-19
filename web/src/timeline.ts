// 把 Agent 事件（SSE 实时事件，或由 /api/history 恢复的事件）归并为时间线：轮次 → 步骤 → 子 Agent 步骤。
// 为了流式输出时的性能，直接修改当前轮次对象，调用方再浅拷贝数组触发重新渲染。

import type { AgentEvent, Entry, Item, Round, SubItem, Todo, ToolItem } from "./types";

export const MAIN = "forge-coder";
const HOST_AGENTS = new Set(["system", "utility"]);
let seq = 0;
const key = () => `e${++seq}`;

export function newRound(user: string, extra: Partial<Round> = {}): Round {
  return { kind: "round", key: key(), user, items: [], answer: "", status: "running", startTs: Date.now() / 1000, ...extra };
}

function currentRound(tl: Entry[]): Round {
  for (let i = tl.length - 1; i >= 0; i--) {
    const entry = tl[i];
    if (entry.kind === "round") return entry;
  }
  const round = newRound("");
  tl.push(round);
  return round;
}

function findTool(items: Item[], id: string): ToolItem | SubItem | undefined {
  for (const item of items) {
    if ((item.kind === "tool" || item.kind === "sub") && item.id === id) return item;
    if (item.kind === "sub") {
      const inner = findTool(item.items, id);
      if (inner) return inner;
    }
  }
  return undefined;
}

function openSub(round: Round, role: string): SubItem | undefined {
  for (let i = round.items.length - 1; i >= 0; i--) {
    const item = round.items[i];
    if (item.kind === "sub" && item.role === role && item.endTs === undefined) return item;
  }
  return undefined;
}

/** 处理一个子 Agent / Reviewer 的工具事件，写入 items */
function toolEvent(items: Item[], e: AgentEvent) {
  const d = e.data;
  if (e.type === "tool_call_start") {
    if (d.tool_name === "Thought") items.push({ kind: "thought", text: d.args?.reasoning ?? "" });
    else if (d.tool_name !== "Finish") items.push({ kind: "tool", id: d.tool_call_id, name: d.tool_name, args: d.args, ts: e.ts });
  } else if (e.type === "tool_call_finish") {
    const item = findTool(items, d.tool_call_id);
    if (item && item.kind === "tool") Object.assign(item, { result: d.result, endTs: e.ts });
  }
}

/** 返回值：本事件更新了待办清单时给出最新清单 */
export function applyEvent(tl: Entry[], e: AgentEvent): { todos?: Todo[] } {
  const d = e.data;
  if (e.type === "user") {
    tl.push(newRound(d.text, { restored: e.restored, status: "completed" }));
    return {};
  }
  if (e.type === "compaction_notice") {
    tl.push({ kind: "notice", key: key(), text: "对话历史已压缩为摘要（以下为保留的最近几轮）" });
    return {};
  }
  const round = currentRound(tl);

  if (e.type === "approval_required") {
    round.items.push({ kind: "approval", requestId: d.request_id, tool: d.tool_name, command: d.command, reason: d.reason, state: "pending", ts: e.ts });
    return {};
  }
  if (e.type === "approval_resolved") {
    round.items.push({
      kind: "approval", tool: d.tool_name, command: d.command, state: d.approved ? "approved" : "denied",
      auto: { risk: d.risk, summary: d.summary, reason: d.reason }, ts: e.ts, endTs: e.ts,
    });
    return {};
  }
  if (e.type === "turn_end") {
    round.footer = { usage: d.usage, context: d.context, commit: d.commit, round: d.round };
    if (round.status === "running") round.status = "completed";
    round.endTs = Date.now() / 1000;
    if (d.compaction) {
      const c = d.compaction;
      tl.push({ kind: "notice", key: key(), text: `对话历史已压缩：${c.compressed_messages} 条消息 → 摘要，历史 ${k(c.tokens_before)} → ${k(c.tokens_after)} tokens；摘要已写入项目记忆` });
    }
    return {};
  }
  if (e.type === "error") {
    round.items.push({ kind: "error", text: d.error });
    round.status = "failed";
    return {};
  }

  const agent = e.agent ?? MAIN;
  if (agent === "reviewer") {
    if (e.type === "agent_finish") {
      round.answer = d.result ?? "";
      Object.assign(round, { steps: d.total_steps, tokens: d.total_tokens });
    } else toolEvent(round.items, e);
    return {};
  }
  if (agent !== MAIN && !HOST_AGENTS.has(agent)) {
    // 子 Agent：归入主 Agent 派出的、尚未结束的同角色 Task
    let sub = openSub(round, agent);
    if (!sub) {
      sub = { kind: "sub", id: key(), role: agent, task: "", items: [], ts: e.ts };
      round.items.push(sub);
    }
    if (e.type === "agent_finish") Object.assign(sub, { tokens: d.total_tokens, steps: d.total_steps });
    else toolEvent(sub.items, e);
    return {};
  }

  // 主 Agent
  switch (e.type) {
    case "llm_chunk":
      round.answer += d.chunk ?? "";
      break;
    case "tool_call_start": {
      const name = d.tool_name;
      if (name === "Thought") round.items.push({ kind: "thought", text: d.args?.reasoning ?? "" });
      else if (name === "TodoWrite") {
        const todos: Todo[] = d.args?.todos ?? [];
        round.items.push({ kind: "todo", todos });
        return { todos };
      } else if (name === "Task") {
        round.items.push({ kind: "sub", id: d.tool_call_id, role: d.args?.role ?? "?", task: d.args?.task ?? "", items: [], ts: e.ts });
      } else if (name !== "Finish") {
        round.items.push({ kind: "tool", id: d.tool_call_id, name, args: d.args, ts: e.ts });
      }
      break;
    }
    case "tool_call_finish": {
      const item = findTool(round.items, d.tool_call_id);
      if (item?.kind === "sub") Object.assign(item, { conclusion: d.result, endTs: e.ts ?? item.ts });
      else if (item?.kind === "tool") Object.assign(item, { result: d.result, endTs: e.ts });
      break;
    }
    case "step_finish":
      if (d.phase === "planning") round.items.push({ kind: "plan", steps: d.plan ?? [] });
      break;
    case "step_start":
      if (d.phase === "execution") round.items.push({ kind: "planStep", step: d.step, total: d.total_steps, description: d.description });
      break;
    case "agent_finish":
      round.answer = d.result || round.answer;
      Object.assign(round, { steps: d.total_steps, tokens: d.total_tokens });
      if (d.status && d.status !== "completed") round.status = d.status;
      break;
  }
  return {};
}

export function k(n: number | undefined | null): string {
  if (!n) return "0";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}
