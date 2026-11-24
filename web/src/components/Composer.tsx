// 输入框：与对话列同宽的卡片，内含范式切换与发送按钮（运行中变为停止按钮）；输入 / 时提示命令；
// 输入 @ 时按路径补全文件（被引用文件的内容作为本轮参考资料注入）；粘贴的长文本（日志、报错）自动转为附件；
// Ctrl+Enter 发送，高度随内容增长；回溯后被撤回的需求放回这里供编辑（附件还原为附件）

import { useEffect, useRef, useState } from "react";
import { ArrowUp, Paperclip, Square, X } from "lucide-react";
import { useStore } from "../store";
import { api } from "../api";
import { lineCount, parseAttachments, withAttachments, type Attachment } from "../format";
import { Spinner } from "./ui";
import { MainModelMenu } from "./ModelPicker";

const COMMANDS: [string, string][] = [
  ["/plansolve ", "用 PlanSolve 范式执行这条需求"],
  ["/review", "Reviewer 审查当前改动"],
  ["/compact", "把对话历史压缩为摘要（可跟保留轮数）"],
  ["/undo", "撤销最近一轮（需求放回输入框）；任意一轮可在消息上回溯"],
  ["/accept", "合并到 main（会话继续）"],
  ["/discard", "丢弃尚未合并的改动"],
  ["/archive", "归档会话：总结写入项目记忆"],
  ["/remember ", "记住一条用户偏好"],
  ["/project-memory", "查看项目记忆"],
  ["/diff", "查看当前改动"],
];

// 粘贴的文本超过任一阈值时转为附件
const ATTACH_CHARS = 1200;
const ATTACH_LINES = 20;
// 光标前正在输入的 @路径
const REF_TOKEN = /(^|[\s(（，,:：])@([\w\-./\\]*)$/;

export default function Composer() {
  const { busy, app, ended, send, review, merge, discard, archive, addPreference, set, notify, act, undo, stop, stopping, draft } = useStore();
  const [text, setText] = useState("");
  const [paradigm, setParadigm] = useState("react");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [preview, setPreview] = useState<number | null>(null);
  const [caret, setCaret] = useState(0);
  const [files, setFiles] = useState<string[]>([]);
  const [active, setActive] = useState(0);
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { // 高度随内容增长，最多约 10 行
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);
  useEffect(() => { // 回溯后放回的需求：附件还原为附件
    if (!draft) return;
    const parsed = parseAttachments(draft.text);
    setText(parsed.text);
    setAttachments(parsed.attachments);
    setParadigm(draft.paradigm === "plansolve" ? "plansolve" : "react");
    set({ draft: null });
    setTimeout(() => area.current?.focus(), 0);
  }, [draft, set]);

  // @ 引用补全：光标前是 @路径 时按路径查找文件（当前任务 worktree，没有任务时为 main）
  const ref = REF_TOKEN.exec(text.slice(0, caret));
  const refQuery = ref ? ref[2] : null;
  useEffect(() => {
    if (refQuery === null) return setFiles([]);
    const timer = setTimeout(() => {
      api<{ files: string[] }>(`/api/files?q=${encodeURIComponent(refQuery)}`)
        .then((r) => { setFiles(r.files); setActive(0); }).catch(() => setFiles([]));
    }, 120);
    return () => clearTimeout(timer);
  }, [refQuery, app?.status.task, app?.status.repo]);
  const pickFile = (path: string) => {
    const before = text.slice(0, caret).replace(/@[\w\-./\\]*$/, `@${path} `);
    const next = before + text.slice(caret).replace(/^\S*/, "");
    setText(next);
    setFiles([]);
    setTimeout(() => { area.current?.focus(); area.current?.setSelectionRange(before.length, before.length); setCaret(before.length); }, 0);
  };

  const hasTask = !!app?.status.task;
  const running = useStore((st) => st.busy && st.timeline.some((e) => e.kind === "round" && e.status === "running"));
  const suggestions = text.startsWith("/") && !text.includes(" ") ? COMMANDS.filter(([c]) => c.trim().startsWith(text.trim())) : [];
  const showFiles = refQuery !== null && files.length > 0;

  const submit = async () => {
    const value = text.trim();
    if ((!value && !attachments.length) || busy) return;
    const [cmd, ...rest] = value.split(/\s+/);
    const arg = value.slice(cmd.length).trim();
    const needTask = () => { if (!hasTask) { notify("当前没有进行中的会话", "error"); return false; } return true; };
    const clear = () => { setText(""); setAttachments([]); setPreview(null); };
    if (!cmd.startsWith("/")) { clear(); return send(withAttachments(value, attachments), paradigm); }
    if (cmd === "/plansolve") {
      if (!arg) return notify("用法：/plansolve <需求>", "error");
      clear();
      return send(withAttachments(arg, attachments), "plansolve");
    }
    setText(""); // 其他命令不使用附件，附件保留在输入框
    switch (cmd) {
      case "/review":
        return needTask() ? review() : undefined;
      case "/compact":
        if (!needTask()) return;
        return act("/api/compact", { keep_rounds: Number(rest[0] ?? 0) || 0 }, (r) => {
          const c = r.compaction;
          useStore.setState({ timeline: [...useStore.getState().timeline, { kind: "notice", key: `c${Date.now()}`,
            text: `对话历史已压缩：${c.compressed_messages} 条消息 → 摘要，历史 ${c.tokens_before} → ${c.tokens_after} tokens；摘要作为本会话后续的上下文` }] });
        }, { pending: "compact" });
      case "/undo":
        return needTask() ? undo() : undefined;
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

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = e.clipboardData.getData("text");
    if (pasted.length < ATTACH_CHARS && lineCount(pasted) < ATTACH_LINES) return;
    e.preventDefault();
    const n = attachments.length + 1;
    setAttachments([...attachments, { name: `粘贴的文本 ${n}`, text: pasted }]);
    notify(`粘贴的内容较长（${lineCount(pasted)} 行），已作为附件，发送时附在需求之后`, "info");
  };

  const placeholder = ended
    ? "正在查看已归档的会话（只读），点击上方\"返回当前会话\"继续"
    : busy ? "本轮进行中…" : "描述需求，@ 引用文件，粘贴长日志自动转为附件（Ctrl+Enter 发送，/ 查看命令）";
  return (
    <div className="composer">
      <div className="composer-box">
        {suggestions.length > 0 && !showFiles && (
          <div className="slash">
            {suggestions.map(([c, d]) => (
              <div key={c} onClick={() => { setText(c); area.current?.focus(); }}><code>{c.trim()}</code><span>{d}</span></div>
            ))}
          </div>
        )}
        {showFiles && (
          <div className="slash refs" title="引用的文件内容会作为本轮参考资料注入">
            {files.map((f, i) => {
              const slash = f.lastIndexOf("/");
              return (
                <div key={f} className={i === active ? "on" : ""} onMouseDown={(e) => { e.preventDefault(); pickFile(f); }}>
                  <code>{f.slice(slash + 1)}</code><span className="dir">{slash > 0 ? f.slice(0, slash) : ""}</span>
                </div>
              );
            })}
          </div>
        )}
        {attachments.length > 0 && (
          <div className="attach-row">
            {attachments.map((a, i) => (
              <span key={i} className="attach-chip" title="点击预览" onClick={() => setPreview(preview === i ? null : i)}>
                <Paperclip size={12} />{a.name}<span className="faint">{lineCount(a.text)} 行</span>
                <button className="ghost xs" title="移除附件" onClick={(e) => {
                  e.stopPropagation();
                  setAttachments(attachments.filter((_, j) => j !== i));
                  setPreview(null);
                }}><X size={12} /></button>
              </span>
            ))}
          </div>
        )}
        {preview !== null && attachments[preview] && <pre className="attach-preview">{attachments[preview].text}</pre>}
        <textarea
          id="composer-input"
          ref={area}
          rows={2}
          value={text}
          disabled={!!ended}
          placeholder={placeholder}
          onChange={(e) => { setText(e.target.value); setCaret(e.target.selectionStart); }}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
          onPaste={onPaste}
          onKeyDown={(e) => {
            if (showFiles) {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setActive((active + (e.key === "ArrowDown" ? 1 : files.length - 1)) % files.length);
                return;
              }
              if ((e.key === "Enter" && !e.ctrlKey && !e.metaKey) || e.key === "Tab") { e.preventDefault(); pickFile(files[active]); return; }
              if (e.key === "Escape") { e.preventDefault(); setFiles([]); return; }
            }
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void submit(); }
            if (e.key === "Tab" && suggestions[0]) { e.preventDefault(); setText(suggestions[0][0]); }
          }}
        />
        <div className="composer-bar">
          <div className="seg small" title="ReAct 边想边做；PlanSolve 先规划再逐步执行（框架范式演示，plan 模式下不可用；@ 引用只对 ReAct 生效）">
            <button className={paradigm === "react" ? "on" : ""} onClick={() => setParadigm("react")}>ReAct</button>
            <button className={paradigm === "plansolve" ? "on" : ""} onClick={() => setParadigm("plansolve")}>PlanSolve</button>
          </div>
          <span className="hint grow ellipsis">改动只发生在任务 worktree 中，合并前可审查</span>
          <MainModelMenu />
          {running ? (
            <button className="send stop" title="停止本轮：已产生的改动照常提交" disabled={stopping} onClick={() => void stop()}>
              {stopping ? <Spinner size={15} /> : <Square size={13} fill="currentColor" />}
            </button>
          ) : (
            <button className="primary send" title="发送（Ctrl+Enter）" disabled={busy || !!ended || (!text.trim() && !attachments.length)} onClick={() => void submit()}>
              {busy ? <Spinner size={15} /> : <ArrowUp size={16} />}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
