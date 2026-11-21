// 对话框：记忆（项目记忆按项目 / 用户偏好全局 / 修改记录，可编辑与撤销）与设置（模型、下一步建议开关与当前配置）

import { useEffect, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { useStore } from "../store";
import { k } from "../format";
import { RoleModelSettings, modelLabel } from "./ModelPicker";

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

const KIND_TAG: Record<string, [string, string]> = {
  merged: ["ok", "会话摘要·已合并"], discarded: ["plain", "会话摘要·未合并"], note: ["accent", "项目约定"], compact: ["sub", "压缩摘要（旧）"],
};
const TRIGGER_TAG: Record<string, [string, string]> = { main: ["accent", "主 Agent 派出"], archive: ["ok", "归档会话"], user: ["plain", "你"] };
const OP_LABEL: Record<string, string> = { add: "新增", update: "修改", delete: "删除" };

/** 可编辑的一行：查看时显示内容，点"编辑"后变为文本框 */
function EditableText({ text, onSave, multiline }: { text: string; onSave: (value: string) => void; multiline?: boolean }) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null) {
    return (
      <>
        {multiline ? <pre>{text}</pre> : <span className="grow">{text}</span>}
        <button className="ghost sm" onClick={(e) => { e.preventDefault(); setDraft(text); }}>编辑</button>
      </>
    );
  }
  return (
    <div className="edit-box">
      <textarea value={draft} rows={multiline ? 8 : 2} autoFocus onChange={(e) => setDraft(e.target.value)} />
      <div className="btns">
        <button className="sm primary" disabled={!draft.trim() || draft === text} onClick={() => { onSave(draft); setDraft(null); }}>保存</button>
        <button className="sm" onClick={() => setDraft(null)}>取消</button>
        <span className="faint">保存后这条由你维护，memory 子 Agent 不会再改动</span>
      </div>
    </div>
  );
}

export function MemoryDialog() {
  const {
    modal, projects, memory, memoryChanges, preferences, agentPreferences, set, loadMemory, loadMemoryChanges, editMemory,
    deleteMemory, addPreference, editPreference, deletePreference, undoMemoryChange,
  } = useStore();
  const tab = modal?.kind === "memory" ? modal.tab : "project";
  const repo = (modal?.kind === "memory" && modal.repo) || projects.find((p) => p.current)?.name || projects[0]?.name;
  const [text, setText] = useState("");
  const entries = repo ? memory[repo] : undefined;
  const changes = repo ? memoryChanges[repo] : undefined;

  useEffect(() => {
    if (tab === "project" && repo) void loadMemory(repo);
    if (tab === "changes" && repo) void loadMemoryChanges(repo);
  }, [tab, repo, loadMemory, loadMemoryChanges]);

  const switchTab = (t: "project" | "prefs" | "changes") => set({ modal: { kind: "memory", tab: t, repo } });
  const head = (
    <div className="seg">
      <button className={tab === "project" ? "on" : ""} onClick={() => switchTab("project")}>项目记忆</button>
      <button className={tab === "prefs" ? "on" : ""} onClick={() => switchTab("prefs")}>用户偏好</button>
      <button className={tab === "changes" ? "on" : ""} onClick={() => switchTab("changes")}>修改记录</button>
    </div>
  );
  const repoPicker = (desc: string) => (
    <div className="desc-row">
      <select value={repo} onChange={(e) => set({ modal: { kind: "memory", tab, repo: e.target.value } })}>
        {projects.map((p) => <option key={p.name}>{p.name}</option>)}
      </select>
      <span className="desc">{desc}</span>
    </div>
  );

  return (
    <Modal title="记忆" head={head}>
      {tab === "prefs" && (
        <>
          <p className="desc">全局生效，跨项目、跨会话，每次请求都会全部注入主 Agent。你可以添加、编辑、删除；对话中表达的长期偏好会由 memory 子 Agent 整理（标注"Agent 整理"），你写入或编辑过的条目它不会改动。</p>
          {preferences.length === 0 && <p className="hint">还没有偏好。</p>}
          {preferences.map((p, i) => (
            <div key={`${i}-${p}`} className="mrow">
              <span className="idx">{i + 1}</span>
              {agentPreferences.includes(p) && <span className="tag sub" title="由 memory 子 Agent 依据对话整理">Agent 整理</span>}
              <EditableText text={p} onSave={(v) => void editPreference(i + 1, v)} />
              <button className="ghost sm danger" onClick={() => deletePreference(i + 1)}>删除</button>
            </div>
          ))}
          <form className="add-row" onSubmit={(e) => { e.preventDefault(); if (text.trim()) { void addPreference(text); setText(""); } }}>
            <input placeholder="例如：变量命名使用 snake_case" value={text} onChange={(e) => setText(e.target.value)} />
            <button className="primary" type="submit">添加</button>
          </form>
        </>
      )}
      {tab === "project" && (
        <>
          {repoPicker("归档会话时 memory 子 Agent 写入会话摘要并整理已有记忆；会话中主 Agent 也会派它记录项目约定。每次请求按需求检索最相关的条目注入主 Agent。")}
          {entries?.length === 0 && <p className="hint">该项目还没有项目记忆：归档会话时会自动写入会话摘要。</p>}
          {entries?.slice().reverse().map((m, ri) => {
            const index = entries.length - ri; // 后端序号从 1 开始、按写入时间从旧到新
            const [cls, label] = KIND_TAG[m.kind] ?? ["plain", m.label];
            return (
              <details key={m.id} className="mrow-d" open={ri === 0}>
                <summary>
                  <span className={`tag ${cls}`}>{label}</span>
                  {m.by_user && <span className="tag plain" title="你编辑过，memory 子 Agent 不会改动">你编辑过</span>}
                  <span className="grow ellipsis">{m.content.split("\n")[0].replace(/^目标：/, "").replace(/^## /, "")}</span>
                  <span className="faint" style={{ fontSize: 12 }}>{m.time.slice(5)}</span>
                  <button className="ghost sm danger" onClick={(e) => { e.preventDefault(); if (repo) void deleteMemory(repo, index); }}>删除</button>
                </summary>
                <div className="mrow-body">
                  <EditableText multiline text={m.content} onSave={(v) => repo && void editMemory(repo, index, v)} />
                  <div className="faint" style={{ fontSize: 12 }}>来源会话：{m.task || "-"}</div>
                </div>
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
      {tab === "changes" && (
        <>
          {repoPicker("该项目的项目记忆与全局用户偏好的每次修改：来源、理由与修改前后的内容；可以撤销（条目之后又被改动过时不能撤销）。")}
          {changes?.length === 0 && <p className="hint">还没有修改记录。</p>}
          {changes?.map((c) => {
            const [cls, label] = TRIGGER_TAG[c.trigger] ?? ["plain", c.trigger];
            return (
              <details key={c.id} className={`mrow-d${c.undone ? " undone" : ""}`}>
                <summary>
                  <span className={`tag ${cls}`}>{label}</span>
                  <span className="tag plain">{OP_LABEL[c.op]}{c.target === "preferences" ? "偏好" : "项目记忆"}</span>
                  <span className="grow ellipsis">{(c.after ?? c.before ?? "").split("\n")[0]}</span>
                  <span className="faint" style={{ fontSize: 12 }}>{c.time.slice(5)}</span>
                  {c.undone ? <span className="faint" style={{ fontSize: 12 }}>已撤销</span> : (
                    <button className="ghost sm" onClick={(e) => { e.preventDefault(); if (repo) void undoMemoryChange(repo, c.id); }}>撤销</button>
                  )}
                </summary>
                <div className="mrow-body">
                  {c.reason && <div className="muted" style={{ fontSize: 12.5 }}>理由：{c.reason}</div>}
                  {c.before && <><div className="lbl">修改前</div><pre>{c.before}</pre></>}
                  {c.after && <><div className="lbl">修改后</div><pre>{c.after}</pre></>}
                  <div className="faint" style={{ fontSize: 12 }}>会话：{c.task || "-"} · 记录 {c.id}</div>
                </div>
              </details>
            );
          })}
        </>
      )}
    </Modal>
  );
}

export function SettingsDialog() {
  const settings = useStore((s) => s.settings);
  const current = useStore((s) => s.app?.status.models);
  const setOption = useStore((s) => s.setOption);
  return (
    <Modal title="设置">
      <div className="section-title">子 Agent 与辅助 Agent 的模型<span className="r">下一轮生效，随会话保存</span></div>
      <RoleModelSettings />
      <p className="hint">主 Agent 的模型在输入框底部切换（当前 {current?.main ? modelLabel(current.main) : "-"}）；可选范围在 .env 中以逗号分隔配置，第一个为默认。</p>
      <div className="section-title" style={{ marginTop: 16 }}>对话</div>
      <label className="role-row">
        <span className="grow"><b>下一步建议</b><span className="faint"> · 每轮结束后由辅助 Agent 给出 2~3 条可直接发送的需求</span></span>
        <input type="checkbox" checked={!!settings?.options?.next_suggestions}
          onChange={(e) => void setOption("next_suggestions", e.target.checked)} />
      </label>
      <div className="section-title" style={{ marginTop: 16 }}>其他配置<span className="r">来自 forge-coder/.env，修改后重启面板服务生效</span></div>
      {settings && (
        <div className="kv">
          <span className="k">上下文窗口</span><span>{k(settings.context_window)} tokens</span>
          <span className="k">自动压缩阈值</span><span>{Math.round(settings.compact_threshold * 100)}%（FORGE_COMPACT_THRESHOLD）</span>
          <span className="k">压缩时保留轮数</span><span>{settings.compact_keep_rounds}（FORGE_COMPACT_KEEP_ROUNDS）</span>
          <span className="k">单轮 token 预算</span><span>{settings.round_token_limit ? k(settings.round_token_limit) : "不限制"}（FORGE_ROUND_TOKEN_LIMIT）</span>
        </div>
      )}
      <div className="section-title" style={{ marginTop: 16 }}>快捷键</div>
      <div className="kv">
        <span className="k"><kbd>Ctrl</kbd> + <kbd>Enter</kbd></span><span>发送需求</span>
        <span className="k"><kbd>/</kbd></span><span>聚焦输入框；在输入框开头输入 / 查看命令</span>
        <span className="k"><kbd>Alt</kbd> + <kbd>1</kbd>~<kbd>5</kbd></span><span>切换右侧检查器标签（改动 / 待办 / 上下文 / 运行 / 审批）</span>
        <span className="k"><kbd>Esc</kbd></span><span>关闭对话框</span>
      </div>
    </Modal>
  );
}
