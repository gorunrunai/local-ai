import { create } from "zustand";
import { upload } from "../api/client";
import type { Attachment } from "../api/types";
import type { DraftAttachment } from "../components/AttachmentView";
import { useChat } from "./chat";

interface DraftState {
  text: string;
  attachments: DraftAttachment[];
  thinking: boolean;
  focusComposer: () => void;
  setText: (t: string) => void;
  setThinking: (v: boolean) => void;
  addFiles: (files: File[], source?: string) => Promise<void>;
  addExisting: (att: Attachment) => void;
  remove: (key: string) => void;
  clear: () => void;
  registerFocus: (fn: () => void) => void;
}

let counter = 0;

export const useDraft = create<DraftState>((set, get) => ({
  text: "",
  attachments: [],
  thinking: false,
  focusComposer: () => undefined,
  setText: (text) => set({ text }),
  setThinking: (thinking) => set({ thinking }),
  registerFocus: (fn) => set({ focusComposer: fn }),

  addFiles: async (files, source = "upload") => {
    if (!files.length) return;
    const chat = useChat.getState();
    // Incognito uploads must land in the temporary store, which needs the chat id up front.
    const conversationId = chat.incognito || chat.current ? await chat.ensureConversation() : null;
    for (const file of files) {
      const key = `d${++counter}`;
      const visual = file.type.startsWith("image/") || file.type.startsWith("video/");
      const draft: DraftAttachment = { key, file, source, progress: 0, previewUrl: visual ? URL.createObjectURL(file) : undefined };
      set({ attachments: [...get().attachments, draft] });
      const patchDraft = (p: Partial<DraftAttachment>) =>
        set({ attachments: get().attachments.map((d) => (d.key === key ? { ...d, ...p } : d)) });
      upload(file, file.name, { conversationId, source, onProgress: (f) => patchDraft({ progress: f }) })
        .then((att) => patchDraft({ uploaded: att, progress: 1 }))
        .catch((e) => patchDraft({ error: e.status === 413 ? "Too large" : "Upload failed" }));
    }
  },

  addExisting: (att) => {
    if (get().attachments.some((d) => d.uploaded?.id === att.id)) return;
    const file = new File([], att.filename, { type: att.mime ?? "" });
    set({ attachments: [...get().attachments, { key: `d${++counter}`, file, source: att.source, progress: 1, uploaded: att }] });
  },

  remove: (key) => {
    const d = get().attachments.find((x) => x.key === key);
    if (d?.previewUrl) URL.revokeObjectURL(d.previewUrl);
    set({ attachments: get().attachments.filter((x) => x.key !== key) });
  },
  clear: () => {
    get().attachments.forEach((d) => d.previewUrl && URL.revokeObjectURL(d.previewUrl));
    set({ text: "", attachments: [] });
  },
}));
