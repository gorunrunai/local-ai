-- Conversations form a tree: every message points at its parent. Editing a user message or
-- regenerating a reply adds a sibling; `active_leaf_id` marks the branch being shown.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    starred INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    style TEXT,
    active_leaf_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS conv_updated ON conversations(updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL DEFAULT '',        -- user text, or the assistant's final text
    blocks TEXT NOT NULL DEFAULT '[]',       -- JSON: assistant steps (thinking, tool_use, tool_result, text)
    citations TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    status TEXT NOT NULL DEFAULT 'complete', -- streaming | complete | stopped | error
    pinned INTEGER NOT NULL DEFAULT 0,
    usage TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS msg_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS msg_parent ON messages(parent_id);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime TEXT,
    size INTEGER NOT NULL,
    path TEXT NOT NULL,
    kind TEXT,
    source TEXT NOT NULL DEFAULT 'upload',
    conversation_id TEXT,
    voice_note TEXT,              -- Gemma's transcript + tone note for voice messages
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS att_conv ON attachments(conversation_id);

CREATE TABLE IF NOT EXISTS message_attachments (
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    attachment_id TEXT NOT NULL REFERENCES attachments(id),
    ord INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (message_id, attachment_id)
);

CREATE TABLE IF NOT EXISTS project_files (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    attachment_id TEXT NOT NULL REFERENCES attachments(id),
    PRIMARY KEY (project_id, attachment_id)
);

CREATE TABLE IF NOT EXISTS tool_events (
    id TEXT PRIMARY KEY,
    message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    args TEXT NOT NULL,
    result TEXT,
    status TEXT NOT NULL,          -- ok | error | denied
    decision TEXT NOT NULL,        -- allow | confirm-approved | confirm-denied | deny
    started_at REAL NOT NULL,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    source_conversation_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Retrieval chunks: project files, long attachments, and past messages (chat search).
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    source_type TEXT NOT NULL,     -- project_file | attachment | message
    source_id TEXT NOT NULL,
    project_id TEXT,
    conversation_id TEXT,
    ord INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS chunk_src ON chunks(source_type, source_id);
CREATE INDEX IF NOT EXISTS chunk_proj ON chunks(project_id);
CREATE INDEX IF NOT EXISTS chunk_conv ON chunks(conversation_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    identifier TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (conversation_id, identifier)
);
CREATE TABLE IF NOT EXISTS artifact_versions (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    message_id TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (artifact_id, version)
);

CREATE TABLE IF NOT EXISTS summaries (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    upto_message_id TEXT NOT NULL,  -- summary covers the path up to and including this message
    text TEXT NOT NULL,
    tokens INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (conversation_id, upto_message_id)
);

CREATE TABLE IF NOT EXISTS prompt_debug (
    message_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
