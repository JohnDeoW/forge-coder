// 模型选择：
// - MainModelMenu：输入框底部的主 Agent 模型按钮（如 "GPT-X.Y Name"），点击向上弹出菜单，列出 .env 配置的可选模型，当前选择打勾；
// - RoleModelSettings：设置对话框中子 Agent 与辅助 Agent 的模型选择。
// 切换都是下一轮生效，选择随会话保存。

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { useStore } from "../store";

/** gpt-x.y-name → GPT-x.y Name；无法识别的名称原样显示 */
export function modelLabel(id: string): string {
  const m = /^gpt-([\d.]+)(?:-(.+))?$/i.exec(id);
  if (!m) return id;
  const variant = (m[2] ?? "").split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
  return `GPT-${m[1]}${variant ? ` ${variant}` : ""}`;
}

export function MainModelMenu() {
  const { app, settings, busy, ended, setModel } = useStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  const current = app?.status.models?.main;
  const options = settings?.model_options?.main ?? [];
  if (!current) return null;
  const locked = busy || !!ended || options.length < 2;
  return (
    <div className="model-menu" ref={ref}>
      <button className="model-pill" disabled={locked} onClick={() => setOpen(!open)}
        title={busy ? "本轮进行中，结束后可切换" : "主 Agent 使用的模型（下一轮生效）；子 Agent 与辅助 Agent 的模型在设置中选择"}>
        {modelLabel(current)}<ChevronDown size={12} />
      </button>
      {open && (
        <div className="menu-pop">
          <div className="menu-head">选择模型</div>
          {options.map((m, i) => (
            <button key={m} className={`menu-item${m === current ? " on" : ""}`}
              onClick={() => { setOpen(false); if (m !== current) void setModel("main", m); }}>
              <span className="grow">
                {modelLabel(m)}
                {i === 0 && <span className="sub">默认</span>}
              </span>
              {m === current && <Check size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const ROLE_NAMES: [string, string, string][] = [
  ["explorer", "explorer", "只读探索代码"],
  ["tester", "tester", "运行测试并汇报"],
  ["reviewer", "reviewer", "/review 审查改动"],
  ["memory", "memory", "维护项目记忆与用户偏好"],
  ["utility", "辅助 Agent", "auto 审批、历史压缩摘要"],
];

export function RoleModelSettings() {
  const { app, settings, busy, ended, setModel } = useStore();
  const models = app?.status.models;
  const options = settings?.model_options;
  if (!models || !options) return null;
  return (
    <div className="role-models">
      {ROLE_NAMES.map(([role, name, desc]) => (
        <label key={role} className="role-row">
          <span className="grow"><b>{name}</b><span className="faint"> · {desc}</span></span>
          <select value={models[role]} disabled={busy || !!ended || (options[role]?.length ?? 0) < 2}
            onChange={(e) => void setModel(role, e.target.value)}>
            {(options[role] ?? [models[role]]).map((m, i) => <option key={m} value={m}>{modelLabel(m)}{i === 0 ? "（默认）" : ""}</option>)}
          </select>
        </label>
      ))}
    </div>
  );
}
