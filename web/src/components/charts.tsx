// 图表：水平分段条（占用与构成）、逐轮堆叠柱状图（变化趋势）、图例与数据表。
// 规则：分段之间留 2px 间隙、柱顶 4px 圆角并贴住基线；颜色固定跟随类别；数值文字使用正文颜色；
// 每个分段 / 柱子都有悬停提示；图例显示数值（浅色模式下部分色块对比度不足 3:1，以数值与数据表补充）。

import { useState, type ReactNode } from "react";
import { k, type Segment } from "../format";

interface Tip { x: number; y: number; flip: boolean; content: ReactNode }

function useTip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const show = (e: React.MouseEvent, content: ReactNode) => {
    const box = (e.currentTarget as HTMLElement).closest(".chart")!.getBoundingClientRect();
    const x = e.clientX - box.left;
    setTip({ x, y: e.clientY - box.top, flip: x > box.width / 2, content }); // 右半部分时提示框显示在指针左侧
  };
  const node = tip && (
    <div className={`chart-tip${tip.flip ? " flip" : ""}`} style={{ left: tip.x, top: tip.y }}>{tip.content}</div>
  );
  return { show, hide: () => setTip(null), node };
}

function TipBody({ title, segments, total }: { title: string; segments: Segment[]; total?: number }) {
  return (
    <>
      <div className="tip-title">{title}</div>
      {segments.filter((s) => s.value).map((s) => (
        <div key={s.label} className="tip-row"><i style={{ background: s.color }} />{s.label}<b>{k(s.value)}</b></div>
      ))}
      {total !== undefined && <div className="tip-row total">合计<b>{k(total)}</b></div>}
    </>
  );
}

/** 水平分段条：各段宽度按 scale（默认各段之和）计算，例如上下文窗口大小 */
export function StackBar({ segments, scale, height = 12 }: { segments: Segment[]; scale?: number; height?: number }) {
  const { show, hide, node } = useTip();
  const total = scale ?? segments.reduce((n, s) => n + s.value, 0);
  return (
    <div className="chart">
      <div className="stackbar" style={{ height }}>
        {segments.filter((s) => s.value).map((s) => (
          <span key={s.label} style={{ width: `${(s.value / (total || 1)) * 100}%`, background: s.color }}
            onMouseMove={(e) => show(e, <TipBody title={s.label} segments={[s]} />)} onMouseLeave={hide} />
        ))}
      </div>
      {node}
    </div>
  );
}

/** 图例：色块 + 名称 + 数值，可附明细 */
export function Legend({ segments, total }: { segments: Segment[]; total?: number }) {
  const sum = total ?? segments.reduce((n, s) => n + s.value, 0);
  return (
    <div className="legend">
      {segments.map((s) => (
        <div key={s.label} className={s.value ? "" : "zero"} title={s.detail}>
          <i style={{ background: s.color }} />
          <span className="name">{s.label}</span>
          <b>{k(s.value)}</b>
          <span className="pct">{sum ? `${Math.round((s.value / sum) * 100)}%` : ""}</span>
        </div>
      ))}
    </div>
  );
}

export interface Column { key: string; label: string; title: string; segments: Segment[] }

/** 逐轮堆叠柱状图：高度按所有柱子的最大合计缩放；点击柱子可选中该轮 */
export function RoundBars({ columns, selected, onSelect, height = 120, limit }: {
  columns: Column[]; selected?: string; onSelect?: (key: string) => void; height?: number; limit?: number;
}) {
  const { show, hide, node } = useTip();
  const totals = columns.map((c) => c.segments.reduce((n, s) => n + s.value, 0));
  const max = Math.max(1, ...totals, limit ?? 0);
  return (
    <div className="chart">
      <div className="bars" style={{ height }}>
        <span className="axis-max">{k(max)}</span>
        {limit !== undefined && <div className="limit-line" style={{ bottom: `${(limit / max) * 100}%` }}><span>上限 {k(limit)}</span></div>}
        {columns.map((c, i) => (
          <div key={c.key} className={`bar-col${selected === c.key ? " on" : ""}${onSelect ? " clickable" : ""}`}
            onClick={() => onSelect?.(c.key)}
            onMouseMove={(e) => show(e, <TipBody title={c.title} segments={c.segments} total={totals[i]} />)} onMouseLeave={hide}>
            <div className="bar" style={{ height: `${(totals[i] / max) * 100}%` }}>
              {c.segments.filter((s) => s.value).reverse().map((s) => (
                <span key={s.label} style={{ flexGrow: s.value, background: s.color }} />
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="bar-labels">{columns.map((c) => <span key={c.key} className={selected === c.key ? "on" : ""}>{c.label}</span>)}</div>
      {node}
    </div>
  );
}

/** 图表的数据表视图（默认折叠） */
export function DataTable({ columns }: { columns: Column[] }) {
  if (!columns.length) return null;
  const labels = columns[0].segments.map((s) => s.label);
  return (
    <details className="fold">
      <summary>查看数据</summary>
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th />{labels.map((l) => <th key={l}>{l}</th>)}<th>合计</th></tr></thead>
          <tbody>
            {columns.map((c) => (
              <tr key={c.key}>
                <th>{c.label}</th>
                {c.segments.map((s) => <td key={s.label}>{k(s.value)}</td>)}
                <td>{k(c.segments.reduce((n, s) => n + s.value, 0))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
