// 右侧检查器：当前任务的改动 / 待办 / 上下文 / 运行 / 审批

import { useStore, type Tab } from "../store";
import ChangesPane from "./panes/ChangesPane";
import TodoPane from "./panes/TodoPane";
import ContextPane from "./panes/ContextPane";
import RunPane from "./panes/RunPane";
import ApprovalsPane from "./panes/ApprovalsPane";
import { parseDiff } from "../format";

export default function Inspector() {
  const { tab, set, changes, todos, approvals } = useStore();
  const files = parseDiff(changes.diff).length;
  const done = todos.filter((t) => t.status === "completed").length;
  const tabs: [Tab, string, string?][] = [
    ["changes", "改动", files ? String(files) : undefined],
    ["todo", "待办", todos.length ? `${done}/${todos.length}` : undefined],
    ["context", "上下文"],
    ["run", "运行"],
    ["approvals", "审批", approvals.length ? String(approvals.length) : undefined],
  ];
  return (
    <section className="inspector">
      <div className="tabs">
        {tabs.map(([id, label, n]) => (
          <button key={id} className={tab === id ? "on" : ""} onClick={() => set({ tab: id })}>
            {label}{n && <span className="n">{n}</span>}
          </button>
        ))}
      </div>
      <div className="pane">
        {tab === "changes" && <ChangesPane />}
        {tab === "todo" && <TodoPane />}
        {tab === "context" && <ContextPane />}
        {tab === "run" && <RunPane />}
        {tab === "approvals" && <ApprovalsPane />}
      </div>
    </section>
  );
}
