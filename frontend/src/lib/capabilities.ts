import { useEffect, useState } from "react";
import { get } from "../api/client";

/** What this Mac and this installation can do (GET /api/capabilities). */
export interface Feature { id: string; label: string; available: boolean; reason: string }
export interface Limitation { text: string; detail: string }
export interface Capabilities {
  machine: { chip: string; memory_gb: number; macos: string };
  tested: boolean;
  report_url: string;
  installed: { role: string; name: string }[];
  web_access: boolean;
  features: Feature[];
  limitations: Limitation[];
}

let current: Capabilities | null = null;
let pending: Promise<void> | null = null;
const listeners = new Set<(c: Capabilities | null) => void>();

/** Fetch again (after a setting that changes what's available) and update every user of the hook. */
export function refreshCapabilities(): Promise<void> {
  pending = get<Capabilities>("/capabilities")
    .then((c) => { current = c; })
    .catch(() => { pending = null; })
    .then(() => listeners.forEach((l) => l(current)));
  return pending;
}

/** Fetched once per page load; null while loading or if the backend can't tell. */
export function useCapabilities(): Capabilities | null {
  const [caps, setCaps] = useState<Capabilities | null>(current);
  useEffect(() => {
    listeners.add(setCaps);
    if (!pending) refreshCapabilities();
    else setCaps(current);
    return () => { listeners.delete(setCaps); };
  }, []);
  return caps;
}

/** The feature with this id, or undefined while loading (treat as available). */
export function useFeature(id: string): Feature | undefined {
  return useCapabilities()?.features.find((f) => f.id === id);
}
