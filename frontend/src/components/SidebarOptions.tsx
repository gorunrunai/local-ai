import clsx from "clsx";
import { Check, X } from "lucide-react";
import { Link } from "react-router";
import { useCapabilities, type Feature } from "../lib/capabilities";

/** Under the logo, on every page: the four things people ask about, ticked or crossed for this Mac. */
const OPTIONS: { label: string; ids: string[] }[] = [
  { label: "Chat, photos, documents", ids: ["chat"] },
  { label: "Voice conversations, voice messages", ids: ["voice_mode", "voice_messages"] },
  { label: "Videos with sound and speech", ids: ["video_sound"] },
  { label: "Animating a photo", ids: ["animate_photo"] },
];

export function SidebarOptions({ onNavigate }: { onNavigate?: () => void }) {
  const caps = useCapabilities();
  if (!caps) return null;
  const byId = new Map(caps.features.map((f) => [f.id, f]));
  return (
    <Link to="/settings/setup" onClick={onNavigate} aria-label="What this Mac can do (open Your setup)"
      className="mx-3 mb-2 block rounded-lg px-2 py-1.5 hover:bg-surface-3">
      <ul className="space-y-0.5 text-xs">
        {OPTIONS.map(({ label, ids }) => {
          const feats = ids.map((id) => byId.get(id)).filter(Boolean) as Feature[];
          const missing = feats.find((f) => !f.available);
          const ok = feats.length > 0 && !missing;
          return (
            <li key={label} className={clsx("flex items-start gap-1.5", ok ? "text-muted" : "text-faint")}
              title={ok ? undefined : missing?.reason}>
              {ok ? <Check size={13} strokeWidth={3} className="mt-px shrink-0 text-ok" aria-hidden />
                : <X size={13} strokeWidth={3} className="mt-px shrink-0 text-danger" aria-hidden />}
              <span><span className="sr-only">{ok ? "Available: " : "Not available: "}</span>{label}</span>
            </li>
          );
        })}
      </ul>
    </Link>
  );
}
