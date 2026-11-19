// 面板整体布局：顶栏 + 左栏（项目 → 会话）+ 中间时间线与输入框 + 右侧检查器；对话框与提示浮层

import { useEffect } from "react";
import { useStore } from "./store";
import TopBar from "./components/TopBar";
import Sidebar from "./components/Sidebar";
import Timeline from "./components/Timeline";
import Composer from "./components/Composer";
import Inspector from "./components/Inspector";
import { MemoryDialog, SettingsDialog } from "./components/Dialogs";

export default function App() {
  const { leftCollapsed, rightCollapsed, theme, modal, toast, init, notify } = useStore();

  useEffect(() => {
    init().catch((err) => notify(`无法连接面板服务：${err.message}`));
  }, [init, notify]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("forge-theme", theme);
    } catch { /* 无法写入时只影响本次会话 */ }
  }, [theme]);

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
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
