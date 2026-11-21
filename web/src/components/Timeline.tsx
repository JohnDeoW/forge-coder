// 中栏时间线：按轮显示需求、主 Agent 步骤（思考默认折叠、工具一行一条）、子 Agent 分组、
// 待办与计划、审批卡片、最终回答（Markdown）与本轮统计；用户消息可回溯，失败的轮次显示原因并可重试；
// 最近一轮下方显示下一步建议（点击直接发送）；左侧导航栏按用户消息定位

import { createContext, useContext, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Archive, Brain, ChevronRight, CircleAlert, CircleStop, FilePen, FileText, FolderTree, ListChecks, Lightbulb, RotateCcw, Search, ShieldAlert, ShieldCheck, Terminal, Wrench } from "lucide-react";
import TimelineNav, { roundDomId } from "./TimelineNav";
import { useStore } from "../store";
import { ROLE_TAG, argSummary, editDiff, fmtSeconds, k, resultClass, short } from "../format";
import type { ApprovalItem, Entry, Item, Round, SubItem, ToolItem } from "../types";

const TOOL_ICON: Record<string, JSX.Element> = {
  Read: <FileText size={14} />, Edit: <FilePen size={14} />, MultiEdit: <FilePen size={14} />, Write: <FilePen size={14} />,
  Grep: <Search size={14} />, Glob: <Search size={14} />, ListDir: <FolderTree size={14} />, RunCommand: <Terminal size={14} />,
};
const MODE_TAG: Record<string, string> = { plan: "sub", auto: "ok", manual: "accent" };
/** 当前轮次各来源使用的模型：结束后取本轮用量中的记录，运行中取当前选择 */
const RoundModels = createContext<Record<string, string>>({});
const STATUS_LABEL: Record<string, string> = { completed: "完成", stopped: "已停止", failed: "失败", max_iterations: "达到步数上限", cancelled: "已取消" };

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
    <>
      <div className="toolrow thought" onClick={() => setOpen(!open)} title={open ? "点击折叠" : "点击展开"}>
        <span className="icon"><Brain size={14} /></span>
        <span className="arg" style={{ fontFamily: "inherit" }}>思考：{short(text, 90)}</span>
        <ChevronRight size={13} className={`chev${open ? " open" : ""}`} />
      </div>
      {open && <div className="thought-body">{text}</div>}
    </>
  );
}

function SubAgent({ item }: { item: SubItem }) {
  const models = useContext(RoundModels);
  const running = item.endTs === undefined;
  const [open, setOpen] = useState(running);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) setOpen(false); // 子 Agent 结束后折叠为一行，结论仍可展开查看
    wasRunning.current = running;
  }, [running]);
  return (
    <details className="sub-agent" open={open}>
      <summary onClick={(e) => { e.preventDefault(); setOpen(!open); }}>
        <span className={`tag ${ROLE_TAG[item.role] ?? "plain"}`}>{item.role}</span>
        <span className="ellipsis grow">{item.task}</span>
        <span className="meta">
          {models?.[item.role] ?? ""}{item.steps !== undefined ? ` · ${item.steps} 步` : ""}{item.tokens ? ` · ${k(item.tokens)}` : ""}
          {running && <span className="typing"><i /><i /><i /></span>}
        </span>
      </summary>
      <div className="inner">
        {item.items.map((i, n) => <ItemView key={n} item={i} />)}
        {item.items.length === 0 && (
          <div className="faint" style={{ fontSize: 12, padding: 4 }}>{running ? "子 Agent 正在启动…" : "恢复的会话只保留子 Agent 的结论"}</div>
        )}
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
  if (round.restored && !round.footer) return null; // 早期只由消息恢复的轮次没有统计
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
      <span className={`tag ${statusTag}`}>{STATUS_LABEL[round.status] ?? round.status}</span>
      {round.steps !== undefined && <span>主 Agent {round.steps} 步</span>}
      {f?.usage && <span title="本轮总量（含子 Agent 与辅助 Agent）">{k(f.usage.total_tokens)} tokens · {f.usage.model_calls} 次调用</span>}
      {f?.usage && <span>{fmtSeconds(f.usage.elapsed_s)}</span>}
      {f?.usage?.models?.["forge-coder"] && <span className="mono" title="主 Agent 本轮使用的模型">{f.usage.models["forge-coder"]}</span>}
      {f?.context && <span>上下文 {k(f.context.used)}</span>}
      {f?.commit ? <span className="mono">提交 {f.commit}</span> : !round.review && <span className="faint">无改动</span>}
      {f?.round != null && <span className="link" onClick={() => openContext(f.round)}>请求详情 ›</span>}
    </div>
  );
}

/** 执行过程的一行摘要：步骤数与各工具 / 子 Agent 的次数 */
function stepSummary(items: Item[]): string {
  const counts = new Map<string, number>();
  let thoughts = 0;
  for (const item of items) {
    if (item.kind === "tool") counts.set(item.name, (counts.get(item.name) ?? 0) + 1);
    else if (item.kind === "sub") counts.set(`${item.role} 子 Agent`, (counts.get(`${item.role} 子 Agent`) ?? 0) + 1);
    else if (item.kind === "thought") thoughts++;
  }
  const parts = [...counts].map(([name, n]) => (n > 1 ? `${name} ×${n}` : name));
  if (thoughts) parts.unshift(`思考 ×${thoughts}`);
  return parts.join(" · ");
}

function Steps({ round }: { round: Round }) {
  const running = round.status === "running";
  // 进行中展开；结束后（以及恢复的轮次）默认折叠为一行摘要，点击展开
  const [open, setOpen] = useState(running);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) setOpen(false);
    wasRunning.current = running;
  }, [running]);
  if (round.items.length === 0) return null;
  const pending = round.items.some((i) => i.kind === "approval" && i.state === "pending");
  const show = open || pending;
  return (
    <div className="steps-wrap">
      <div className="steps-head" onClick={() => setOpen(!show)}>
        <ChevronRight size={13} className={`chev${show ? " open" : ""}`} />
        <span>执行过程{running ? "（进行中）" : ""}</span>
        <span className="muted ellipsis grow">{stepSummary(round.items)}</span>
      </div>
      {show && <div className="steps">{round.items.map((item, i) => <ItemView key={i} item={item} />)}</div>}
    </div>
  );
}

/** 本轮未正常完成：已停止，或失败原因（预算超限、模型超时等）与重试入口 */
function OutcomeBox({ round, canRetry }: { round: Round; canRetry: boolean }) {
  const { rewind, busy, pending } = useStore();
  const o = round.outcome;
  if (!o || o.status === "completed") return null;
  if (o.status === "stopped") {
    return <div className="outcome stopped"><CircleStop size={15} /><span className="grow">{o.reason ?? "已停止本轮"}，已产生的改动照常提交</span></div>;
  }
  return (
    <div className="outcome failed">
      <CircleAlert size={15} />
      <div className="grow">
        <div>{o.reason}</div>
        {o.detail && <div className="detail-line">{o.detail}</div>}
      </div>
      {canRetry && (
        <button className="sm" disabled={busy} title="撤销本轮的改动与对话后重新发送同一需求"
          onClick={() => void rewind(round.number!, { retry: true })}>
          {pending === `rewind-${round.number}` ? "重试中…" : "重试本轮"}
        </button>
      )}
    </div>
  );
}

/** 下一步建议：最近一轮下方的可点选需求，点击直接发送 */
function Suggestions({ items }: { items: string[] }) {
  const { send, busy } = useStore();
  return (
    <div className="suggest">
      <Lightbulb size={13} className="faint" />
      {items.map((s) => (
        <button key={s} className="chip" disabled={busy} title="直接发送这条需求" onClick={() => void send(s, "react")}>{s}</button>
      ))}
    </div>
  );
}

function RoundView({ round, isLast }: { round: Round; isLast: boolean }) {
  const { app, busy, ended, rewind } = useStore();
  const current = app?.status.models;
  const models: Record<string, string> = round.footer?.usage?.models
    ?? (round.status === "running" && current ? { "forge-coder": current.main, ...current } : {});
  const rewindable = !ended && !busy && round.number !== undefined && !!app?.rewindable.includes(round.number);
  const onRewind = () => {
    if (confirm(`回溯到第 ${round.number} 轮之前？代码与对话回到发送这条消息前的状态，之后各轮的改动与运行记录一并移除，这条消息放回输入框。`)) {
      void rewind(round.number!);
    }
  };
  return (
    <RoundModels.Provider value={models}>
    <div className="round" id={roundDomId(round.key)}>
      <div className="user-msg">
        <div className="avatar u">你</div>
        <div className="grow">
          <div className="bubble">{round.user}</div>
          <div className="note">
            {round.number !== undefined && <span className="faint">第 {round.number} 轮</span>}
            {round.mode && <span className={`tag ${MODE_TAG[round.mode]}`}>{round.mode}</span>}
            {round.paradigm === "plansolve" && <span className="tag plain">PlanSolve</span>}
            {round.review && <span className="tag warn">Reviewer 审查</span>}
            {round.restored && <span className="tag plain">已恢复</span>}
            {rewindable && (
              <button className="ghost xs rewind" title="回溯到发送这条消息之前" onClick={onRewind}><RotateCcw size={12} />回溯</button>
            )}
          </div>
        </div>
      </div>
      <div className="agent">
        <div className={`avatar ${round.review ? "r" : "a"}`}>{round.review ? "R" : "F"}</div>
        <div className="body">
          <Steps round={round} />
          {round.answer && <Markdown text={round.answer} />}
          <OutcomeBox round={round} canRetry={isLast && rewindable} />
          <Footer round={round} />
          {isLast && !ended && !busy && !!round.suggestions?.length && <Suggestions items={round.suggestions} />}
        </div>
      </div>
    </div>
    </RoundModels.Provider>
  );
}

function Entries({ entries }: { entries: Entry[] }) {
  const last = [...entries].reverse().find((e) => e.kind === "round" && !e.review);
  return (
    <>
      {entries.map((entry) => entry.kind === "notice"
        ? <div key={entry.key} className="notice">{entry.text}</div>
        : <RoundView key={entry.key} round={entry} isLast={entry === last} />)}
    </>
  );
}

export default function Timeline() {
  const { timeline, ended, app, viewEnded } = useStore();
  const ref = useRef<HTMLDivElement>(null);
  const endedRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current && !ended) el.scrollTop = el.scrollHeight;
  }, [timeline, ended]);

  if (ended) {
    const info = ended.info;
    return (
      <div className="timeline-wrap">
      <TimelineNav rounds={roundsOf(ended.timeline)} scroller={endedRef} />
      <div className="timeline" ref={endedRef}>
        <div className="card ended-card">
          <div className="head">
            <Archive size={15} />
            <span className={`tag ${info.state === "merged" ? "ok" : "plain"}`}>{info.state === "merged" ? `已合并 ${info.merges} 次` : "未合并"}</span>
            <span className="ellipsis grow">{info.title}</span>
            <button className="ghost sm" onClick={() => viewEnded(null)}>返回当前会话</button>
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{info.task_id} · 归档于 {info.time} · 只读</div>
          <details className="fold" style={{ marginTop: 8 }}>
            <summary>写入项目记忆的会话摘要</summary>
            <pre style={{ fontFamily: "inherit" }}>{info.summary || "（归档时没有生成摘要）"}</pre>
          </details>
          {!!info.memory_changes?.length && (
            <details className="fold" style={{ marginTop: 6 }}>
              <summary>归档时 memory 子 Agent 整理记忆 · {info.memory_changes.length} 项修改</summary>
              <div className="change-list">
                {info.memory_changes.map((c) => (
                  <div key={c.id}>
                    <span className="tag plain">{{ add: "新增", update: "修改", delete: "删除" }[c.op]}{c.target === "preferences" ? "偏好" : "项目记忆"}</span>
                    <span>{(c.after ?? c.before ?? "").split("\n")[0]}</span>
                    {c.reason && <span className="faint"> · {c.reason}</span>}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
        <Entries entries={ended.timeline} />
      </div>
      </div>
    );
  }

  return (
    <div className="timeline-wrap">
    <TimelineNav rounds={roundsOf(timeline)} scroller={ref} />
    <div className="timeline" ref={ref}
      onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
      {timeline.length === 0 && (
        <div className="welcome">
          <h2>在 {app?.status.repo ?? "测试项目"} 中开始一个会话</h2>
          <p>用自然语言描述需求。Agent 的改动只发生在独立的任务分支（git worktree）中，你审查后合并到 main，会话可以继续；完成后归档会话，总结会写入项目记忆。</p>
          <p className="faint">plan 模式只读规划 · auto 由辅助 Agent 审批 · manual 由你确认白名单外的命令</p>
        </div>
      )}
      <Entries entries={timeline} />
    </div>
    </div>
  );
}

const roundsOf = (entries: Entry[]) => entries.filter((e): e is Round => e.kind === "round");
