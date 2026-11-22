// 左栏：项目 → 会话。列出 samples/ 下的全部测试项目模板，项目行显示模板简介、会话数与项目记忆数；
// 未生成的项目可一键生成；每个项目下列出进行中 / 可恢复的会话与已结束（已合并 / 已丢弃）的会话；
// 底部固定"用户偏好"（全局）与"设置"入口。项目记忆、项目说明与还原从项目行打开。
// 搜索：标题即时过滤，同时在服务端查找需求、对话内容与归档摘要，命中的会话显示匹配片段。

import { useEffect, useState } from "react";
import { BookMarked, ChevronRight, Folder, FolderPlus, Heart, Info, PanelLeft, Plus, RotateCcw, Settings } from "lucide-react";
import { useStore } from "../store";
import { api } from "../api";
import type { EndedSession, Project, SessionRef } from "../types";

export default function Sidebar() {
  const { projects, preferences, leftCollapsed, busy, ended, pending, set, switchProject, newSession, resume, viewEnded, act } = useStore();
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Record<string, Record<string, string>>>({}); // 项目 → 任务ID → 匹配片段
  useEffect(() => {
    const q = query.trim();
    if (!q) return setHits({});
    const timer = setTimeout(() => {
      api<{ hits: Record<string, Record<string, string>> }>(`/api/search?q=${encodeURIComponent(q)}`)
        .then((r) => setHits(r.hits)).catch(() => setHits({}));
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);
  const toggle = (name: string) => setClosed((s) => { const n = new Set(s); n.has(name) ? n.delete(name) : n.add(name); return n; });
  const snippet = (repo: string, taskId: string) => (query.trim() ? hits[repo]?.[taskId] : undefined);
  const match = (repo: string, title: string, taskId: string) =>
    !query.trim() || title.toLowerCase().includes(query.trim().toLowerCase()) || !!snippet(repo, taskId);
  const Snippet = ({ text }: { text?: string }) => {
    if (!text) return null;
    const q = query.trim(), i = text.toLowerCase().indexOf(q.toLowerCase());
    return <div className="snippet">{i < 0 ? text : <>{text.slice(0, i)}<mark>{text.slice(i, i + q.length)}</mark>{text.slice(i + q.length)}</>}</div>;
  };

  const ensureProject = async (p: Project) => { if (!p.current) await switchProject(p.name); };
  const openMemory = (p: Project) => set({ modal: { kind: "memory", tab: "project", repo: p.name } });
  const openInfo = (p: Project) => set({ modal: { kind: "project", repo: p.name } });
  const generate = (p: Project) => { if (!busy) void switchProject(p.name, { pending: `generate-${p.name}`, success: `已生成测试项目 ${p.name}` }); };
  const resetProject = async (p: Project) => {
    if (!confirm(`把测试项目 ${p.name} 还原为初始状态？会删除它的所有任务分支、会话与项目记忆。`)) return;
    await ensureProject(p);
    await act("/api/reset", {}, () => set({ timeline: [], todos: [], ended: null }), { pending: "reset", success: `已还原测试项目 ${p.name}` });
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
      <span className="time">{s.last_active ? s.last_active.slice(5, 16).replace("T", " ") : "新"}</span>
      <div className="meta">{state === "active" ? "进行中" : "可恢复"} · {s.mode} · {s.rounds ?? 0} 次提交</div>
      <Snippet text={snippet(p.name, s.task_id)} />
    </div>
  );
  const endedRow = (p: Project, s: EndedSession) => (
    <div key={s.task_id} className={`sess ended${ended?.info.task_id === s.task_id ? " current" : ""}`} title={s.task_id}
      onClick={async () => { const p = projects.find((x) => x.ended.includes(s)); if (p && !p.current) await switchProject(p.name); await viewEnded(s); }}>
      <span className={`dot ${s.state}`} />
      <span className="t">{s.title}</span>
      <span className="time">{s.time.slice(5)}</span>
      <div className="meta">已归档 · {s.state === "merged" ? `合并 ${s.merges} 次` : "未合并"}</div>
      <Snippet text={snippet(p.name, s.task_id)} />
    </div>
  );

  return (
    <aside>
      <div className="aside-head">
        <span className="aside-title">项目与会话</span>
        <button className="ghost icon" title="收起 / 展开左栏" onClick={() => set({ leftCollapsed: !leftCollapsed })}><PanelLeft size={15} /></button>
      </div>
      <div className="aside-search"><input placeholder="搜索会话（标题、对话内容、归档摘要）" value={query} onChange={(e) => setQuery(e.target.value)} /></div>
      <div className="scroll">
        {projects.map((p) => {
          const open = !closed.has(p.name);
          // 进行中与可恢复的会话按最后一轮对话的时间排序（新会话尚无对话时排在最前），切换会话不改变顺序
          const live = [
            ...(p.active ? [{ s: p.active, state: "active" as const }] : []),
            ...p.resumable.map((s) => ({ s, state: "resumable" as const })),
          ]
            .filter(({ s }) => match(p.name, s.title, s.task_id))
            .sort((a, b) => (b.s.last_active ?? "9999").localeCompare(a.s.last_active ?? "9999"))
            .map(({ s, state }) => liveRow(p, s, state));
          const endedRows = p.ended.filter((s) => match(p.name, s.title, s.task_id)).map((s) => endedRow(p, s));
          if (query.trim() && !live.length && !endedRows.length) return null; // 搜索时隐藏没有命中会话的项目
          return (
            <div key={p.name} className={`proj${open ? " open" : ""}${p.generated ? "" : " ungenerated"}`}>
              <div className={`proj-row${p.current ? " current" : ""}`} onClick={() => toggle(p.name)}>
                <ChevronRight size={13} className="chev" />
                <Folder size={14} className="folder" />
                <span className="name" title={p.generated ? `repos/${p.name}` : "尚未从模板生成"}>{p.name}<span className="proj-title"> · {p.title}</span></span>
                <span className="proj-actions" onClick={(e) => e.stopPropagation()}>
                  <button className="ghost" title="项目说明与示例需求" onClick={() => openInfo(p)}><Info size={13} /></button>
                  {p.generated && <button className="ghost" title="还原测试项目" disabled={busy} onClick={() => resetProject(p)}><RotateCcw size={13} /></button>}
                </span>
              </div>
              <div className="proj-info" onClick={() => toggle(p.name)}>
                <div className="proj-summary" title={p.focus ? `${p.summary}\n侧重：${p.focus}` : p.summary}>{p.summary}</div>
                <div className="proj-meta">
                  {p.generated ? <>
                    <span>{p.sessions} 个会话</span>
                    <span className="link" title="查看项目记忆" onClick={(e) => { e.stopPropagation(); openMemory(p); }}><BookMarked size={11} /> {p.memory} 条记忆</span>
                  </> : <span>未生成</span>}
                </div>
              </div>
              {open && !p.generated && (
                <div className="sess-list">
                  <div className="sess-new" title={`从 samples/${p.name} 生成本地 git 仓库 repos/${p.name}`} onClick={() => generate(p)}>
                    <FolderPlus size={12} style={{ verticalAlign: -1 }} /> {pending === `generate-${p.name}` ? "正在生成…" : "生成项目"}
                  </div>
                </div>
              )}
              {open && p.generated && (
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
