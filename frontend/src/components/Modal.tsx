import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

export function Modal({ title, onClose, children, wide, footer }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean; footer?: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}
        className={`flex max-h-[88vh] w-full flex-col rounded-2xl border border-line bg-surface shadow-card outline-none ${wide ? "max-w-5xl" : "max-w-lg"}`}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-base font-semibold">{title}</h2>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-fg" aria-label="Close"><X size={18} /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({ title, body, confirmLabel, danger, onConfirm, onClose }: {
  title: string; body: ReactNode; confirmLabel: string; danger?: boolean; onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Modal title={title} onClose={onClose} footer={
      <>
        <button type="button" onClick={onClose} className="rounded-lg px-4 py-1.5 text-sm hover:bg-surface-2">Cancel</button>
        <button type="button" autoFocus onClick={() => { onConfirm(); onClose(); }}
          className={`rounded-lg px-4 py-1.5 text-sm font-medium ${danger ? "bg-danger text-white" : "bg-accent text-on-accent"}`}>{confirmLabel}</button>
      </>
    }>
      <div className="text-sm text-muted">{body}</div>
    </Modal>
  );
}
