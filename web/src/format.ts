// 显示用的小工具：diff 解析、上下文分布配色、文本截断

export { k } from "./timeline";

/** 上下文分布各部分的显示顺序与颜色（标签与后端 context/window.py 一致） */
export const CONTEXT_PARTS: [string, string][] = [
  ["系统提示词", "--c-system"], ["仓库地图", "--c-map"], ["子 Agent 已读文件", "--c-seen"], ["用户偏好", "--c-pref"],
  ["项目记忆", "--c-mem"], ["对话", "--c-talk"], ["工具调用", "--c-call"], ["工具结果", "--c-result"], ["工具定义", "--c-schema"],
];
export const partColor = (label: string) => `var(${CONTEXT_PARTS.find(([l]) => l === label)?.[1] ?? "--faint"})`;

export const ROLE_TAG: Record<string, string> = { explorer: "accent", tester: "ok", reviewer: "warn" };
export const ROLE_LABEL: Record<string, string> = {
  "forge-coder": "主 Agent", explorer: "explorer", tester: "tester", reviewer: "reviewer", utility: "辅助 Agent",
};

export function short(value: unknown, n = 120): string {
  const text = typeof value === "string" ? value : JSON.stringify(value ?? "");
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > n ? flat.slice(0, n) + "…" : flat;
}

/** 工具参数的一行摘要：优先显示路径、命令、模式等关键参数 */
export function argSummary(name: string, args: Record<string, any> | null): string {
  if (!args) return "";
  const main = args.path ?? args.command ?? args.pattern ?? args.file_path;
  if (name === "Grep" && args.pattern) return `${args.pattern}${args.path ? `  in ${args.path}` : ""}`;
  return main !== undefined ? String(main) : short(args, 100);
}

export interface DiffFile {
  path: string;
  added: number;
  deleted: number;
  lines: [string, string][]; // [类别 a/d/h/c, 行内容]
}

/** 解析 git diff 文本为文件列表 */
export function parseDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  let cur: DiffFile | null = null;
  for (const line of text.split("\n")) {
    if (line.startsWith("diff --git")) {
      const m = line.match(/ b\/(.+)$/);
      cur = { path: m ? m[1] : line, added: 0, deleted: 0, lines: [] };
      files.push(cur);
      continue;
    }
    if (!cur || line.startsWith("index ") || line.startsWith("--- ") || line.startsWith("+++ ")) continue;
    if (line.startsWith("@@")) cur.lines.push(["h", line]);
    else if (line.startsWith("+")) { cur.added++; cur.lines.push(["a", line]); }
    else if (line.startsWith("-")) { cur.deleted++; cur.lines.push(["d", line]); }
    else cur.lines.push(["c", line]);
  }
  return files;
}

/** Edit / MultiEdit / Write 参数生成小段 diff */
export function editDiff(name: string, args: Record<string, any> | null): [string, string][] | null {
  if (!args) return null;
  const pairs: [string, string][] = [];
  const add = (oldS: string, newS: string) => {
    (oldS || "").split("\n").forEach((l) => oldS && pairs.push(["d", "-" + l]));
    (newS || "").split("\n").forEach((l) => newS && pairs.push(["a", "+" + l]));
  };
  if (name === "Edit") add(args.old_string, args.new_string);
  else if (name === "MultiEdit") (args.edits ?? []).forEach((e: any, i: number) => { if (i) pairs.push(["h", "@@"]); add(e.old_string, e.new_string); });
  else if (name === "Write") add("", args.content);
  else return null;
  return pairs.slice(0, 80);
}

export function resultClass(result: string | undefined): string {
  if (result === undefined) return "";
  if (/^❌|错误|失败|denied|拒绝/.test(result)) return "err";
  if (/退出码 [1-9]|failed|⚠️/.test(result)) return "warn";
  return "ok";
}

export const fmtSeconds = (s: number | undefined) => (s === undefined ? "" : s >= 60 ? `${Math.floor(s / 60)}m${Math.round(s % 60)}s` : `${Math.round(s)}s`);
