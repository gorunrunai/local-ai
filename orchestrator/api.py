"""HTTP API. Streaming endpoints use Server-Sent Events (event name + JSON data)."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from inference.types import ChatRequest, TextDelta
from media.detect import detect_kind
from media.types import Attachment
from orchestrator.chat import AppState, ChatService, TurnRequest
from orchestrator.pipeline import STYLES
from orchestrator.settings import DEFAULT_PREFS
from orchestrator.storage.files import FileTooLarge
from orchestrator.voice_note import preload_listener

router = APIRouter(prefix="/api")


def state(request: Request) -> AppState:
    return request.app.state.app_state


def _sse(gen):
    async def stream():
        async for event, data in gen:
            yield {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)}
    return EventSourceResponse(stream(), ping=15)


def _public_att(a: dict) -> dict:
    return {k: a[k] for k in ("id", "filename", "mime", "size", "kind", "source", "conversation_id",
                              "voice_note", "created_at") if k in a}


# --- health / models -------------------------------------------------------------------------
@router.get("/health")
async def health() -> dict:
    return {"ok": True}


@router.get("/models")
async def models(request: Request) -> dict:
    return await state(request).manager.status()


@router.post("/models/{model_id}/load")
async def load_model(model_id: str, request: Request) -> dict:
    await state(request).manager.ensure_llm(model_id)
    return {"loaded": model_id}


@router.post("/models/{model_id}/unload")
async def unload_model(model_id: str, request: Request) -> dict:
    await state(request).manager.unload_llm(model_id)
    return {"unloaded": model_id}


@router.post("/models/preload-listener")
async def preload(request: Request) -> dict:
    """Called by the UI when the mic is pressed or audio is dropped."""
    s = state(request)
    s.spawn(preload_listener(s.manager))
    return {"started": True}


# --- attachments / speech ----------------------------------------------------------------------
@router.post("/attachments")
async def upload(request: Request, file: UploadFile = File(...), conversation_id: str | None = Form(None),
                 source: str = Form("upload")) -> dict:
    s = state(request)
    try:
        path, sha, size = await s.files_for(conversation_id).save_upload(file)
    except FileTooLarge as e:
        raise HTTPException(413, str(e)) from e
    filename = Path(file.filename or f"upload{path.suffix}").name
    kind = detect_kind(Attachment(path, filename=filename, mime=file.content_type, source=source, sha256=sha))
    row = await s.store_for(conversation_id).add_attachment(
        sha256=sha, filename=filename, mime=file.content_type, size=size, path=str(path), kind=str(kind),
        source=source, conversation_id=conversation_id)
    return _public_att(row)


async def _find_att(s: AppState, aid: str) -> dict:
    for store in (s.persistent, s.ephemeral):
        if row := await store.get_attachment(aid):
            return row
    raise HTTPException(404, "attachment not found")


@router.get("/attachments/{aid}")
async def attachment_meta(aid: str, request: Request) -> dict:
    return _public_att(await _find_att(state(request), aid))


@router.get("/attachments/{aid}/file")
async def attachment_file(aid: str, request: Request, download: bool = False):
    row = await _find_att(state(request), aid)
    if not row["path"] or not Path(row["path"]).exists():
        raise HTTPException(410, "This file was cleared to free up space (Settings → Clear clutter).")
    return FileResponse(row["path"], media_type=row["mime"] or None, filename=row["filename"],
                        content_disposition_type="attachment" if download else "inline")


@router.post("/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...)) -> dict:
    """Dictation: audio in, text out. Nothing is stored."""
    s = state(request)
    path, _, _ = await s.tmp_files.save_upload(file)
    try:
        tr = await s.manager.stt().transcribe(path)
    finally:
        path.unlink(missing_ok=True)
    return tr.to_dict()


class TTSBody(BaseModel):
    text: str = Field(max_length=20_000)
    voice: str | None = None
    speed: float = 1.0


@router.post("/tts")
async def tts(body: TTSBody, request: Request) -> Response:
    """Read aloud: returns one WAV for the whole text (voice mode streams sentence by sentence)."""
    import numpy as np
    import soundfile as sf

    chunks = [c async for c in state(request).manager.tts().stream(body.text, body.voice, body.speed)]
    if not chunks:
        raise HTTPException(400, "nothing to say")
    buf = io.BytesIO()
    sf.write(buf, np.concatenate([c.samples for c in chunks]), chunks[0].sample_rate, format="WAV",
             subtype="PCM_16")
    return Response(buf.getvalue(), media_type="audio/wav")


@router.websocket("/voice")
async def voice(ws: WebSocket, mode: Literal["conversation", "dictation"] = "conversation",
                conversation_id: str | None = None, voice: str | None = None, speed: float = 1.0,
                sensitivity: float | None = None):
    """Full-duplex voice (see orchestrator/voice/session.py for the protocol)."""
    from orchestrator.auth import load_or_create_token, websocket_allowed
    from orchestrator.voice.session import VoiceSession

    s: AppState = ws.app.state.app_state
    if not websocket_allowed(ws, ws.app.state.remote.enabled, load_or_create_token(s.settings.auth_token_file)):
        await ws.close(code=4403)
        return
    await ws.accept()
    prefs = (await s.prefs()).get("voice", {})
    session = VoiceSession(s, ws, mode=mode, conversation_id=conversation_id or None,
                           voice=voice or prefs.get("tts_voice"), speed=speed or prefs.get("speed", 1.0),
                           sensitivity=sensitivity if sensitivity is not None else prefs.get("vad_sensitivity", 0.5))
    await session.run()


# --- conversations --------------------------------------------------------------------------
class NewConversation(BaseModel):
    project_id: str | None = None
    model: str | None = None
    style: str | None = None
    incognito: bool = False


@router.get("/conversations")
async def list_conversations(request: Request, project_id: str | None = None, starred: bool | None = None,
                             q: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
    return await state(request).persistent.list_conversations(project_id, starred, q, limit, offset)


@router.post("/conversations")
async def create_conversation(body: NewConversation, request: Request) -> dict:
    s = state(request)
    store = s.ephemeral if body.incognito else s.persistent
    return await store.create_conversation(body.project_id, body.model, body.style)


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, request: Request) -> dict:
    s = state(request)
    store = s.store_for(cid)
    conv = await store.get_conversation(cid)
    if not conv:
        raise HTTPException(404, "conversation not found")
    messages = await store.all_messages(cid)
    path = await store.active_path(cid)
    atts = {a["id"]: _public_att(a) for a in await store.conversation_attachments(cid)}
    for m in messages:
        for aid in m.get("attachment_ids", []):
            if aid not in atts and (row := await store.get_attachment(aid)):
                atts[aid] = _public_att(row)
    return {"conversation": conv, "messages": messages, "active_path": [m["id"] for m in path],
            "attachments": atts, "artifacts": await store.list_artifacts(cid),
            "running": cid in s.runs}


class PatchConversation(BaseModel):
    title: str | None = None
    starred: bool | None = None
    project_id: str | None = None
    model: str | None = None
    style: str | None = None


@router.patch("/conversations/{cid}")
async def patch_conversation(cid: str, body: PatchConversation, request: Request) -> dict:
    store = state(request).store_for(cid)
    fields = body.model_dump(exclude_unset=True)
    if "starred" in fields:
        fields["starred"] = int(fields["starred"])
    conv = await store.update_conversation(cid, **fields)
    if not conv:
        raise HTTPException(404, "conversation not found")
    return conv


@router.delete("/conversations/{cid}")
async def delete_conversation(cid: str, request: Request) -> dict:
    await state(request).store_for(cid).delete_conversation(cid)
    return {"deleted": cid}


class SendBody(BaseModel):
    text: str = ""
    attachment_ids: list[str] = Field(default_factory=list)
    parent_id: str | None = None      # "" = new root (edit of the first message); None = active leaf
    model: str | None = None
    thinking: bool = False


@router.post("/conversations/{cid}/messages")
async def send_message(cid: str, body: SendBody, request: Request):
    s = state(request)
    if cid in s.runs:
        raise HTTPException(409, "a reply is already being generated in this chat")
    if not body.text.strip() and not body.attachment_ids:
        raise HTTPException(400, "empty message")
    return _sse(ChatService(s).run_turn(TurnRequest(cid, body.text, body.attachment_ids, body.parent_id,
                                                    None, body.model, body.thinking)))


class RegenerateBody(BaseModel):
    model: str | None = None
    thinking: bool = False


@router.post("/messages/{mid}/regenerate")
async def regenerate(mid: str, body: RegenerateBody, request: Request):
    """New assistant reply (sibling) for the user message `mid` or for the parent of assistant `mid`."""
    s = state(request)
    for store in (s.persistent, s.ephemeral):
        if msg := await store.get_message(mid):
            break
    else:
        raise HTTPException(404, "message not found")
    user_id = msg["id"] if msg["role"] == "user" else msg["parent_id"]
    cid = msg["conversation_id"]
    if cid in s.runs:
        raise HTTPException(409, "a reply is already being generated in this chat")
    return _sse(ChatService(s).run_turn(TurnRequest(cid, regenerate_user_message_id=user_id,
                                                    model=body.model, thinking=body.thinking)))


class SwitchBody(BaseModel):
    message_id: str


@router.post("/conversations/{cid}/switch")
async def switch_branch(cid: str, body: SwitchBody, request: Request) -> dict:
    """Show another version: activates the newest leaf under `message_id`."""
    store = state(request).store_for(cid)
    msg = await store.get_message(body.message_id)
    if not msg or msg["conversation_id"] != cid:
        raise HTTPException(404, "message not found")
    leaf = await store.latest_leaf(body.message_id)
    await store.update_conversation(cid, active_leaf_id=leaf)
    return {"active_leaf_id": leaf, "active_path": [m["id"] for m in await store.path_to(leaf)]}


@router.post("/conversations/{cid}/stop")
async def stop(cid: str, request: Request) -> dict:
    ev = state(request).runs.get(cid)
    if ev:
        ev.set()
    return {"stopping": bool(ev)}


class PatchMessage(BaseModel):
    pinned: bool


@router.patch("/messages/{mid}")
async def patch_message(mid: str, body: PatchMessage, request: Request) -> dict:
    s = state(request)
    for store in (s.persistent, s.ephemeral):
        if await store.get_message(mid):
            await store.update_message(mid, pinned=int(body.pinned))
            return await store.get_message(mid)
    raise HTTPException(404, "message not found")


@router.get("/messages/{mid}/prompt")
async def prompt_debug(mid: str, request: Request) -> dict:
    """The exact prompt sent to the model for this reply (media shown as placeholders)."""
    s = state(request)
    for store in (s.persistent, s.ephemeral):
        if dbg := await store.get_prompt_debug(mid):
            return dbg
    raise HTTPException(404, "no prompt recorded for this message")


@router.get("/messages/{mid}/tool-events")
async def tool_events(mid: str, request: Request) -> list[dict]:
    s = state(request)
    return await s.store_for(mid).tool_events(mid) or await s.ephemeral.tool_events(mid)


class Decision(BaseModel):
    approve: bool


@router.post("/tool-calls/{call_id}/decision")
async def decide(call_id: str, body: Decision, request: Request) -> dict:
    if not state(request).confirmations.resolve(call_id, body.approve):
        raise HTTPException(404, "no pending confirmation with that id")
    return {"id": call_id, "approved": body.approve}


@router.get("/conversations/{cid}/export")
async def export(cid: str, request: Request, format: Literal["md", "json"] = "md"):
    store = state(request).store_for(cid)
    conv = await store.get_conversation(cid)
    if not conv:
        raise HTTPException(404, "conversation not found")
    path = await store.active_path(cid)
    if format == "json":
        return {"conversation": conv, "messages": path}
    lines = [f"# {conv.get('title') or 'Conversation'}", ""]
    for m in path:
        who = "You" if m["role"] == "user" else f"Assistant ({m.get('model') or ''})"
        lines += [f"## {who}", "", m["content"] or "", ""]
        for c in m.get("citations") or []:
            lines.append(f"- [{c['index']}] {c.get('title', '')} {c.get('url', '')}".rstrip())
        if m.get("citations"):
            lines.append("")
    name = (conv.get("title") or "conversation").replace("/", "-")[:60]
    return PlainTextResponse("\n".join(lines), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


# --- search -----------------------------------------------------------------------------------
@router.get("/search")
async def search(q: str, request: Request, project_id: str | None = None) -> dict:
    s = state(request)
    titles = await s.persistent.list_conversations(project_id=project_id, query=q, limit=20)
    hits = await s.retriever_for(s.persistent).search(q, {"source_type": "message"}, k=12, rerank=False)
    by_conv: dict[str, dict] = {}
    for h in hits:
        conv = await s.persistent.get_conversation(h.conversation_id) if h.conversation_id else None
        if not conv or (project_id and conv.get("project_id") != project_id):
            continue
        entry = by_conv.setdefault(conv["id"], {"conversation": conv, "snippets": []})
        entry["snippets"].append({"message_id": h.source_id, "text": h.text[:300], "role": h.meta.get("role")})
    return {"titles": titles, "semantic": list(by_conv.values())}


# --- projects ------------------------------------------------------------------------------------
class ProjectBody(BaseModel):
    name: str
    instructions: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    instructions: str | None = None


@router.get("/projects")
async def list_projects(request: Request) -> list[dict]:
    return await state(request).persistent.list_projects()


@router.post("/projects")
async def create_project(body: ProjectBody, request: Request) -> dict:
    return await state(request).persistent.create_project(body.name, body.instructions)


@router.get("/projects/{pid}")
async def get_project(pid: str, request: Request) -> dict:
    s = state(request)
    proj = await s.persistent.get_project(pid)
    if not proj:
        raise HTTPException(404, "project not found")
    return {**proj, "files": [_public_att(a) for a in await s.persistent.project_files(pid)],
            "conversations": await s.persistent.list_conversations(project_id=pid)}


@router.patch("/projects/{pid}")
async def patch_project(pid: str, body: ProjectPatch, request: Request) -> dict:
    proj = await state(request).persistent.update_project(pid, **body.model_dump(exclude_unset=True))
    if not proj:
        raise HTTPException(404, "project not found")
    return proj


@router.delete("/projects/{pid}")
async def delete_project(pid: str, request: Request) -> dict:
    await state(request).persistent.delete_project(pid)
    return {"deleted": pid}


class ProjectFile(BaseModel):
    attachment_id: str


@router.post("/projects/{pid}/files")
async def add_project_file(pid: str, body: ProjectFile, request: Request) -> dict:
    s = state(request)
    store = s.persistent
    att = await store.get_attachment(body.attachment_id)
    if not att or not await store.get_project(pid):
        raise HTTPException(404, "project or attachment not found")
    await store.add_project_file(pid, att["id"])
    spec = s.manager.llm_spec()
    from orchestrator.pipeline import _att

    prepared = await s.router.prepare(_att(att), spec)
    text = "\n\n".join(c.text for c in prepared.context)
    chunks = await s.retriever_for(store).index("project_file", att["id"], [("", text)], project_id=pid,
                                               extra_meta={"filename": att["filename"]}, replace=True)
    return {"attachment": _public_att(att), "chunks": chunks, "chars": len(text)}


@router.delete("/projects/{pid}/files/{aid}")
async def remove_project_file(pid: str, aid: str, request: Request) -> dict:
    await state(request).persistent.remove_project_file(pid, aid)
    return {"removed": aid}


# --- memory ------------------------------------------------------------------------------------
class MemoryBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.get("/memories")
async def list_memories(request: Request) -> list[dict]:
    return await state(request).persistent.list_memories()


@router.post("/memories")
async def add_memory(body: MemoryBody, request: Request) -> dict:
    s = state(request)
    return await s.memory_for(s.persistent).save(body.text, None)


@router.patch("/memories/{mid}")
async def edit_memory(mid: str, body: MemoryBody, request: Request) -> dict:
    s = state(request)
    mem = await s.memory_for(s.persistent).update(mid, body.text)
    if not mem:
        raise HTTPException(404, "memory not found")
    return mem


@router.delete("/memories/{mid}")
async def delete_memory(mid: str, request: Request) -> dict:
    if not await state(request).persistent.delete_memory(mid):
        raise HTTPException(404, "memory not found")
    return {"deleted": mid}


# --- settings, styles, tools, MCP, artifacts ----------------------------------------------------
@router.get("/settings")
async def get_settings(request: Request) -> dict:
    prefs = await state(request).prefs()
    prefs.pop("custom_styles", None)
    return prefs


@router.patch("/settings")
async def patch_settings(body: dict[str, Any], request: Request) -> dict:
    s = state(request)
    unknown = set(body) - set(DEFAULT_PREFS)
    if unknown:
        raise HTTPException(400, f"unknown settings: {sorted(unknown)}")
    if "voice" in body:
        stt = (body["voice"] or {}).get("stt")
        if stt and stt not in s.manager.cfg.stt:
            raise HTTPException(400, f"unknown speech-to-text model {stt!r}")
    stored = await s.persistent.get_setting("prefs", {})
    stored.update(body)
    await s.persistent.set_setting("prefs", stored)
    s.apply_prefs(await s.prefs())
    return await get_settings(request)


@router.get("/styles")
async def list_styles(request: Request) -> dict:
    presets = {p.stem: p.read_text().strip() for p in sorted(STYLES.glob("*.md"))}
    return {"presets": presets, "custom": await state(request).persistent.get_setting("custom_styles", {})}


class StyleBody(BaseModel):
    instructions: str = Field(min_length=1, max_length=4000)


@router.put("/styles/{name}")
async def put_style(name: str, body: StyleBody, request: Request) -> dict:
    s = state(request)
    custom = await s.persistent.get_setting("custom_styles", {})
    custom[name] = body.instructions
    await s.persistent.set_setting("custom_styles", custom)
    return {"name": name, "instructions": body.instructions}


@router.delete("/styles/{name}")
async def delete_style(name: str, request: Request) -> dict:
    s = state(request)
    custom = await s.persistent.get_setting("custom_styles", {})
    custom.pop(name, None)
    await s.persistent.set_setting("custom_styles", custom)
    return {"deleted": name}


class StyleSample(BaseModel):
    name: str
    sample: str = Field(min_length=50, max_length=20_000)


@router.post("/styles/from-sample")
async def style_from_sample(body: StyleSample, request: Request) -> dict:
    s = state(request)
    req = ChatRequest(model=s.manager.cfg.defaults.llm, max_tokens=400, temperature=0.3, messages=[
        {"role": "system", "content": "Describe the writing style of the sample as instructions another "
         "writer could follow: tone, sentence length, vocabulary, structure, formatting habits. 80-150 words, "
         "second person ('Write…'). Don't quote or summarize the sample's content."},
        {"role": "user", "content": body.sample}])
    out = [ev.text async for ev in s.manager.provider.chat_stream(req) if isinstance(ev, TextDelta)]
    instructions = "".join(out).strip()
    return await put_style(body.name, StyleBody(instructions=instructions), request)


@router.get("/tools")
async def list_tools(request: Request) -> list[dict]:
    s = state(request)
    overrides = (await s.prefs()).get("tool_overrides", {})
    out = []
    for name, tool in s.registry.all().items():
        enabled, policy = s.policy.base(tool, overrides)
        out.append({"name": name, "description": tool.description, "enabled": enabled, "policy": policy,
                    "source": "mcp" if name.startswith("mcp__") else "builtin", "network": tool.network,
                    "side_effect": tool.side_effect})
    return out


class ToolPatch(BaseModel):
    enabled: bool | None = None
    policy: Literal["allow", "confirm", "deny"] | None = None


@router.patch("/tools/{name}")
async def patch_tool(name: str, body: ToolPatch, request: Request) -> dict:
    s = state(request)
    if not s.registry.get(name):
        raise HTTPException(404, "unknown tool")
    before = await _web_enabled(s) if name in WEB_TOOLS else None
    stored = await s.persistent.get_setting("prefs", {})
    overrides = stored.get("tool_overrides", {})
    overrides[name] = {**overrides.get(name, {}), **body.model_dump(exclude_none=True)}
    stored["tool_overrides"] = overrides
    await s.persistent.set_setting("prefs", stored)
    if before is not None:                      # switching web search or web fetch in Settings → Tools
        await _phone_follows_web(request, before, await _web_enabled(s))
    return {"name": name, **overrides[name]}


@router.get("/voices")
async def voices() -> list[dict]:
    """Kokoro voices available offline (downloaded by `make models`)."""
    from huggingface_hub import snapshot_download

    try:
        root = Path(snapshot_download("prince-canuma/Kokoro-82M", allow_patterns=["voices/*.safetensors"],
                                      local_files_only=True))
    except Exception:  # noqa: BLE001
        return []
    lang = {"a": "American English", "b": "British English", "e": "Spanish", "f": "French", "h": "Hindi",
            "i": "Italian", "j": "Japanese", "p": "Portuguese", "z": "Chinese"}
    out = []
    for f in sorted((root / "voices").glob("*.safetensors")):
        vid = f.stem
        out.append({"id": vid, "name": vid.split("_", 1)[-1].title(), "language": lang.get(vid[0], vid[0]),
                    "gender": {"f": "female", "m": "male"}.get(vid[1:2], "")})
    return out


class McpConfig(BaseModel):
    mcpServers: dict[str, dict[str, Any]]


@router.get("/mcp/config")
async def mcp_config(request: Request) -> dict:
    return {"mcpServers": state(request).mcp.load_config(), "path": str(state(request).mcp.config_path)}


@router.put("/mcp/config")
async def put_mcp_config(body: McpConfig, request: Request) -> list[dict]:
    s = state(request)
    for name, conf in body.mcpServers.items():
        if not isinstance(conf, dict) or not (conf.get("command") or conf.get("url")):
            raise HTTPException(400, f"server {name!r} needs a 'command' (stdio) or a 'url' (HTTP)")
        if conf.get("policy") not in (None, "allow", "confirm", "deny"):
            raise HTTPException(400, f"server {name!r}: policy must be allow, confirm or deny")
    s.mcp.config_path.write_text(json.dumps({"mcpServers": body.mcpServers}, indent=2) + "\n")
    await s.mcp.reload()
    return s.mcp.status()


def _direct_local(request: Request) -> bool:
    from orchestrator.auth import PROXY_HEADERS

    return not any(h in request.headers for h in PROXY_HEADERS)


class CleanupBody(BaseModel):
    categories: list[Literal["videos", "files", "artifacts", "temp", "my_photos", "my_videos", "my_files", "my_voice"]]
    since: float | None = None          # unix seconds; None = from the beginning
    until: float | None = None          # unix seconds, exclusive; None = until now
    dry_run: bool = True                # count first; clear only when asked


@router.post("/cleanup")
async def cleanup(body: CleanupBody, request: Request) -> dict:
    """Settings → Clear clutter: what the AI made, or your uploads. Never the text of chats or project files."""
    from orchestrator import cleanup as c

    s = state(request)
    return await c.run(s.persistent, s.settings.data_dir, list(body.categories), body.since, body.until, body.dry_run)


@router.get("/version")
async def version() -> dict:
    from orchestrator.updates import current_version

    return {"version": current_version()}


@router.get("/updates/check")
async def check_updates(request: Request) -> dict:
    """Settings → Updates. Runs only when the user asks; reads UPDATES.md from GitHub."""
    from orchestrator.updates import check, current_version

    if not await _web_enabled(state(request)):
        return {"ok": False, "current": current_version(), "web_access": False,
                "error": "Web access is off, so the app doesn't go online. Turn on Web access to check for updates."}
    return await check()


@router.get("/capabilities")
async def capabilities(request: Request) -> dict:
    """This Mac, what's installed, which features work, and their limitations (home screen)."""
    from orchestrator.capabilities import report

    s = state(request)
    return report(s.manager.cfg, web_enabled=await _web_enabled(s))


async def _web_enabled(s: AppState) -> bool:
    """The Web access switch: web search and reading web pages (the only features that go online)."""
    overrides = (await s.prefs()).get("tool_overrides", {})
    web = [t for n in WEB_TOOLS if (t := s.registry.get(n))]
    return any(s.policy.base(t, overrides)[0] for t in web)


WEB_TOOLS = ("web_search", "web_fetch")


class WebAccess(BaseModel):
    enabled: bool


@router.put("/web-access")
async def set_web_access(body: WebAccess, request: Request) -> dict:
    """The home screen's Web access switch: turns web_search and web_fetch on or off together."""
    s = state(request)
    before = await _web_enabled(s)
    stored = await s.persistent.get_setting("prefs", {})
    overrides = stored.get("tool_overrides", {})
    for name in WEB_TOOLS:
        overrides[name] = {**overrides.get(name, {}), "enabled": body.enabled}
    stored["tool_overrides"] = overrides
    await s.persistent.set_setting("prefs", stored)
    phone_before = request.app.state.remote.enabled
    note = await _phone_follows_web(request, before, body.enabled)
    remote = request.app.state.remote
    return {"enabled": body.enabled, "phone_access": remote.enabled, "phone_changed": remote.enabled != phone_before,
            "phone_paused": remote.paused_by_web, "note": note}


async def _phone_follows_web(request: Request, before: bool, after: bool) -> str | None:
    """Phone access goes off with web access, and comes back only if web access turned it off."""
    remote = request.app.state.remote
    if before and not after:
        await remote.pause_for_web_off()
    elif after and not before:
        return await remote.resume_for_web_on()
    return None


@router.get("/remote")
async def remote_status(request: Request) -> dict:
    """Phone-access status; the token is only revealed to this Mac (not to proxied clients)."""
    from orchestrator.auth import load_or_create_token

    remote = request.app.state.remote
    out = {"remote_access": remote.enabled, "tailscale": await remote.status(),
           "proxied": not _direct_local(request), "web_access": await _web_enabled(state(request)),
           "paused_by_web": remote.paused_by_web}
    if _direct_local(request):
        out["token"] = load_or_create_token(state(request).settings.auth_token_file)
    return out


class RemoteBody(BaseModel):
    enabled: bool


@router.post("/remote")
async def set_remote(body: RemoteBody, request: Request) -> dict:
    """Turn phone access on or off (only from this Mac)."""
    from orchestrator.remote import RemoteError

    if not _direct_local(request):
        raise HTTPException(403, "turn phone access on or off from the Mac itself")
    if body.enabled and not await _web_enabled(state(request)):
        raise HTTPException(409, "Web access is off, so phone access stays off. Turn on Web access first.")
    try:
        await request.app.state.remote.set_by_user(body.enabled)
    except RemoteError as e:
        raise HTTPException(400, str(e)) from e
    return await remote_status(request)


@router.post("/remote/token/rotate")
async def rotate_token(request: Request) -> dict:
    if not _direct_local(request):
        raise HTTPException(403, "rotate the token from the Mac itself")
    s = state(request)
    s.settings.auth_token_file.unlink(missing_ok=True)
    from orchestrator.auth import load_or_create_token

    token = load_or_create_token(s.settings.auth_token_file)
    return {"token": token, "note": "Devices using the old token must sign in again."}


@router.get("/mcp")
async def mcp_status(request: Request) -> list[dict]:
    return state(request).mcp.status()


@router.post("/mcp/reload")
async def mcp_reload(request: Request) -> list[dict]:
    s = state(request)
    await s.mcp.reload()
    return s.mcp.status()


class NewArtifact(BaseModel):
    identifier: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    type: Literal["html", "svg", "react", "mermaid", "markdown", "code"]
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=2_000_000)


@router.post("/conversations/{cid}/artifacts")
async def create_artifact(cid: str, body: NewArtifact, request: Request) -> dict:
    """Save content as an artifact version (e.g. 'Preview' on a code block in a reply)."""
    store = state(request).store_for(cid)
    if not await store.get_conversation(cid):
        raise HTTPException(404, "conversation not found")
    return await store.save_artifact_version(cid, body.identifier, body.type, body.title, body.content, None)


@router.get("/conversations/{cid}/artifacts")
async def list_artifacts(cid: str, request: Request) -> list[dict]:
    return await state(request).store_for(cid).list_artifacts(cid)


# Artifacts run in an opaque origin (CSP sandbox, even when opened in a new tab) with no
# network access: they can't read the app's data, call the API, or phone home.
ARTIFACT_CSP = (
    "sandbox allow-scripts allow-modals allow-forms allow-downloads; default-src 'none'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; media-src 'self' data: blob:; "
    "connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'"
)
REACT_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="/artifact-runtime/tailwind.js"></script>
<script src="/artifact-runtime/babel.min.js"></script>
<style>html,body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,sans-serif}}</style>
</head><body><div id="root"></div>
<script type="text/plain" id="artifact-source">{source}</script>
<script src="/artifact-runtime/runtime.js"></script>
</body></html>"""
SVG_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>html,body{{margin:0;height:100%;display:grid;place-items:center;background:#fff}}svg{{max-width:100%;max-height:100vh}}</style>
</head><body>{source}</body></html>"""


# HTML artifacts can't reach the network, but models often load these libraries from a CDN.
# Point such <script> tags at the copies bundled in /artifact-runtime/ so the page still works.
_SRC = r"""(<script\b[^>]*\bsrc=["'])"""  # group 1: the tag up to the URL; group 2: closing quote
_CDN_SCRIPTS = [
    (re.compile(_SRC + r"""https?://[^"']*(?:chart\.js|chart\.umd)[^"']*(["'])""", re.IGNORECASE),
     "/artifact-runtime/chart.js"),
    (re.compile(_SRC + r"""https?://[^"']*/d3(?:[@.][^/"']*)?(?:/dist/[^"']*)?(["'])""", re.IGNORECASE),
     "/artifact-runtime/d3.js"),
    (re.compile(_SRC + r"""https?://cdn\.tailwindcss\.com[^"']*(["'])""", re.IGNORECASE),
     "/artifact-runtime/tailwind.js"),
]


def localize_cdn_scripts(html: str) -> str:
    for pattern, local in _CDN_SCRIPTS:
        html = pattern.sub(lambda m, local=local: f"{m.group(1)}{local}{m.group(2)}", html)
    return html


def render_artifact_html(art_type: str, title: str, content: str) -> tuple[str, str]:
    """Return (body, media type) for viewing an artifact version in a sandboxed frame/tab."""
    import html as _html

    t = _html.escape(title)
    if art_type == "react":
        return REACT_TEMPLATE.format(title=t, source=content.replace("</script", "<\\/script")), "text/html"
    if art_type == "svg":
        return SVG_TEMPLATE.format(title=t, source=content), "text/html"
    if art_type == "html":
        content = localize_cdn_scripts(content)
        doc = content if "<html" in content.lower() else (
            f"<!doctype html><html><head><meta charset='utf-8'><title>{t}</title></head><body>{content}</body></html>")
        return doc, "text/html"
    return content, "text/plain"


@router.get("/artifacts/{aid}/view")
async def view_artifact(aid: str, request: Request, version: int | None = None) -> Response:
    s = state(request)
    for store in (s.persistent, s.ephemeral):
        if art := await store.get_artifact(aid):
            break
    else:
        raise HTTPException(404, "artifact not found")
    versions = {v["version"]: v for v in art["versions"]}
    v = versions.get(version or max(versions))
    if not v:
        raise HTTPException(404, "version not found")
    body, media = render_artifact_html(art["type"], art["title"], v["content"])
    return Response(body, media_type=f"{media}; charset=utf-8", headers={
        "Content-Security-Policy": ARTIFACT_CSP, "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/artifacts/{aid}")
async def get_artifact(aid: str, request: Request) -> dict:
    s = state(request)
    for store in (s.persistent, s.ephemeral):
        if art := await store.get_artifact(aid):
            return art
    raise HTTPException(404, "artifact not found")
