// 全局状态（zustand）：服务端状态、时间线、检查器数据与界面状态；所有接口调用都经过这里

import { create } from "zustand";
import { api, streamEvents } from "./api";
import { applyEvent, closeRestored, markerNotice, newRound } from "./timeline";
import type {
  AppState, ApprovalItem, ApprovalRecord, Commit, Compaction, Details, EndedSession, Entry, MemoryChange, MemoryEntry, Mode, Project, Settings, Todo,
} from "./types";

export type Tab = "changes" | "todo" | "context" | "run" | "approvals";
export type ToastKind = "info" | "success" | "error";
type Modal = null | { kind: "memory"; tab: "project" | "prefs" | "changes"; repo?: string } | { kind: "settings" };

interface Store {
  app: AppState | null;
  projects: Project[];
  timeline: Entry[];
  todos: Todo[];
  busy: boolean; // 一轮对话或修改性操作进行中
  changes: { diff: string; commits: Commit[]; branch: string | null };
  details: Details | null;
  approvals: ApprovalRecord[];
  settings: Settings | null;
  preferences: string[];
  agentPreferences: string[]; // 由 memory 子 Agent 整理的偏好（其余为用户写入）
  memory: Record<string, MemoryEntry[]>;
  memoryChanges: Record<string, MemoryChange[]>;
  // 界面
  tab: Tab;
  leftCollapsed: boolean;
  rightCollapsed: boolean;
  theme: "light" | "dark";
  modal: Modal;
  contextRound: number | null; // 上下文标签当前查看的轮次（round_details 下标）
  ended: { info: EndedSession; timeline: Entry[] } | null; // 正在查看的已归档会话（只读）
  toast: { text: string; kind: ToastKind } | null;
  pending: string | null; // 正在执行的操作（按钮上显示加载状态）
  draft: { text: string; paradigm: string } | null; // 回溯后放回输入框的需求（输入框读取后清空）
  stopping: boolean; // 已请求停止本轮，等待结束

  init(): Promise<void>;
  refresh(): Promise<void>;
  loadChanges(): Promise<void>;
  loadDetails(): Promise<void>;
  loadApprovals(): Promise<void>;
  loadHistory(): Promise<void>;
  send(text: string, paradigm: string): Promise<void>;
  review(): Promise<void>;
  answerApproval(item: ApprovalItem, answer: string): Promise<void>;
  setMode(mode: Mode): Promise<void>;
  setModel(role: string, model: string): Promise<void>;
  act(path: string, body?: unknown, done?: (r: any) => void, opts?: { pending?: string; success?: string }): Promise<boolean>;
  switchProject(name: string): Promise<void>;
  newSession(): Promise<void>;
  resume(taskId: string): Promise<void>;
  viewEnded(s: EndedSession | null): Promise<void>;
  stop(): Promise<void>;
  rewind(round: number, opts?: { retry?: boolean; pending?: string }): Promise<void>;
  undo(): Promise<void>;
  merge(): Promise<void>;
  discard(): Promise<void>;
  archive(): Promise<void>;
  addPreference(text: string): Promise<void>;
  editPreference(index: number, text: string): Promise<void>;
  deletePreference(index: number): Promise<void>;
  loadMemory(repo: string): Promise<void>;
  editMemory(repo: string, index: number, text: string): Promise<void>;
  deleteMemory(repo: string, index: number): Promise<void>;
  loadMemoryChanges(repo: string): Promise<void>;
  undoMemoryChange(repo: string, id: string): Promise<void>;
  setOption(name: "next_suggestions", value: boolean): Promise<void>;
  openContext(round: number | null): void;
  set(patch: Partial<Store>): void;
  notify(text: string, kind?: ToastKind): void;
}

function initialTheme(): "light" | "dark" {
  try {
    const saved = localStorage.getItem("forge-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch { /* 无法访问本地存储时按系统设置 */ }
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export const useStore = create<Store>((set, get) => ({
  app: null,
  projects: [],
  timeline: [],
  todos: [],
  busy: false,
  changes: { diff: "", commits: [], branch: null },
  details: null,
  approvals: [],
  settings: null,
  preferences: [],
  agentPreferences: [],
  memory: {},
  memoryChanges: {},
  tab: "changes",
  leftCollapsed: false,
  rightCollapsed: false,
  theme: initialTheme(),
  modal: null,
  contextRound: null,
  ended: null,
  toast: null,
  pending: null,
  draft: null,
  stopping: false,

  set: (patch) => set(patch),
  notify(text, kind = "info") {
    const toast = { text, kind };
    set({ toast });
    setTimeout(() => get().toast === toast && set({ toast: null }), kind === "error" ? 6000 : 3500);
  },

  async init() {
    const [settings, prefs] = await Promise.all([api<Settings>("/api/settings"), api<PrefState>("/api/preferences")]);
    set({ settings, preferences: prefs.items, agentPreferences: prefs.agent_items });
    await get().refresh();
    await get().loadHistory();
  },

  async refresh() {
    // 偏好也随之刷新：memory 子 Agent 可能在本轮或归档时修改了偏好
    const [app, projects, prefs] = await Promise.all([
      api<AppState>("/api/state"), api<{ projects: Project[] }>("/api/projects"), api<PrefState>("/api/preferences"),
    ]);
    set({ app, projects: projects.projects });
    setPrefs(prefs);
    await Promise.all([get().loadChanges(), get().loadDetails(), get().loadApprovals()]);
  },
  async loadChanges() {
    set({ changes: await api("/api/changes") });
  },
  async loadDetails() {
    set({ details: await api<Details>("/api/details") });
  },
  async loadApprovals() {
    set({ approvals: (await api<{ records: ApprovalRecord[] }>("/api/approvals")).records });
  },
  async loadHistory() {
    const { events } = await api<{ events: any[] }>("/api/history");
    const tl: Entry[] = [];
    let todos: Todo[] = [];
    for (const e of events) todos = applyEvent(tl, e).todos ?? todos;
    closeRestored(tl);
    set({ timeline: tl, todos, ended: null });
  },

  async send(text, paradigm) {
    const mode = get().app?.status.mode;
    const tl = [...get().timeline, newRound(text, { mode, paradigm })];
    set({ timeline: tl, busy: true, ended: null });
    await runStream("/api/chat", { text, paradigm });
  },
  async review() {
    set({ timeline: [...get().timeline, newRound("审查当前任务的全部改动", { review: true })], busy: true });
    await runStream("/api/review", {});
  },

  async answerApproval(item, answer) {
    try {
      await api("/api/approval", "POST", { request_id: item.requestId, answer });
      Object.assign(item, { state: "answered", answer, endTs: Date.now() / 1000 });
    } catch (err) {
      item.state = "answered";
      item.answer = "expired";
      get().notify(`审批已失效：${(err as Error).message}`, "error");
    }
    set({ timeline: [...get().timeline] });
  },

  async setMode(mode) {
    try {
      set({ app: await api<AppState>("/api/mode", "POST", { value: mode }) });
    } catch (err) {
      get().notify((err as Error).message, "error");
    }
  },

  async setModel(role, model) {
    try {
      set({ app: await api<AppState>("/api/model", "POST", { role, value: model }) });
      get().notify(`${role === "main" ? "主 Agent" : role} 已切换为 ${model}，下一轮生效`, "success");
    } catch (err) {
      get().notify((err as Error).message, "error");
    }
  },

  /** 修改性操作：调用后刷新状态；done 处理返回结果；opts.pending 标记进行中的按钮，opts.success 为成功提示。返回是否成功 */
  async act(path, body, done, opts) {
    set({ busy: true, pending: opts?.pending ?? path });
    try {
      const r = await api(path, "POST", body ?? {});
      done?.(r);
      await get().refresh();
      if (opts?.success) get().notify(opts.success, "success");
      return true;
    } catch (err) {
      get().notify((err as Error).message, "error");
      return false;
    } finally {
      set({ busy: false, pending: null });
    }
  },

  async switchProject(name) {
    await get().act("/api/project", { value: name });
    await get().loadHistory();
    set({ todos: [], contextRound: null });
  },
  async newSession() {
    await get().act("/api/sessions/new");
    set({ timeline: [], todos: [], ended: null, contextRound: null });
  },
  async resume(taskId) {
    await get().act("/api/sessions/resume", { value: taskId });
    await get().loadHistory();
    set({ contextRound: null });
  },
  async viewEnded(s) {
    if (!s) return set({ ended: null });
    try {
      const { events } = await api<{ events: any[] }>(`/api/archived/${s.task_id}`);
      const tl: Entry[] = [];
      for (const e of events) applyEvent(tl, e);
      closeRestored(tl);
      set({ ended: { info: s, timeline: tl } });
    } catch (err) {
      get().notify((err as Error).message, "error");
    }
  },

  async stop() {
    set({ stopping: true });
    try {
      await api("/api/stop", "POST");
    } catch (err) {
      set({ stopping: false });
      get().notify((err as Error).message, "error");
    }
  },
  /** 回溯到第 round 轮开始前：代码与对话回退，该轮需求放回输入框；retry 时直接重新发送 */
  async rewind(round, opts = {}) {
    const retry = !!opts.retry;
    let draft: { text: string; paradigm: string } | null = null;
    const ok = await get().act("/api/rewind", { round }, (r) => { draft = { text: r.text, paradigm: r.paradigm }; },
      { pending: opts.pending ?? `rewind-${round}`, success: retry ? undefined : `已回溯到第 ${round} 轮之前，需求已放回输入框` });
    if (!ok || !draft) return;
    await get().loadHistory();
    set({ contextRound: null });
    const d = draft as { text: string; paradigm: string };
    if (retry) await get().send(d.text, d.paradigm);
    else set({ draft: d });
  },

  /** 撤销最近一轮 = 回溯到最近一轮之前（/undo 与"撤销上一轮"按钮共用） */
  async undo() {
    const last = get().details?.rounds.at(-1)?.round;
    if (!last || !get().app?.rewindable.includes(last)) {
      return get().notify("最近一轮不能撤销（已合并、已丢弃或已被历史压缩）", "error");
    }
    if (!confirm("撤销最近一轮？代码与对话回到该轮之前，该轮需求放回输入框。")) return;
    await get().rewind(last, { pending: "undo" });
  },

  async merge() {
    await get().act("/api/accept", {}, (r) => set({ timeline: [...get().timeline, markerNotice("merge", r.commit)] }),
      { pending: "merge", success: "已合并到 main，会话继续" });
  },
  async discard() {
    if (!confirm("丢弃尚未合并的改动？代码会回到最近一次合并（或会话开始）时的状态，会话继续。")) return;
    await get().act("/api/discard", {}, () => set({ timeline: [...get().timeline, markerNotice("discard", "")] }),
      { pending: "discard", success: "已丢弃尚未合并的改动" });
  },
  async archive() {
    const unmerged = !!get().changes.diff.trim();
    const tip = unmerged ? "还有尚未合并的改动，归档会丢弃它们。" : "";
    if (!confirm(`${tip}归档当前会话？memory 子 Agent 会总结整个会话写入项目记忆并整理已有记忆，对话保存为只读存档。`)) return;
    const taskId = get().app?.status.task;
    const ok = await get().act("/api/archive", {}, () => set({ timeline: [], todos: [], contextRound: null }),
      { pending: "archive", success: "会话已归档，摘要已写入项目记忆" });
    if (!ok) return;
    const archived = get().projects.find((p) => p.current)?.ended.find((e) => e.task_id === taskId);
    if (archived) await get().viewEnded(archived); // 直接打开刚归档的会话，之前的对话仍可查看
  },

  async addPreference(text) {
    try {
      setPrefs(await api<PrefState>("/api/preferences", "POST", { text }));
      get().notify("已记住，后续每次请求都会带上", "success");
    } catch (err) {
      get().notify((err as Error).message, "error");
    }
  },
  async editPreference(index, text) {
    try {
      setPrefs(await api<PrefState>(`/api/preferences/${index}`, "PUT", { text }));
      get().notify("已修改，memory 子 Agent 不会再改动这条偏好", "success");
    } catch (err) {
      get().notify((err as Error).message, "error");
    }
  },
  async deletePreference(index) {
    setPrefs(await api<PrefState>(`/api/preferences/${index}`, "DELETE"));
  },
  async loadMemory(repo) {
    const { entries } = await api<{ entries: MemoryEntry[] }>(`/api/memory/${repo}`);
    set({ memory: { ...get().memory, [repo]: entries } });
  },
  async editMemory(repo, index, text) {
    try {
      const { entries } = await api<{ entries: MemoryEntry[] }>(`/api/memory/${repo}/${index}`, "PUT", { text });
      set({ memory: { ...get().memory, [repo]: entries } });
      get().notify("已修改，memory 子 Agent 不会再改动这条记忆", "success");
    } catch (err) {
      get().notify((err as Error).message, "error");
    }
  },
  async deleteMemory(repo, index) {
    const { entries } = await api<{ entries: MemoryEntry[] }>(`/api/memory/${repo}/${index}`, "DELETE");
    set({ memory: { ...get().memory, [repo]: entries } });
    await get().refresh();
  },
  async loadMemoryChanges(repo) {
    const { changes } = await api<{ changes: MemoryChange[] }>(`/api/memory-changes/${repo}`);
    set({ memoryChanges: { ...get().memoryChanges, [repo]: changes } });
  },
  async undoMemoryChange(repo, id) {
    try {
      const r = await api<PrefState & { changes: MemoryChange[]; entries: MemoryEntry[] }>(`/api/memory-changes/${repo}/${id}/undo`, "POST");
      setPrefs(r);
      set({ memoryChanges: { ...get().memoryChanges, [repo]: r.changes }, memory: { ...get().memory, [repo]: r.entries } });
      get().notify("已撤销这次修改", "success");
      await get().refresh();
    } catch (err) {
      get().notify((err as Error).message, "error");
    }
  },
  async setOption(name, value) {
    const options = await api<Settings["options"]>("/api/options", "POST", { [name]: value });
    const settings = get().settings;
    if (settings) set({ settings: { ...settings, options } });
  },

  openContext(round) {
    set({ tab: "context", contextRound: round, rightCollapsed: false });
  },
}));

type PrefState = { items: string[]; agent_items: string[] };
const setPrefs = (r: PrefState) => useStore.setState({ preferences: r.items, agentPreferences: r.agent_items });

let streamSeq = 0;
async function runStream(path: string, body: unknown) {
  const { set, get } = { set: useStore.setState, get: useStore.getState };
  const seq = ++streamSeq;
  const latest = () => seq === streamSeq;
  let pending = false;
  const flush = () => {
    pending = false;
    set({ timeline: [...get().timeline] });
  };
  try {
    await streamEvents(path, body, (e) => {
      const result = applyEvent(get().timeline, e);
      if (result.todos) set({ todos: result.todos });
      if (e.type === "turn_end") {
        const app = get().app;
        set({ app: app ? { ...app, status: e.data.status, rewindable: e.data.rewindable ?? app.rewindable } : app });
        if (latest()) { set({ busy: false, stopping: false }); void get().refresh(); } // 之后的标题与建议不占用对话
      }
      if (e.type === "session_title") void get().refresh();
      if (e.type === "agent_start" && e.agent === "forge-coder") void get().refresh(); // 首条需求会新建任务
      if (!pending) {
        pending = true;
        requestAnimationFrame(flush); // 流式文本块合并到每帧一次渲染
      }
    });
  } catch (err) {
    applyEvent(get().timeline, { type: "error", data: { error: (err as Error).message } });
  } finally {
    flush();
    if (latest()) {
      set({ busy: false, stopping: false });
      await get().refresh();
    }
  }
}

export type { Compaction };
