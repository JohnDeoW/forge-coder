// 时间线左侧的会话导航栏：每条用户消息一个短横线标记；悬停显示该条消息，单击滚动到该处；
// 当前可见的轮次高亮。随新消息、恢复会话与归档视图同步（由传入的轮次列表决定）。

import { useEffect, useState, type RefObject } from "react";
import type { Round } from "../types";
import { parseAttachments } from "../format";

export const roundDomId = (key: string) => `round-${key}`;

export default function TimelineNav({ rounds, scroller }: { rounds: Round[]; scroller: RefObject<HTMLDivElement> }) {
  const [visible, setVisible] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<{ key: string; top: number } | null>(null);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const box = el.getBoundingClientRect();
      const keys = new Set<string>();
      for (const r of rounds) {
        const node = document.getElementById(roundDomId(r.key));
        if (!node) continue;
        const rect = node.getBoundingClientRect();
        if (rect.bottom > box.top + 8 && rect.top < box.bottom - 8) keys.add(r.key);
      }
      setVisible(keys);
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };
    update();
    el.addEventListener("scroll", onScroll);
    window.addEventListener("resize", onScroll);
    return () => { el.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); cancelAnimationFrame(frame); };
  }, [rounds, scroller]);

  if (rounds.length < 2) return null;
  const jump = (key: string) => document.getElementById(roundDomId(key))?.scrollIntoView({ behavior: "smooth", block: "start" });
  const hovered = hover && rounds.find((r) => r.key === hover.key);
  return (
    <nav className="tl-nav" aria-label="会话导航" onMouseLeave={() => setHover(null)}>
      {rounds.map((r, i) => (
        <button key={r.key} className={`tick${visible.has(r.key) ? " on" : ""}`} aria-label={`跳到第 ${i + 1} 条消息`}
          onMouseEnter={(e) => setHover({ key: r.key, top: (e.currentTarget as HTMLElement).offsetTop })}
          onFocus={(e) => setHover({ key: r.key, top: (e.currentTarget as HTMLElement).offsetTop })}
          onClick={() => jump(r.key)}>
          <i />
        </button>
      ))}
      {hovered && (
        <div className="tl-nav-tip" style={{ top: hover!.top }}>
          <span className="faint">{hovered.review ? "审查" : hovered.number !== undefined ? `第 ${hovered.number} 轮` : "消息"}</span>
          <div>{(() => { const { text, attachments } = parseAttachments(hovered.user);
            const short = text.length > 120 ? text.slice(0, 120) + "…" : text;
            return attachments.length ? `${short}（附件 ${attachments.length} 个）` : short; })()}</div>
        </div>
      )}
    </nav>
  );
}
