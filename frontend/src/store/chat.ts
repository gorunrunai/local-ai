import { create } from "zustand";
import { ApiError, del, get, patch, post, streamPost } from "../api/client";
import type { Attachment, Conversation, ConversationDetail, ModelStatus, Project, StreamEvent } from "../api/types";
import { applyEvent, emptyLive, type LiveReply } from "../lib/live";

export interface PendingUser {
  text: string;
  attachments: Attachment[];
  parentId: string | null | undefined;
}

interface ChatState {
  conversations: Conversation[];
  projects: Project[];
  models: ModelStatus | null;
  current: ConversationDetail | null;
  live: LiveReply | null;
  pendingUser: PendingUser | null;
  sending: boolean;
  incognito: boolean;
  toast: string | null;
  navigateTo: string | null;
  abort: AbortController | null;

  loadConversations: () => Promise<void>;
  loadProjects: () => Promise<void>;
  loadModels: () => Promise<void>;
  openConversation: (id: string) => Promise<void>;
  refreshCurrent: () => Promise<void>;
  newChat: (opts?: { incognito?: boolean }) => void;
  ensureConversation: () => Promise<string>;
  send: (text: string, attachments: Attachment[], opts?: { thinking?: boolean; parentId?: string | null; model?: string }) => Promise<void>;
  regenerate: (messageId: string, opts?: { model?: string; thinking?: boolean }) => Promise<void>;
  switchVersion: (messageId: string) => Promise<void>;
  stop: () => Promise<void>;
  decideTool: (callId: string, approve: boolean) => Promise<void>;
  rename: (id: string, title: string) => Promise<void>;
  star: (id: string, starred: boolean) => Promise<void>;
  remove: (id: string) => Promise<void>;
  setConversationFields: (fields: Partial<Pick<Conversation, "model" | "style" | "project_id">>) => Promise<void>;
  setIncognito: (on: boolean) => void;
  showToast: (msg: string | null) => void;
  consumeNavigation: () => string | null;
}

export const useChat = create<ChatState>((set, getState) => ({
  conversations: [],
  projects: [],
  models: null,
  current: null,
  live: null,
  pendingUser: null,
  sending: false,
  incognito: false,
  toast: null,
  navigateTo: null,
  abort: null,

  loadConversations: async () => {
    try {
      set({ conversations: await get<Conversation[]>("/conversations?limit=200") });
    } catch (e) {
      console.warn(e);
    }
  },
  loadProjects: async () => {
    try {
      set({ projects: await get<Project[]>("/projects") });
    } catch (e) {
      console.warn(e);
    }
  },
  loadModels: async () => {
    try {
      set({ models: await get<ModelStatus>("/models") });
    } catch (e) {
      console.warn(e);
    }
  },

  openConversation: async (id) => {
    if (getState().current?.conversation.id === id && !getState().sending) return getState().refreshCurrent();
    try {
      const detail = await get<ConversationDetail>(`/conversations/${id}`);
      set({ current: detail, incognito: !!detail.conversation.incognito, live: getState().sending ? getState().live : null });
    } catch (e) {
      set({ toast: e instanceof ApiError && e.status === 404 ? "That chat no longer exists." : String(e), navigateTo: "/" });
    }
  },
  refreshCurrent: async () => {
    const id = getState().current?.conversation.id;
    if (!id) return;
    set({ current: await get<ConversationDetail>(`/conversations/${id}`) });
  },

  newChat: (opts) => {
    if (getState().sending) getState().abort?.abort();
    set({ current: null, live: null, pendingUser: null, incognito: opts?.incognito ?? false });
  },

  ensureConversation: async () => {
    const cur = getState().current;
    if (cur) return cur.conversation.id;
    const conv = await post<Conversation>("/conversations", { incognito: getState().incognito });
    set({ current: { conversation: conv, messages: [], active_path: [], attachments: {}, artifacts: [], running: false } });
    return conv.id;
  },

  send: async (text, attachments, opts = {}) => {
    const cid = await getState().ensureConversation();
    const body = { text, attachment_ids: attachments.map((a) => a.id), thinking: !!opts.thinking, parent_id: opts.parentId, model: opts.model };
    set({ pendingUser: { text, attachments, parentId: opts.parentId } });
    await runStream(set, getState, `/conversations/${cid}/messages`, body);
  },

  regenerate: async (messageId, opts = {}) => {
    set({ pendingUser: null });
    await runStream(set, getState, `/messages/${messageId}/regenerate`, { model: opts.model, thinking: !!opts.thinking });
  },

  switchVersion: async (messageId) => {
    const id = getState().current?.conversation.id;
    if (!id || getState().sending) return;
    await post(`/conversations/${id}/switch`, { message_id: messageId });
    await getState().refreshCurrent();
  },

  stop: async () => {
    const id = getState().current?.conversation.id ?? getState().live?.conversationId;
    if (id) await post(`/conversations/${id}/stop`).catch(() => undefined);
  },

  decideTool: async (callId, approve) => {
    await post(`/tool-calls/${callId}/decision`, { approve }).catch((e) => set({ toast: String(e) }));
  },

  rename: async (id, title) => {
    await patch(`/conversations/${id}`, { title });
    await getState().loadConversations();
    if (getState().current?.conversation.id === id) await getState().refreshCurrent();
  },
  star: async (id, starred) => {
    await patch(`/conversations/${id}`, { starred });
    await getState().loadConversations();
    if (getState().current?.conversation.id === id) await getState().refreshCurrent();
  },
  remove: async (id) => {
    await del(`/conversations/${id}`);
    if (getState().current?.conversation.id === id) set({ current: null, navigateTo: "/" });
    await getState().loadConversations();
  },
  setConversationFields: async (fields) => {
    const cid = await getState().ensureConversation();
    await patch(`/conversations/${cid}`, fields);
    await getState().refreshCurrent();
  },

  // Toggling incognito always starts a fresh chat in that mode.
  setIncognito: (on) => set({ incognito: on, current: null, live: null, pendingUser: null }),
  showToast: (msg) => set({ toast: msg }),
  consumeNavigation: () => {
    const to = getState().navigateTo;
    if (to) set({ navigateTo: null });
    return to;
  },
}));

type Set = (partial: Partial<ChatState>) => void;

async function runStream(set: Set, getState: () => ChatState, path: string, body: unknown) {
  if (getState().sending) return;
  const abort = new AbortController();
  set({ sending: true, abort, live: emptyLive() });
  let convId = getState().current?.conversation.id ?? null;
  try {
    for await (const ev of streamPost(path, body, abort.signal)) {
      handleSideEffects(set, getState, ev);
      if (ev.event === "message_start") convId = ev.data.conversation_id;
      set({ live: applyEvent(getState().live ?? emptyLive(), ev) });
    }
  } catch (e) {
    if (!(e instanceof DOMException && e.name === "AbortError")) {
      const msg = e instanceof ApiError ? e.message : String(e);
      set({ toast: msg, live: { ...(getState().live ?? emptyLive()), error: msg, done: true } });
    }
  } finally {
    set({ sending: false, abort: null });
    if (convId) {
      try {
        const detail = await get<ConversationDetail>(`/conversations/${convId}`);
        if (!getState().current || getState().current?.conversation.id === convId) set({ current: detail });
      } catch {
        /* conversation deleted mid-stream */
      }
    }
    set({ live: null, pendingUser: null });
    getState().loadConversations();
  }
}

function handleSideEffects(set: Set, getState: () => ChatState, ev: StreamEvent) {
  if (ev.event === "message_start" && !getState().current?.messages.length && !ev.data.incognito) {
    set({ navigateTo: `/c/${ev.data.conversation_id}` });
  }
  if (ev.event === "title") {
    const cur = getState().current;
    if (cur && cur.conversation.id === ev.data.conversation_id) {
      set({ current: { ...cur, conversation: { ...cur.conversation, title: ev.data.title } } });
    }
    getState().loadConversations();
  }
}
