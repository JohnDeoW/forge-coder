// 对话框：记忆（项目记忆按项目 / 用户偏好全局）与设置（只读展示当前配置）

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
          {preferences.length === 0 && <p className="hint">还没有偏好。</p>}
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
          {entries?.length === 0 && <p className="hint">该项目还没有项目记忆：归档会话时会自动写入会话摘要。</p>}
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
  const current = useStore((s) => s.app?.status.models);
  return (
    <Modal title="设置">
      <div className="section-title">子 Agent 与辅助 Agent 的模型<span className="r">下一轮生效，随会话保存</span></div>
      <RoleModelSettings />
      <p className="hint">主 Agent 的模型在输入框底部切换（当前 {current?.main ? modelLabel(current.main) : "-"}）；可选范围在 .env 中以逗号分隔配置，第一个为默认。</p>
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
