// 全局状态（zustand）：服务端状态、时间线、检查器数据与界面状态；所有接口调用都经过这里

import { create } from "zustand";
import { api, streamEvents } from "./api";
import { applyEvent, newRound } from "./timeline";
import type {
  AppState, ApprovalItem, ApprovalRecord, Commit, Compaction, Details, EndedSession, Entry, MemoryEntry, Mode, Project, Settings, Todo,
} from "./types";

export type Tab = "changes" | "todo" | "context" | "run" | "approvals";
type Modal = null | { kind: "memory"; tab: "project" | "prefs"; repo?: string } | { kind: "settings" };

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
  memory: Record<string, MemoryEntry[]>;
  // 界面
  tab: Tab;
  leftCollapsed: boolean;
  rightCollapsed: boolean;
  theme: "light" | "dark";
  modal: Modal;
  contextRound: number | null; // 上下文标签当前查看的轮次（round_details 下标）
  ended: EndedSession | null; // 正在查看的已结束会话（只读）
  toast: string | null;

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
  act(path: string, body?: unknown, done?: (r: any) => void): Promise<void>;
  switchProject(name: string): Promise<void>;
  newSession(): Promise<void>;
  resume(taskId: string): Promise<void>;
  viewEnded(s: EndedSession | null): void;
  addPreference(text: string): Promise<void>;
  deletePreference(index: number): Promise<void>;
  loadMemory(repo: string): Promise<void>;
  deleteMemory(repo: string, index: number): Promise<void>;
  openContext(round: number | null): void;
  set(patch: Partial<Store>): void;
  notify(text: string): void;
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
  memory: {},
  tab: "changes",
  leftCollapsed: false,
  rightCollapsed: false,
  theme: initialTheme(),
  modal: null,
  contextRound: null,
  ended: null,
  toast: null,

  set: (patch) => set(patch),
  notify(text) {
    set({ toast: text });
    setTimeout(() => get().toast === text && set({ toast: null }), 3500);
  },

  async init() {
    const [settings, prefs] = await Promise.all([api<Settings>("/api/settings"), api<{ items: string[] }>("/api/preferences")]);
    set({ settings, preferences: prefs.items });
    await get().refresh();
    await get().loadHistory();
  },

  async refresh() {
    const [app, projects] = await Promise.all([api<AppState>("/api/state"), api<{ projects: Project[] }>("/api/projects")]);
    set({ app, projects: projects.projects });
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
      item.state = "answered";
      item.answer = answer;
    } catch (err) {
      item.state = "answered";
      item.answer = "expired";
      get().notify(`审批已失效：${(err as Error).message}`);
    }
    set({ timeline: [...get().timeline] });
  },

  async setMode(mode) {
    try {
      set({ app: await api<AppState>("/api/mode", "POST", { value: mode }) });
    } catch (err) {
      get().notify((err as Error).message);
    }
  },

  /** 修改性操作：调用后刷新状态；done 处理返回结果 */
  async act(path, body, done) {
    set({ busy: true });
    try {
      const r = await api(path, "POST", body ?? {});
      done?.(r);
      await get().refresh();
    } catch (err) {
      get().notify((err as Error).message);
    } finally {
      set({ busy: false });
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
  viewEnded(s) {
    set({ ended: s });
  },

  async addPreference(text) {
    try {
      set({ preferences: (await api<{ items: string[] }>("/api/preferences", "POST", { text })).items });
    } catch (err) {
      get().notify((err as Error).message);
    }
  },
  async deletePreference(index) {
    set({ preferences: (await api<{ items: string[] }>(`/api/preferences/${index}`, "DELETE")).items });
  },
  async loadMemory(repo) {
    const { entries } = await api<{ entries: MemoryEntry[] }>(`/api/memory/${repo}`);
    set({ memory: { ...get().memory, [repo]: entries } });
  },
  async deleteMemory(repo, index) {
    const { entries } = await api<{ entries: MemoryEntry[] }>(`/api/memory/${repo}/${index}`, "DELETE");
    set({ memory: { ...get().memory, [repo]: entries } });
    await get().refresh();
  },

  openContext(round) {
    set({ tab: "context", contextRound: round, rightCollapsed: false });
  },
}));

async function runStream(path: string, body: unknown) {
  const { set, get } = { set: useStore.setState, get: useStore.getState };
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
        set({ app: get().app ? { ...get().app!, status: e.data.status } : get().app });
      }
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
    set({ busy: false });
    await get().refresh();
  }
}

export type { Compaction };
