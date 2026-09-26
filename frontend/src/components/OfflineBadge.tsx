import clsx from "clsx";
import { WifiOff } from "lucide-react";
import { useOnline } from "../lib/online";
import { useWebAccess, WebAccessSwitch } from "./WebAccessSwitch";

/** The home screen's promise: it all runs here, with or without the internet, plus the one switch that goes online. */
export function OfflineBadge() {
  const online = useOnline();
  const { ready, on: web } = useWebAccess();

  const text = !web
    ? "Chat, voice, photos, documents and video all run on this Mac, and nothing you share leaves it. Web access is off, so nothing goes online at all."
    : online
      ? "Chat, voice, photos, documents and video all run on this Mac, and nothing you share leaves it. Only web access uses the internet, when you want answers from the web. Don't need that? Turn it off."
      : "Web access is paused until you reconnect. Everything else runs on this Mac.";

  return (
    <div className="mt-3 flex flex-col items-center gap-2.5">
      <span role="status" className={clsx("inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-sm font-medium",
        online ? "border border-fg/80 text-fg" : "bg-fg text-bg")}>
        <WifiOff size={15} aria-hidden />
        {online ? "Works completely offline" : "You're offline, and everything still works"}
      </span>
      <div className="flex max-w-xl items-center gap-4 text-left">
        <p className="flex-1 text-sm text-muted">{text}</p>
        {ready && (
          <div className="flex shrink-0 flex-col items-center gap-1 text-xs text-muted">
            <WebAccessSwitch />
            Web access {web ? "on" : "off"}
          </div>
        )}
      </div>
    </div>
  );
}
