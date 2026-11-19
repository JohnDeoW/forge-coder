// 对话框：记忆（项目记忆按项目 / 用户偏好全局）与设置（只读展示当前配置）

import { Fragment, useEffect, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { useStore } from "../store";
import { k } from "../format";

function Modal({ title, head, children }: { title: string; head?: ReactNode; children: ReactNode }) {
  const close = () => useStore.setState({ modal: null });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="modal-mask" onClick={(e) => e.target === e.currentTarget && close()}>
      <div className="modal" role="dialog" aria-label={title}>
        <div className="modal-head"><b>{title}</b>{head}<button className="ghost icon close" title="关闭" onClick={close}><X size={16} /></button></div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

const KIND_TAG: Record<string, [string, string]> = { merged: ["ok", "已合并"], discarded: ["plain", "已丢弃"], compact: ["sub", "压缩摘要"] };

export function MemoryDialog() {
  const { modal, projects, memory, preferences, set, loadMemory, deleteMemory, addPreference, deletePreference } = useStore();
  const tab = modal?.kind === "memory" ? modal.tab : "project";
  const repo = (modal?.kind === "memory" && modal.repo) || projects.find((p) => p.current)?.name || projects[0]?.name;
  const [text, setText] = useState("");
  const entries = repo ? memory[repo] : undefined;

  useEffect(() => {
    if (tab === "project" && repo) void loadMemory(repo);
  }, [tab, repo, loadMemory]);

  const switchTab = (t: "project" | "prefs") => set({ modal: { kind: "memory", tab: t, repo } });
  const head = (
    <div className="seg">
      <button className={tab === "project" ? "on" : ""} onClick={() => switchTab("project")}>项目记忆</button>
      <button className={tab === "prefs" ? "on" : ""} onClick={() => switchTab("prefs")}>用户偏好</button>
    </div>
  );

  return (
    <Modal title="记忆" head={head}>
      {tab === "prefs" ? (
        <>
          <p className="desc">全局生效，跨项目、跨会话。只能由你添加或删除，每次请求都会全部注入主 Agent。</p>
          {preferences.length === 0 && <div className="empty-hint">还没有偏好</div>}
          {preferences.map((p, i) => (
            <div key={i} className="mrow">
              <span className="idx">{i + 1}</span><span className="grow">{p}</span>
              <button className="ghost sm danger" onClick={() => deletePreference(i + 1)}>删除</button>
            </div>
          ))}
          <form className="add-row" onSubmit={(e) => { e.preventDefault(); if (text.trim()) { void addPreference(text); setText(""); } }}>
            <input placeholder="例如：变量命名使用 snake_case" value={text} onChange={(e) => setText(e.target.value)} />
            <button className="primary" type="submit">添加</button>
          </form>
        </>
      ) : (
        <>
          <div className="desc-row">
            <select value={repo} onChange={(e) => set({ modal: { kind: "memory", tab: "project", repo: e.target.value } })}>
              {projects.map((p) => <option key={p.name}>{p.name}</option>)}
            </select>
            <span className="desc">任务合并或丢弃时由辅助 Agent 写入任务摘要，历史压缩时写入压缩摘要；每次请求按需求检索最相关的条目注入主 Agent。</span>
          </div>
          {entries?.length === 0 && <div className="empty-hint">该项目还没有项目记忆</div>}
          {entries?.slice().reverse().map((m, ri) => {
            const index = entries.length - ri; // 后端序号从 1 开始、按写入时间从旧到新
            const [cls, label] = KIND_TAG[m.kind] ?? ["plain", m.label];
            return (
              <details key={m.id} className="mrow-d" open={ri === 0}>
                <summary>
                  <span className={`tag ${cls}`}>{label}</span>
                  <span className="grow ellipsis">{m.content.split("\n")[0].replace(/^目标：/, "").replace(/^## /, "")}</span>
                  <span className="faint" style={{ fontSize: 12 }}>{m.time.slice(5)}</span>
                  <button className="ghost sm danger" onClick={(e) => { e.preventDefault(); if (repo) void deleteMemory(repo, index); }}>删除</button>
                </summary>
                <pre>{m.content}{`\n\n来源任务：${m.task}`}</pre>
              </details>
            );
          })}
          {!!entries?.length && (
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 10 }}>
              <button className="ghost sm danger" onClick={() => repo && confirm(`清空 ${repo} 的全部项目记忆？`) && deleteMemory(repo, 0)}>清空该项目的记忆</button>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}

export function SettingsDialog() {
  const settings = useStore((s) => s.settings);
  return (
    <Modal title="设置">
      <p className="desc">当前配置来自 forge-coder/.env，修改后重启面板服务生效。</p>
      {settings && (
        <div className="kv">
          {Object.entries(settings.models).map(([role, model]) => (
            <Fragment key={role}><span className="k">模型 · {role}</span><span className="mono">{model}</span></Fragment>
          ))}
          <span className="k">上下文窗口</span><span>{k(settings.context_window)} tokens</span>
          <span className="k">自动压缩阈值</span><span>{Math.round(settings.compact_threshold * 100)}%（FORGE_COMPACT_THRESHOLD）</span>
          <span className="k">压缩时保留轮数</span><span>{settings.compact_keep_rounds}（FORGE_COMPACT_KEEP_ROUNDS）</span>
          <span className="k">单轮 token 预算</span><span>{settings.round_token_limit ? k(settings.round_token_limit) : "不限制"}（FORGE_ROUND_TOKEN_LIMIT）</span>
        </div>
      )}
    </Modal>
  );
}
