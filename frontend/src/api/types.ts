// Shapes returned by the backend (orchestrator/api.py).

export interface Conversation {
  id: string;
  title: string | null;
  project_id: string | null;
  starred: number;
  model: string | null;
  style: string | null;
  active_leaf_id: string | null;
  created_at: number;
  updated_at: number;
  incognito?: boolean;
}

export type Block =
  | { type: "thinking"; text: string }
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; arguments: unknown; decision: string }
  | {
      type: "tool_result";
      id: string;
      name: string;
      ok: boolean;
      status: string;
      content: string;
      data: Record<string, unknown>;
      files: FileRef[];
      duration_ms: number;
    };

export interface FileRef {
  id: string | null;
  filename: string;
  size: number;
}

export interface Citation {
  index: number;
  title: string;
  url?: string;
  attachment_id?: string;
  conversation_id?: string;
  locator?: string;
  snippet?: string;
  implicit?: boolean;
}

export interface Message {
  id: string;
  conversation_id: string;
  parent_id: string | null;
  role: "user" | "assistant";
  content: string;
  blocks: Block[];
  citations: Citation[];
  model: string | null;
  status: "streaming" | "complete" | "stopped" | "error";
  pinned: number;
  usage: Usage | null;
  created_at: number;
  attachment_ids: string[];
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  ttft_s: number | null;
  decode_tok_s: number | null;
  iterations?: number;
}

export interface Attachment {
  id: string;
  filename: string;
  mime: string | null;
  size: number;
  kind: string | null;
  source: string;
  conversation_id: string | null;
  voice_note?: string | null;
  created_at: number;
}

export interface Artifact {
  id: string;
  identifier: string;
  type: string;
  title: string;
  version?: number;
  latest?: number;
  language?: string;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: Message[];
  active_path: string[];
  attachments: Record<string, Attachment>;
  artifacts: Artifact[];
  running: boolean;
}

export interface ModelInfo {
  id: string;
  display_name: string;
  loaded: boolean;
  installed?: boolean;
  memory_gb: number | null;
  est_memory_gb: number;
  context_length: number;
  capabilities: { image: boolean; audio: boolean; video: boolean; tools: boolean; thinking: boolean };
  speed: { decode_tok_s: number | null; ttft_s: number | null; n: number } | null;
}

export interface ModelStatus {
  budget_gb: number;
  llms: ModelInfo[];
  services: { id: string; kind: string; loaded: boolean; est_memory_gb: number }[];
  models_total_gb: number;
  defaults: { llm: string; audio_listener?: string; video?: string };
  system: { total_gb: number; available_gb: number; used_gb: number };
  video?: VideoModel[];
}

export interface VideoModel {
  id: string;
  display_name: string;
  installed: boolean;
  reason: string;
  est_memory_gb: number;
  max_seconds: number;
  default_max_seconds: number;
  settable_max_seconds: number;   // the longest Settings may choose on this Mac (memory budget)
  ceiling_seconds: number;        // the longest the model allows at all
  tested_seconds: number | null;
  unloads_chat_from_seconds: number | null;  // clips this long unload the chat model while rendering
  estimate: { base_seconds: number; base_gb: number; gb_per_s: number; base_render_s: number | null; render_s_per_s: number | null };
  audio: boolean;
  image_input: boolean;
  default: boolean;
  running: boolean;
}

export interface SearchResult {
  titles: Conversation[];
  semantic: { conversation: Conversation; snippets: { message_id: string; text: string; role?: string }[] }[];
}

export interface Project {
  id: string;
  name: string;
  instructions: string;
}

// SSE events streamed while a reply is generated.
export type StreamEvent =
  | { event: "message_start"; data: { conversation_id: string; user_message: Partial<Message> & { id: string }; assistant_message_id: string; model: string; incognito: boolean } }
  | { event: "status"; data: { stage: string; message: string } }
  | { event: "media_progress"; data: { attachment_id: string; stage: string; fraction: number; message: string } }
  | { event: "media_warning"; data: { attachment_id: string; warnings: string[] } }
  | { event: "prompt_ready"; data: { message_id: string; prompt_tokens: number } }
  | { event: "thinking_delta"; data: { text: string } }
  | { event: "text_delta"; data: { text: string } }
  | { event: "text_replace"; data: { text: string } }
  | { event: "tool_call"; data: { id: string; name: string; arguments: unknown; status: "pending" | "running" } }
  | { event: "tool_confirmation"; data: { id: string; name: string; arguments: unknown; reason: string } }
  | { event: "tool_progress"; data: { id: string; name: string; stage: string; step?: number; total?: number; elapsed_s?: number } }
  | { event: "tool_result"; data: { id: string; name: string; ok: boolean; status: string; decision: string; duration_ms: number; preview: string; data: Record<string, unknown>; files: FileRef[] } }
  | { event: "citation"; data: Citation }
  | { event: "artifact"; data: Artifact }
  | { event: "usage"; data: Usage }
  | { event: "title"; data: { conversation_id: string; title: string } }
  | { event: "error"; data: { message: string; status?: number } }
  | { event: "message_end"; data: { message_id: string; status: string; finish_reason: string } };
