// 通用小组件：加载图标、空状态、带加载状态的按钮

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { useStore } from "../store";

export function Spinner({ size = 13 }: { size?: number }) {
  return <Loader2 size={size} className="spin" aria-hidden />;
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon && <div className="empty-icon">{icon}</div>}
      <div className="empty-title">{title}</div>
      {children && <div className="empty-desc">{children}</div>}
    </div>
  );
}

/** 修改性操作的按钮：store.pending 等于 action 时显示加载图标；其他操作进行中时禁用 */
export function ActionButton({ action, children, disabled, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { action: string }) {
  const { busy, pending } = useStore();
  const running = pending === action;
  return (
    <button {...rest} disabled={disabled || busy}>
      {running && <Spinner />}
      {children}
    </button>
  );
}
