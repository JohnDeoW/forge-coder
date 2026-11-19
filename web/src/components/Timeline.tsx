// 中栏时间线：按轮显示需求、主 Agent 步骤（思考默认折叠、工具一行一条）、子 Agent 分组、
// 待办与计划、审批卡片、最终回答（Markdown）与本轮统计

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Brain, FilePen, FileText, FolderTree, ListChecks, Search, ShieldAlert, ShieldCheck, Terminal, Wrench } from "lucide-react";
import { useStore } from "../store";
import { ROLE_TAG, argSummary, editDiff, fmtSeconds, k, resultClass, short } from "../format";
import type { ApprovalItem, Item, Round, SubItem, ToolItem } from "../types";

const TOOL_ICON: Record<string, JSX.Element> = {
  Read: <FileText size={14} />, Edit: <FilePen size={14} />, MultiEdit: <FilePen size={14} />, Write: <FilePen size={14} />,
  Grep: <Search size={14} />, Glob: <Search size={14} />, ListDir: <FolderTree size={14} />, RunCommand: <Terminal size={14} />,
};
const MODE_TAG: Record<string, string> = { plan: "sub", auto: "ok", manual: "accent" };

export function Markdown({ text }: { text: string }) {
  return <div className="answer"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}

function ToolRow({ item }: { item: ToolItem }) {
  const diff = editDiff(item.name, item.args);
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="toolrow" onClick={() => setOpen(!open)}>
        <span className="icon">{TOOL_ICON[item.name] ?? <Wrench size={14} />}</span>
        <span className="name">{item.name}</span>
        <span className="arg">{argSummary(item.name, item.args)}</span>
        {item.result === undefined ? <span className="typing"><i /><i /><i /></span>
          : <span className={`res ${resultClass(item.result)}`}>{short(item.result, 60)}</span>}
      </div>
      {diff && (open || item.result === undefined) && (
        <div className="diff mini-diff">{diff.map(([c, l], i) => <div key={i} className={c}>{l}</div>)}</div>
      )}
      {open && !diff && (
        <div className="detail">
          <div className="lbl">参数</div>{JSON.stringify(item.args, null, 2)}
          {item.result !== undefined && <><div className="lbl">结果</div>{item.result}</>}
        </div>
      )}
      {open && diff && item.result !== undefined && <div className="detail"><div className="lbl">结果</div>{item.result}</div>}
    </>
  );
}

function Thought({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="toolrow thought" onClick={() => setOpen(!open)}>
      <span className="icon"><Brain size={14} /></span>
      <span style={{ whiteSpace: open ? "pre-wrap" : "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{open ? text : `思考：${short(text, 90)}`}</span>
    </div>
  );
}

function SubAgent({ item }: { item: SubItem }) {
  const models = useStore((s) => s.settings?.models);
  const running = item.endTs === undefined;
  return (
    <details className="sub-agent" open={running}>
      <summary>
        <span className={`tag ${ROLE_TAG[item.role] ?? "plain"}`}>{item.role}</span>
        <span className="ellipsis grow">{item.task}</span>
        <span className="meta">
          {models?.[item.role] ?? ""}{item.steps !== undefined ? ` · ${item.steps} 步` : ""}{item.tokens ? ` · ${k(item.tokens)}` : ""}
          {running && <span className="typing"><i /><i /><i /></span>}
        </span>
      </summary>
      <div className="inner">
        {item.items.map((i, n) => <ItemView key={n} item={i} />)}
        {item.items.length === 0 && !item.conclusion && <div className="faint" style={{ fontSize: 12, padding: 4 }}>子 Agent 运行中（恢复的会话不保留子 Agent 步骤）</div>}
        {item.conclusion !== undefined && <div className="conclusion"><b>结论</b><Markdown text={item.conclusion} /></div>}
      </div>
    </details>
  );
}

function Approval({ item }: { item: ApprovalItem }) {
  const answer = useStore((s) => s.answerApproval);
  if (item.auto) {
    const ok = item.state === "approved";
    return (
      <div className="card">
        <div className="head">{ok ? <ShieldCheck size={15} color="var(--ok)" /> : <ShieldAlert size={15} color="var(--danger)" />}
          审批助手：{ok ? "允许" : "拒绝"} <span className={`tag ${item.auto.risk === "high" ? "danger" : item.auto.risk === "low" ? "ok" : "warn"}`}>风险 {item.auto.risk}</span></div>
        <div className="cmd">{item.tool} · {item.command}</div>
        <div className="muted" style={{ fontSize: 12.5 }}>{item.auto.summary}　理由：{item.auto.reason}</div>
      </div>
    );
  }
  const pending = item.state === "pending";
  const label: Record<string, string> = { once: "已允许本次", always: "已设为本会话总是允许", deny: "已拒绝", expired: "审批已失效（可能已超时）" };
  return (
    <div className={`card approval${pending ? "" : " done"}`} id={pending ? "pending-approval" : undefined}>
      <div className="head"><ShieldAlert size={15} color="var(--warn)" />需要你确认 <span className="tag warn">{item.reason || "白名单外操作"}</span></div>
      <div className="cmd">{item.tool} · {item.command}</div>
      {pending ? (
        <div className="btns">
          <button className="sm primary" onClick={() => answer(item, "once")}>允许本次</button>
          <button className="sm" onClick={() => answer(item, "always")}>本会话总是允许该程序</button>
          <button className="sm danger" onClick={() => answer(item, "deny")}>拒绝</button>
          <span className="faint" style={{ fontSize: 12 }}>300 秒内未处理视为拒绝</span>
        </div>
      ) : <div className="muted" style={{ fontSize: 12.5 }}>{label[item.answer ?? ""] ?? "已处理"}</div>}
    </div>
  );
}

export function TodoList({ todos }: { todos: { content: string; status: string }[] }) {
  return (
    <>
      {todos.map((t, i) => (
        <div key={i} className={`todo ${t.status}`}><span className="box">{t.status === "completed" ? "✓" : ""}</span><span>{t.content}</span></div>
      ))}
    </>
  );
}

function ItemView({ item }: { item: Item }) {
  switch (item.kind) {
    case "tool": return <ToolRow item={item} />;
    case "thought": return <Thought text={item.text} />;
    case "sub": return <SubAgent item={item} />;
    case "approval": return <Approval item={item} />;
    case "todo":
      return (
        <div className="card">
          <div className="head"><ListChecks size={15} />待办清单 <span className="muted" style={{ fontWeight: 400 }}>
            {item.todos.filter((t) => t.status === "completed").length}/{item.todos.length}</span></div>
          <TodoList todos={item.todos} />
        </div>
      );
    case "plan":
      return (
        <div className="card">
          <div className="head"><ListChecks size={15} />执行计划（PlanSolve）</div>
          <ol style={{ margin: "4px 0", paddingLeft: 20, fontSize: 12.5 }}>{item.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        </div>
      );
    case "planStep":
      return <div style={{ color: "var(--accent-text)", fontWeight: 600, fontSize: 12.5, margin: "8px 0 2px" }}>▶ 步骤 {item.step}/{item.total}：{item.description}</div>;
    case "error":
      return <div className="error-line">错误：{item.text}</div>;
  }
}

function Footer({ round }: { round: Round }) {
  const openContext = useStore((s) => s.openContext);
  const [, tick] = useState(0);
  useEffect(() => {
    if (round.status !== "running") return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [round.status]);
  if (round.restored) return null;
  if (round.status === "running") {
    const waiting = round.items.some((i) => i.kind === "approval" && i.state === "pending");
    return (
      <div className="round-foot">
        <span className="tag warn">运行中</span>
        <span>{waiting ? "等待你确认操作" : "Agent 正在工作"}</span><span className="typing"><i /><i /><i /></span>
        <span>{fmtSeconds(Date.now() / 1000 - (round.startTs ?? 0))}</span>
      </div>
    );
  }
  const f = round.footer;
  const statusTag = round.status === "completed" ? "ok" : round.status === "failed" ? "danger" : "warn";
  return (
    <div className="round-foot">
      <span className={`tag ${statusTag}`}>{round.status}</span>
      {round.steps !== undefined && <span>主 Agent {round.steps} 步</span>}
      {f?.usage && <span title="本轮总量（含子 Agent 与辅助 Agent）">{k(f.usage.total_tokens)} tokens · {f.usage.model_calls} 次调用</span>}
      {f?.usage && <span>{fmtSeconds(f.usage.elapsed_s)}</span>}
      {f?.context && <span>上下文 {k(f.context.used)}</span>}
      {f?.commit ? <span className="mono">提交 {f.commit}</span> : !round.review && <span className="faint">无改动</span>}
      {f?.round != null && <span className="link" onClick={() => openContext(f.round)}>请求详情 ›</span>}
    </div>
  );
}

function RoundView({ round }: { round: Round }) {
  return (
    <div className="round">
      <div className="user-msg">
        <div className="avatar u">你</div>
        <div>
          <div className="bubble">{round.user}</div>
          <div className="note">
            {round.mode && <span className={`tag ${MODE_TAG[round.mode]}`}>{round.mode}</span>}
            {round.paradigm === "plansolve" && <span className="tag plain">PlanSolve</span>}
            {round.review && <span className="tag warn">Reviewer 审查</span>}
            {round.restored && <span className="tag plain">已恢复</span>}
          </div>
        </div>
      </div>
      <div className="agent">
        <div className={`avatar ${round.review ? "r" : "a"}`}>{round.review ? "R" : "F"}</div>
        <div className="body">
          <div className="steps">{round.items.map((item, i) => <ItemView key={i} item={item} />)}</div>
          {round.answer && <Markdown text={round.answer} />}
          <Footer round={round} />
        </div>
      </div>
    </div>
  );
}

export default function Timeline() {
  const { timeline, ended, app, viewEnded } = useStore();
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [timeline]);

  if (ended) {
    return (
      <div className="timeline">
        <div className="card ended-card">
          <div className="head">
            <span className={`tag ${ended.state === "merged" ? "ok" : "plain"}`}>{ended.state === "merged" ? "已合并" : "已丢弃"}</span>
            {ended.title}<span className="faint" style={{ fontWeight: 400 }}>{ended.task_id} · {ended.time}</span>
            <button className="ghost sm" style={{ marginLeft: "auto" }} onClick={() => viewEnded(null)}>返回当前会话</button>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>会话已结束，对话不再保留；以下是任务结束时写入项目记忆的摘要。</div>
          <pre>{ended.summary}</pre>
        </div>
      </div>
    );
  }

  return (
    <div className="timeline" ref={ref}
      onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
      {timeline.length === 0 && (
        <div className="welcome">
          <h2>在 {app?.status.repo ?? "测试项目"} 中开始一个任务</h2>
          <p>用自然语言描述需求。Agent 的改动只发生在独立的任务分支（git worktree）中，你审查后再决定合并或丢弃。</p>
          <p className="faint">plan 模式只读规划 · auto 由辅助 Agent 审批 · manual 由你确认白名单外的命令</p>
        </div>
      )}
      {timeline.map((entry) => entry.kind === "notice"
        ? <div key={entry.key} className="notice">{entry.text}</div>
        : <RoundView key={entry.key} round={entry} />)}
    </div>
  );
}
