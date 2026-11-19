// 输入框：与对话列同宽的卡片，内含范式切换与发送按钮；输入 / 时提示命令；Ctrl+Enter 发送，高度随内容增长

import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import { useStore } from "../store";
import { Spinner } from "./ui";

const COMMANDS: [string, string][] = [
  ["/plansolve ", "用 PlanSolve 范式执行这条需求"],
  ["/review", "Reviewer 审查当前改动"],
  ["/compact", "把对话历史压缩为摘要（可跟保留轮数）"],
  ["/undo", "撤销最近一轮改动"],
  ["/accept", "合并到 main（会话继续）"],
  ["/discard", "丢弃尚未合并的改动"],
  ["/archive", "归档会话：总结写入项目记忆"],
  ["/remember ", "记住一条用户偏好"],
  ["/project-memory", "查看项目记忆"],
  ["/diff", "查看当前改动"],
];

export default function Composer() {
  const { busy, app, ended, send, review, act, merge, discard, archive, addPreference, set, notify } = useStore();
  const [text, setText] = useState("");
  const [paradigm, setParadigm] = useState("react");
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { // 高度随内容增长，最多约 10 行
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);
  const hasTask = !!app?.status.task;
  const suggestions = text.startsWith("/") && !text.includes(" ") ? COMMANDS.filter(([c]) => c.trim().startsWith(text.trim())) : [];

  const submit = async () => {
    const value = text.trim();
    if (!value || busy) return;
    const [cmd, ...rest] = value.split(/\s+/);
    const arg = value.slice(cmd.length).trim();
    const needTask = () => { if (!hasTask) { notify("当前没有进行中的会话", "error"); return false; } return true; };
    setText("");
    if (!cmd.startsWith("/")) return send(value, paradigm);
    switch (cmd) {
      case "/plansolve":
        if (!arg) return notify("用法：/plansolve <需求>", "error");
        return send(arg, "plansolve");
      case "/review":
        return needTask() ? review() : undefined;
      case "/compact":
        if (!needTask()) return;
        return act("/api/compact", { keep_rounds: Number(rest[0] ?? 0) || 0 }, (r) => {
          const c = r.compaction;
          useStore.setState({ timeline: [...useStore.getState().timeline, { kind: "notice", key: `c${Date.now()}`,
            text: `对话历史已压缩：${c.compressed_messages} 条消息 → 摘要，历史 ${c.tokens_before} → ${c.tokens_after} tokens；摘要已写入项目记忆` }] });
        }, { pending: "compact" });
      case "/undo":
        if (!needTask() || !confirm("撤销最近一轮改动？该轮及之后的对话也会一并撤销。")) return;
        return act("/api/undo", {}, () => void useStore.getState().loadHistory(), { pending: "undo", success: "已撤销最近一轮改动" });
      case "/accept":
        return needTask() ? merge() : undefined;
      case "/discard":
        return needTask() ? discard() : undefined;
      case "/archive":
        return needTask() ? archive() : undefined;
      case "/remember":
        return addPreference(arg);
      case "/project-memory":
        return set({ modal: { kind: "memory", tab: "project", repo: app?.status.repo } });
      case "/diff":
        return set({ tab: "changes", rightCollapsed: false });
      default:
        notify(`未知命令 ${cmd}，输入 / 查看可用命令`, "error");
    }
  };

  const placeholder = ended
    ? "正在查看已归档的会话（只读），点击上方\"返回当前会话\"继续"
    : busy ? "本轮进行中…" : "描述需求，例如：给库存增加按类别汇总的报表（Ctrl+Enter 发送，/ 查看命令）";
  return (
    <div className="composer">
      <div className="composer-box">
        {suggestions.length > 0 && (
          <div className="slash">
            {suggestions.map(([c, d]) => (
              <div key={c} onClick={() => { setText(c); area.current?.focus(); }}><code>{c.trim()}</code><span>{d}</span></div>
            ))}
          </div>
        )}
        <textarea
          id="composer-input"
          ref={area}
          rows={2}
          value={text}
          disabled={!!ended}
          placeholder={placeholder}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void submit(); }
            if (e.key === "Tab" && suggestions[0]) { e.preventDefault(); setText(suggestions[0][0]); }
          }}
        />
        <div className="composer-bar">
          <div className="seg small" title="ReAct 边想边做；PlanSolve 先规划再逐步执行（框架范式演示，plan 模式下不可用）">
            <button className={paradigm === "react" ? "on" : ""} onClick={() => setParadigm("react")}>ReAct</button>
            <button className={paradigm === "plansolve" ? "on" : ""} onClick={() => setParadigm("plansolve")}>PlanSolve</button>
          </div>
          <span className="hint grow ellipsis">改动只发生在任务 worktree 中，合并前可审查</span>
          <button className="primary send" title="发送（Ctrl+Enter）" disabled={busy || !!ended || !text.trim()} onClick={() => void submit()}>
            {busy ? <Spinner size={15} /> : <ArrowUp size={16} />}
          </button>
        </div>
      </div>
    </div>
  );
}
