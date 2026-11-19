// 改动：相对 main 的文件列表与 diff、逐轮提交、最近一次审查结论；审查 / 撤销 / 丢弃 / 合并

import { useState } from "react";
import { useStore } from "../../store";
import { parseDiff } from "../../format";
import { Markdown } from "../Timeline";

export default function ChangesPane() {
  const { changes, app, busy, timeline, review, act, notify, loadHistory } = useStore();
  const [selected, setSelected] = useState(0);
  const files = parseDiff(changes.diff);
  const file = files[Math.min(selected, files.length - 1)];
  const added = files.reduce((n, f) => n + f.added, 0);
  const deleted = files.reduce((n, f) => n + f.deleted, 0);
  const lastReview = [...timeline].reverse().find((e) => e.kind === "round" && e.review && e.answer);
  const hasTask = !!app?.status.task;

  const finish = (path: string, label: string) => {
    if (!confirm(path === "/api/accept" ? "将当前任务的改动合并到 main？" : "丢弃当前任务的全部改动？main 不受影响。")) return;
    notify(`正在${label}，辅助 Agent 生成任务摘要…`);
    void act(path, {}, (r) => {
      useStore.setState({
        timeline: [{ kind: "notice", key: `end${Date.now()}`, text: `任务已${label}${r.commit ? `（${r.commit}）` : ""}；任务摘要已写入项目记忆，可在左栏"已结束"中查看` }],
        todos: [],
      });
    });
  };

  if (!hasTask) return <div className="empty-hint">当前没有进行中的任务。发送一条需求后，Agent 的改动会出现在这里。</div>;
  return (
    <>
      <div className="section-title">相对 main 的改动<span className="r">{files.length} 个文件 · <span className="add">+{added}</span> <span className="del">−{deleted}</span></span></div>
      {files.length === 0 && <div className="empty-hint">还没有改动</div>}
      {files.map((f, i) => (
        <div key={f.path} className={`file-row${file === f ? " on" : ""}`} onClick={() => setSelected(i)}>
          <span className="path">{f.path}</span><span className="add">+{f.added}</span><span className="del">−{f.deleted}</span>
        </div>
      ))}
      {file && (
        <>
          <div className="section-title">{file.path}</div>
          <div className="diff">{file.lines.map(([c, l], i) => <div key={i} className={c}>{l || " "}</div>)}</div>
        </>
      )}
      <div className="section-title">逐轮提交<span className="r mono">{changes.branch}</span></div>
      {changes.commits.length === 0 && <div className="faint" style={{ fontSize: 12.5 }}>还没有提交</div>}
      {changes.commits.map((c) => (
        <div key={c.commit} className="commit">
          <span className="hash">{c.commit}</span><span className="grow ellipsis" title={c.subject}>{c.subject}</span>
          <span className="faint" style={{ fontSize: 12 }}>{c.time}</span><span className="add">+{c.added}</span><span className="del">−{c.deleted}</span>
        </div>
      ))}
      {lastReview && lastReview.kind === "round" && (
        <>
          <div className="section-title">审查结论<span className="r">Reviewer</span></div>
          <div className="review-box"><Markdown text={lastReview.answer} /></div>
        </>
      )}
      <div className="action-bar">
        <button disabled={busy || files.length === 0} onClick={() => void review()}>审查改动</button>
        <button disabled={busy} onClick={() => confirm("撤销最近一轮改动？该轮及之后的对话也会一并撤销。") && act("/api/undo", {}, () => void loadHistory())}>撤销上一轮</button>
        <button className="danger" disabled={busy} onClick={() => finish("/api/reject", "丢弃")}>丢弃任务</button>
        <button className="primary push" disabled={busy} onClick={() => finish("/api/accept", "合并")}>接受合并</button>
      </div>
    </>
  );
}
