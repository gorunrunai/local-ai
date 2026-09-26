import type { Message } from "../api/types";

export interface PathEntry {
  message: Message;
  siblings: Message[]; // same parent + same role, oldest first (the "versions")
  index: number; // position of `message` among siblings
}

/** Messages on the active path, each with its sibling versions for ‹ n/m › navigation. */
export function buildPath(messages: Message[], activePath: string[]): PathEntry[] {
  const byId = new Map(messages.map((m) => [m.id, m]));
  const children = new Map<string, Message[]>();
  for (const m of messages) {
    const key = `${m.parent_id ?? "root"}:${m.role}`;
    const list = children.get(key) ?? [];
    list.push(m);
    children.set(key, list);
  }
  for (const list of children.values()) list.sort((a, b) => a.created_at - b.created_at);
  const out: PathEntry[] = [];
  for (const id of activePath) {
    const m = byId.get(id);
    if (!m) continue;
    const siblings = children.get(`${m.parent_id ?? "root"}:${m.role}`) ?? [m];
    out.push({ message: m, siblings, index: Math.max(0, siblings.findIndex((s) => s.id === m.id)) });
  }
  return out;
}

/** Group conversations for the sidebar by recency. */
export function recencyGroup(ts: number, now = Date.now() / 1000): string {
  const day = 86400;
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const today = startOfToday.getTime() / 1000;
  if (ts >= today) return "Today";
  if (ts >= today - day) return "Yesterday";
  if (ts >= now - 7 * day) return "Previous 7 days";
  if (ts >= now - 30 * day) return "Previous 30 days";
  return "Older";
}
