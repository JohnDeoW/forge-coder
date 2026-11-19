// 左栏：项目 → 会话。每个项目下列出进行中 / 可恢复的会话与已结束（已合并 / 已丢弃）的会话；
// 底部固定"用户偏好"（全局）与"设置"入口。项目记忆从项目行打开。

import { useState } from "react";
import { BookMarked, ChevronRight, Folder, Heart, PanelLeft, Plus, RotateCcw, Settings } from "lucide-react";
import { useStore } from "../store";
import type { EndedSession, Project, SessionRef } from "../types";

export default function Sidebar() {
  const { projects, preferences, leftCollapsed, busy, ended, set, switchProject, newSession, resume, viewEnded, act } = useStore();
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const toggle = (name: string) => setClosed((s) => { const n = new Set(s); n.has(name) ? n.delete(name) : n.add(name); return n; });
  const match = (title: string) => !query || title.toLowerCase().includes(query.toLowerCase());

  const ensureProject = async (p: Project) => { if (!p.current) await switchProject(p.name); };
  const openMemory = (p: Project) => set({ modal: { kind: "memory", tab: "project", repo: p.name } });
  const resetProject = async (p: Project) => {
    if (!confirm(`把测试项目 ${p.name} 还原为初始状态？会删除它的所有任务分支、会话与项目记忆。`)) return;
    await ensureProject(p);
    await act("/api/reset", {}, () => set({ timeline: [], todos: [], ended: null }));
  };

  const liveRow = (p: Project, s: SessionRef, state: "active" | "resumable") => (
    <div key={s.task_id} title={s.task_id}
      className={`sess${state === "active" && p.current && !ended ? " current" : ""}`}
      onClick={async () => {
        if (busy) return;
        await ensureProject(p);
        if (state === "resumable") await resume(s.task_id);
        else await viewEnded(null);
      }}>
      <span className={`dot ${state}`} />
      <span className="t">{s.title}</span>
      <span className="time">{s.saved_at ? s.saved_at.slice(5, 16).replace("T", " ") : ""}</span>
      <div className="meta">{state === "active" ? "进行中" : "可恢复"} · {s.mode} · {s.rounds ?? 0} 轮</div>
    </div>
  );
  const endedRow = (s: EndedSession) => (
    <div key={s.task_id} className={`sess ended${ended?.info.task_id === s.task_id ? " current" : ""}`} title={s.task_id}
      onClick={async () => { const p = projects.find((x) => x.ended.includes(s)); if (p && !p.current) await switchProject(p.name); await viewEnded(s); }}>
      <span className={`dot ${s.state}`} />
      <span className="t">{s.title}</span>
      <span className="time">{s.time.slice(5)}</span>
      <div className="meta">已归档 · {s.state === "merged" ? `合并 ${s.merges} 次` : "未合并"}</div>
    </div>
  );

  return (
    <aside>
      <div className="aside-head">
        <span className="aside-title">项目与会话</span>
        <button className="ghost icon" title="收起 / 展开左栏" onClick={() => set({ leftCollapsed: !leftCollapsed })}><PanelLeft size={15} /></button>
      </div>
      <div className="aside-search"><input placeholder="搜索会话" value={query} onChange={(e) => setQuery(e.target.value)} /></div>
      <div className="scroll">
        {projects.map((p) => {
          const open = !closed.has(p.name);
          const live = [
            ...(p.active && match(p.active.title) ? [liveRow(p, p.active, "active")] : []),
            ...p.resumable.filter((s) => match(s.title)).map((s) => liveRow(p, s, "resumable")),
          ];
          const endedRows = p.ended.filter((s) => match(s.title)).map(endedRow);
          return (
            <div key={p.name} className={`proj${open ? " open" : ""}`}>
              <div className={`proj-row${p.current ? " current" : ""}`} onClick={() => toggle(p.name)}>
                <ChevronRight size={13} className="chev" />
                <Folder size={14} className="folder" />
                <span className="name" title={`repos/${p.name}`}>{p.name}</span>
                <span className="proj-actions" onClick={(e) => e.stopPropagation()}>
                  <button className="ghost" title={`项目记忆（${p.memory} 条）`} onClick={() => openMemory(p)}><BookMarked size={13} />{p.memory}</button>
                  <button className="ghost" title="还原测试项目" disabled={busy} onClick={() => resetProject(p)}><RotateCcw size={13} /></button>
                </span>
              </div>
              {open && (
                <div className="sess-list">
                  {live}
                  <div className="sess-new" title="当前会话会自动保存，之后可从列表恢复；发送第一条需求时创建任务分支"
                    onClick={async () => { if (!busy) { await ensureProject(p); await newSession(); } }}>
                    <Plus size={12} style={{ verticalAlign: -1 }} /> 新会话
                  </div>
                  {endedRows.length > 0 && <div className="sess-sep">已归档 · {endedRows.length}</div>}
                  {endedRows}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="aside-foot">
        <div className="foot-item" title="全局生效的用户偏好" onClick={() => set({ modal: { kind: "memory", tab: "prefs" } })}>
          <span className="ico"><Heart size={15} /></span><span>用户偏好</span><span className="count">{preferences.length}</span>
        </div>
        <div className="foot-item" onClick={() => set({ modal: { kind: "settings" } })}>
          <span className="ico"><Settings size={15} /></span><span>设置</span><span className="count">模型 · 压缩 · 预算</span>
        </div>
      </div>
    </aside>
  );
}
