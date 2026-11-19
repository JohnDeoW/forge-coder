// 待办：主 Agent 用 TodoWrite 维护的清单（实时）；使用 PlanSolve 时显示计划与当前步骤

import { useStore } from "../../store";
import { TodoList } from "../Timeline";
import { ListChecks } from "lucide-react";
import { Empty } from "../ui";

export default function TodoPane() {
  const { todos, timeline } = useStore();
  let plan: string[] | null = null;
  let step: { step: number; total: number; description: string } | null = null;
  for (const entry of timeline) {
    if (entry.kind !== "round") continue;
    for (const item of entry.items) {
      if (item.kind === "plan") { plan = item.steps; step = null; }
      if (item.kind === "planStep") step = item;
    }
  }
  return (
    <>
      <div className="section-title">待办清单<span className="r">主 Agent 用 TodoWrite 维护，实时更新</span></div>
      {todos.length ? <TodoList todos={todos} /> : <Empty icon={<ListChecks size={22} />} title="还没有待办清单">多步骤的需求中，主 Agent 会先用 TodoWrite 列出步骤，执行时实时更新。</Empty>}
      <div className="section-title">PlanSolve 计划<span className="r">以 PlanSolve 发送需求时显示</span></div>
      {plan ? (
        <ol style={{ margin: 0, paddingLeft: 20, fontSize: 12.5 }}>
          {plan.map((s, i) => (
            <li key={i} style={step && i + 1 === step.step ? { color: "var(--accent-text)", fontWeight: 600 } : step && i + 1 < step.step ? { color: "var(--muted)" } : undefined}>{s}</li>
          ))}
        </ol>
      ) : <p className="hint">本会话没有使用 PlanSolve。</p>}
    </>
  );
}
