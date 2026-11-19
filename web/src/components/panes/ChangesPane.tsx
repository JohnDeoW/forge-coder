// 改动：尚未合并的文件列表与 diff、逐轮提交、最近一次审查结论；审查 / 撤销 / 丢弃未合并改动 / 合并到 main（会话继续）

import { useState } from "react";
import { useStore } from "../../store";
import { parseDiff } from "../../format";
import { Markdown } from "../Timeline";

export default function ChangesPane() {
  const { changes, app, busy, timeline, review, act, merge, discard, loadHistory } = useStore();
  const [selected, setSelected] = useState(0);
  const files = parseDiff(changes.diff);
  const file = files[Math.min(selected, files.length - 1)];
  const added = files.reduce((n, f) => n + f.added, 0);
  const deleted = files.reduce((n, f) => n + f.deleted, 0);
  const lastReview = [...timeline].reverse().find((e) => e.kind === "round" && e.review && e.answer);
  const hasTask = !!app?.status.task;

  if (!hasTask) return <div className="empty-hint">当前没有进行中的会话。发送一条需求后，Agent 的改动会出现在这里。</div>;
  return (
    <>
      <div className="section-title" title="上次合并（或会话开始）以来的改动">尚未合并的改动<span className="r">{files.length} 个文件 · <span className="add">+{added}</span> <span className="del">−{deleted}</span></span></div>
      {files.length === 0 && <div className="empty-hint">没有尚未合并的改动</div>}
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
      <div className="section-title">尚未合并的提交<span className="r mono">{changes.branch}</span></div>
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
        <button className="danger" disabled={busy || files.length === 0} onClick={() => void discard()}>丢弃未合并改动</button>
        <button className="primary push" disabled={busy || files.length === 0} title="合并后会话继续，之后的改动可再次合并"
          onClick={() => confirm("将尚未合并的改动合并到 main？合并后会话继续。") && void merge()}>合并到 main</button>
      </div>
    </>
  );
}
