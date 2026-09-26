import { Info } from "lucide-react";
import { useId, useRef, useState } from "react";

const WIDTH = 256;

/** An ⓘ icon that explains something on hover or keyboard focus (kept inside the window on phones). */
export function InfoTip({ text, label = "More information" }: { text: string; label?: string }) {
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  const ref = useRef<HTMLButtonElement>(null);
  const id = useId();
  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const left = Math.min(Math.max(8, r.left + r.width / 2 - WIDTH / 2), window.innerWidth - WIDTH - 8);
    setPos({ left, bottom: window.innerHeight - r.top + 6 });
  };
  const hide = () => setPos(null);
  return (
    <span className="inline-flex align-middle">
      <button ref={ref} type="button" aria-label={label} aria-describedby={pos ? id : undefined}
        onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
        onClick={show} onKeyDown={(e) => e.key === "Escape" && hide()}
        className="rounded-full p-0.5 text-faint hover:text-fg focus-visible:text-fg">
        <Info size={14} />
      </button>
      {pos && (
        <span role="tooltip" id={id} style={{ left: pos.left, bottom: pos.bottom, width: WIDTH }}
          className="fixed z-50 rounded-lg border border-line bg-surface px-3 py-2 text-left text-xs font-normal leading-snug text-fg shadow-card">
          {text}
        </span>
      )}
    </span>
  );
}
