import type { StreamEvent } from "./types";

const TOKEN_KEY = "la_token";

export function apiUrl(path: string): string {
  return `/api${path}`;
}

export function wsUrl(path: string): string {
  return `${location.protocol.replace(/^http/, "ws")}//${location.host}/api${path}`;
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: token lives for this page only */
  }
  // Same token as a cookie so <img>/<video> requests for attachments are authorized too.
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = token
    ? `la_token=${encodeURIComponent(token)}; Path=/; SameSite=Strict; Max-Age=31536000${secure}`
    : "la_token=; Path=/; Max-Age=0";
}

/** A sign-in link: the Mac's address with the token after "#" (never sent to any server). */
export function signInLink(address: string, token: string): string {
  const u = new URL(address);
  u.hash = `token=${encodeURIComponent(token)}`;
  return u.toString();
}

/** Opened from a sign-in link: save its token and take it out of the address bar (and history). */
export function consumeLinkToken(): boolean {
  const m = /^#token=([^&]+)/.exec(location.hash);
  if (!m) return false;
  setToken(decodeURIComponent(m[1]));
  history.replaceState(history.state, "", location.pathname + location.search);
  return true;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  const t = getToken();
  if (t) h.set("Authorization", `Bearer ${t}`);
  return h;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const body = init.body && typeof init.body !== "string" && !(init.body instanceof FormData)
    ? JSON.stringify(init.body)
    : init.body;
  const h = headers(init.headers);
  if (body && typeof body === "string") h.set("Content-Type", "application/json");
  const res = await fetch(apiUrl(path), { ...init, body, headers: h });
  if (res.status === 401) onUnauthorized?.();
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const get = <T>(p: string) => api<T>(p);
export const post = <T>(p: string, body?: unknown) =>
  api<T>(p, { method: "POST", body: body === undefined ? undefined : (body as BodyInit) });
export const patch = <T>(p: string, body: unknown) => api<T>(p, { method: "PATCH", body: body as BodyInit });
export const del = <T>(p: string) => api<T>(p, { method: "DELETE" });

export function fileUrl(attachmentId: string, opts: { download?: boolean } = {}): string {
  // Same origin: the login cookie authorizes <img>/<video> loads (no token in the URL).
  const q = new URLSearchParams();
  if (opts.download) q.set("download", "1");
  const qs = q.toString();
  return apiUrl(`/attachments/${attachmentId}/file`) + (qs ? `?${qs}` : "");
}

/** Upload with progress (fetch has no upload progress; XHR does). */
export function upload(
  file: Blob,
  filename: string,
  opts: { conversationId?: string | null; source?: string; onProgress?: (f: number) => void; signal?: AbortSignal },
): Promise<import("./types").Attachment> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, filename);
    if (opts.conversationId) form.append("conversation_id", opts.conversationId);
    form.append("source", opts.source ?? "upload");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl("/attachments"));
    const t = getToken();
    if (t) xhr.setRequestHeader("Authorization", `Bearer ${t}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && opts.onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
      else reject(new ApiError(xhr.status, xhr.responseText || xhr.statusText));
    };
    xhr.onerror = () => reject(new ApiError(0, "network error"));
    opts.signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(form);
  });
}

/** Parse an SSE byte stream into events. Handles frames split across chunks. */
export async function* parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    for (;;) {
      const sep = buf.search(/\r?\n\r?\n/);
      if (sep === -1) break;
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep).replace(/^\r?\n\r?\n/, "");
      const ev = parseFrame(frame);
      if (ev) yield ev;
    }
  }
  const tail = parseFrame(buf);
  if (tail) yield tail;
}

export function parseFrame(frame: string): StreamEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith(":")) continue; // comment / ping
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) } as StreamEvent;
  } catch {
    return null;
  }
}

/** POST that returns an SSE stream of reply events. */
export async function* streamPost(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
  const res = await fetch(apiUrl(path), {
    method: "POST",
    headers: headers({ "Content-Type": "application/json", Accept: "text/event-stream" }),
    body: JSON.stringify(body),
    signal,
  });
  if (res.status === 401) onUnauthorized?.();
  if (!res.ok || !res.body) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  yield* parseSSE(res.body);
}
