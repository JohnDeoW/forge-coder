// 与后端接口（forge_coder/ui/web/server.py）对应的数据类型

export type Mode = "plan" | "auto" | "manual";

export interface Status {
  repo: string;
  model: string;
  mode: Mode;
  task: string | null;
  branch: string | null;
  rounds: number;
  approvals: number;
  preferences: number;
  project_memory: number;
  session_tokens: number;
}

export interface UsageData {
  total_tokens: number;
  model_calls: number;
  elapsed_s: number;
  by_agent: Record<string, { tokens: number; model_calls: number; runs: number }>;
  shares: Record<string, number>;
}

export interface ContextUsage {
  used: number;
  window: number;
  breakdown: Record<string, number>;
}

export interface AppState {
  status: Status;
  usage: { last: UsageData | null; session: UsageData };
  context: ContextUsage | null;
  budget: { limit: number | null };
  running: boolean;
}

export interface SessionRef {
  task_id: string;
  title: string;
  mode?: Mode;
  rounds?: number;
  last_active?: string | null; // 最后一轮对话完成的时间（ISO），尚无对话时为空
}

export interface EndedSession {
  task_id: string;
  title: string;
  state: "merged" | "discarded"; // 归档时是否合并过
  time: string;
  merges: number;
  summary: string;
}

export interface Project {
  name: string;
  current: boolean;
  active: SessionRef | null;
  resumable: SessionRef[];
  ended: EndedSession[];
  memory: number;
}

export interface Commit {
  commit: string;
  subject: string;
  time: string;
  added: number;
  deleted: number;
}

export interface Packet {
  source: string;
  tokens: number;
  content: string;
}

export interface RoundDetail {
  round: number;
  request: string;
  mode: Mode;
  paradigm: string;
  commit: string | null;
  context: ContextUsage | null;
  usage: UsageData | null;
  packets: Packet[];
  system_prompt: string;
  trace: string | null;
}

export interface Details {
  prompts: Record<string, string>;
  rounds: RoundDetail[];
  window: number;
}

export interface MemoryEntry {
  id: string;
  time: string;
  task: string;
  kind: "merged" | "discarded" | "compact";
  label: string;
  content: string;
}

export interface ApprovalRecord {
  time: string;
  request?: string;
  mode: string;
  by: string;
  tool: string;
  operation: string;
  approved: boolean;
  risk?: string;
  summary?: string;
  decision_reason?: string;
  reason?: string;
  exchange?: { messages: { role: string; content: string }[]; response: string | null };
}

export interface Settings {
  models: Record<string, string>;
  context_window: number;
  compact_threshold: number;
  compact_keep_rounds: number;
  round_token_limit: number | null;
}

export interface Compaction {
  summary: string;
  compressed_messages: number;
  kept_messages: number;
  tokens_before: number;
  tokens_after: number;
}

/** SSE 事件（与 history 接口恢复的事件同格式） */
export interface AgentEvent {
  type: string;
  agent?: string;
  ts?: number;
  restored?: boolean;
  data: Record<string, any>;
}

export type TodoStatus = "pending" | "in_progress" | "completed";
export interface Todo {
  content: string;
  status: TodoStatus;
}

// ---------------------------------------------------------------- 时间线
export interface ToolItem {
  kind: "tool";
  id: string;
  name: string;
  args: Record<string, any> | null;
  result?: string;
  ts?: number;
  endTs?: number;
}
export interface SubItem {
  kind: "sub";
  id: string;
  role: string;
  task: string;
  items: Item[];
  conclusion?: string;
  tokens?: number;
  steps?: number;
  ts?: number;
  endTs?: number;
}
export interface ApprovalItem {
  kind: "approval";
  requestId?: string;
  tool: string;
  command: string;
  reason?: string;
  state: "pending" | "approved" | "denied" | "answered";
  answer?: string;
  auto?: { risk: string; summary: string; reason: string };
  ts?: number;
  endTs?: number;
}
export type Item =
  | ToolItem
  | SubItem
  | ApprovalItem
  | { kind: "thought"; text: string }
  | { kind: "todo"; todos: Todo[] }
  | { kind: "plan"; steps: string[] }
  | { kind: "planStep"; step: number; total: number; description: string }
  | { kind: "error"; text: string };

export interface Round {
  kind: "round";
  key: string;
  user: string;
  mode?: Mode;
  paradigm?: string;
  review?: boolean;
  items: Item[];
  answer: string;
  status: "running" | "completed" | "failed" | string;
  steps?: number;
  tokens?: number;
  startTs?: number;
  endTs?: number;
  restored?: boolean;
  footer?: { usage: UsageData | null; context: ContextUsage | null; commit: string | null; round: number | null };
}
export interface Notice {
  kind: "notice";
  key: string;
  text: string;
}
export type Entry = Round | Notice;
