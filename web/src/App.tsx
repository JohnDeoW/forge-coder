// 面板整体布局：顶栏 + 左栏（项目 → 会话）+ 中间时间线与输入框 + 右侧检查器；对话框与提示浮层
// 快捷键：Ctrl+Enter 发送 · / 聚焦输入框 · Alt+1~5 切换检查器标签 · Esc 关闭对话框

import { useEffect } from "react";
import { CircleAlert, CircleCheck, Info } from "lucide-react";
import { useStore, type Tab } from "./store";
import TopBar from "./components/TopBar";
import Sidebar from "./components/Sidebar";
import Timeline from "./components/Timeline";
import Composer from "./components/Composer";
import Inspector from "./components/Inspector";
import { MemoryDialog, ProjectDialog, SettingsDialog } from "./components/Dialogs";

const TABS: Tab[] = ["changes", "todo", "context", "run", "approvals"];
const TOAST_ICON = { info: <Info size={15} />, success: <CircleCheck size={15} />, error: <CircleAlert size={15} /> };

export default function App() {
  const { leftCollapsed, rightCollapsed, theme, modal, toast, init, notify, set } = useStore();

  useEffect(() => {
    init().catch((err) => notify(`无法连接面板服务：${err.message}`, "error"));
  }, [init, notify]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("forge-theme", theme);
    } catch { /* 无法写入时只影响本次会话 */ }
  }, [theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
      if (e.altKey && /^[1-5]$/.test(e.key)) {
        e.preventDefault();
        set({ tab: TABS[Number(e.key) - 1], rightCollapsed: false });
      } else if (e.key === "/" && !typing && !useStore.getState().modal) {
        e.preventDefault();
        document.getElementById("composer-input")?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [set]);

  const cls = ["layout", leftCollapsed && "left-collapsed", rightCollapsed && "right-collapsed"].filter(Boolean).join(" ");
  return (
    <>
      <TopBar />
      <div className={cls}>
        <Sidebar />
        <main className="center">
          <Timeline />
          <Composer />
        </main>
        <Inspector />
      </div>
      {modal?.kind === "memory" && <MemoryDialog />}
      {modal?.kind === "settings" && <SettingsDialog />}
      {modal?.kind === "project" && <ProjectDialog />}
      {toast && <div className={`toast ${toast.kind}`} role="status">{TOAST_ICON[toast.kind]}{toast.text}</div>}
    </>
  );
}
