"""generate_video: render a short clip locally with LTX-2.3 or Wan 2.2 (see inference/video.py)."""

from __future__ import annotations

import contextlib
from pathlib import Path

from inference.config import ROOT, ModelsConfig, get_config
from inference.video import VideoFailed, VideoJob, VideoUnavailable, installed
from media.ffmpeg import probe
from media.images import normalize_image
from orchestrator.tools.base import Tool, ToolContext, ToolResult
from orchestrator.tools.local import _find_attachment

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif"}


async def _photo_in_request(ctx: ToolContext) -> dict | None:
    """The image attached to the user's current message, if exactly one.

    "Make a clip of this boy saying hi" with a photo attached means that photo, but the model
    sometimes leaves `image` empty; without this the clip would show a made-up person.
    """
    reply = await ctx.store.get_message(ctx.message_id)
    user = await ctx.store.get_message(reply["parent_id"]) if reply and reply.get("parent_id") else None
    photos = []
    for aid in (user or {}).get("attachment_ids", []):
        att = await ctx.store.get_attachment(aid)
        if att and Path(att["path"]).suffix.lower() in IMAGE_EXT:
            photos.append(att)
    return photos[0] if len(photos) == 1 else None


# LTX-2.3 sometimes burns garbled captions of the spoken line into the picture, and it has no negative
# prompt in distilled mode. Naming what to avoid ("no subtitles") makes it worse: with the same seed
# and photo that line produced subtitles, while describing a clean picture produced none.
NO_TEXT = " The image is clean, like raw camera footage."
WANTS_TEXT = ("text", "caption", "subtitle", "title card", "sign that says", "words on", "lettering", "logo")


def no_text(prompt: str) -> str:
    return prompt if any(w in prompt.lower() for w in WANTS_TEXT) else prompt.rstrip() + NO_TEXT


async def _chat_photos(ctx: ToolContext) -> list[dict]:
    return [a for a in await ctx.store.conversation_attachments(ctx.conversation_id)
            if Path(a["path"]).suffix.lower() in IMAGE_EXT]


def aspect_for(width: int, height: int) -> str:
    """The clip shape closest to a photo's, so it isn't cropped or squashed."""
    ratio = width / height
    return "landscape" if ratio > 1.15 else "portrait" if ratio < 1 / 1.15 else "square"


class GenerateVideo(Tool):
    name = "generate_video"

    def __init__(self, cfg: ModelsConfig | None = None):
        self.cfg = cfg or get_config()

    def _models(self) -> dict:
        # Offer only the engines this Mac has installed (install.sh can install either or both).
        return {mid: s for mid, s in self.cfg.video.items() if installed(s)[0]} or dict(self.cfg.video)

    # Built on each use, not once: Settings → Video generation can change each model's longest clip.
    @property
    def description(self) -> str:
        return (
            "Create a short video clip (a few seconds), rendered locally on this Mac, from a text description "
            "or by animating a photo attached to this chat. Use it whenever the user asks for a video, clip or "
            "animation. Photos of people work: pass the photo in `image` and it becomes the first frame, so "
            "the same person moves, and with a model that makes sound (LTX-2.3) they can speak: put the exact "
            "words in quotes in the prompt, e.g. 'The boy looks into the camera, smiles and says \"What\'s "
            "up, dawg?!\" in a cheerful voice.' Mouth movement follows the speech approximately. Write "
            "`prompt` as one detailed paragraph in English: subject, action, setting, camera movement, "
            "lighting and style; when animating a photo, describe what is in it and what should happen; for "
            "models with sound, describe the sounds and any speech. When animating a photo of a person, use about 5 "
            "seconds unless the user asks for a length: longer clips drift away from the person's likeness. "
            "The longest clip each model can make is set by the user in Settings; if they ask for longer, make "
            "the longest allowed and tell them they can raise the limit in Settings → Models → Video generation. "
            "Rendering takes one to several minutes; "
            "the clip is shown to the user when done. Call it once per clip; you will not see the result.")

    @property
    def parameters(self) -> dict:
        models = self._models()
        default = self.cfg.defaults.video if self.cfg.defaults.video in models else next(iter(models), None)
        max_s = max((s.max_seconds for s in models.values()), default=8)
        lines = [f"{mid}: {s.display_name}, up to {s.max_seconds:g}s"
                 + (" (also generates matching sound)" if s.audio else "") for mid, s in models.items()]
        return {"type": "object", "properties": {
            "prompt": {"type": "string", "description": "Detailed description of the clip"},
            "model": {"type": "string", "enum": list(models), "default": default,
                      "description": "; ".join(lines) + ". Use a model with sound whenever someone should speak."},
            "seconds": {"type": "number", "minimum": 1, "maximum": max_s, "default": min(4, max_s)},
            "aspect": {"type": "string", "enum": ["landscape", "portrait", "square"], "default": "landscape",
                       "description": "Ignored when animating a photo: the clip follows the photo's shape"},
            "image": {"type": "string", "description": "File name or id of an image attached to this chat to "
                                                       "animate; it becomes the first frame. Always pass it when "
                                                       "the user asks to animate a photo or to make someone or "
                                                       "something in it move or speak."},
            "seed": {"type": "integer", "minimum": 0, "description": "Optional: reuse to reproduce a clip"}},
            "required": ["prompt"]}

    def available(self) -> bool:
        return any(installed(spec)[0] for spec in self.cfg.video.values())

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        gen = ctx.manager.video
        root = Path(ctx.workdir_root).parent if ctx.workdir_root else ROOT / "data"
        image, image_name = None, None
        aspect = args.get("aspect") or "landscape"
        att = None
        if ref := (args.get("image") or "").strip():
            att = await _find_attachment(ctx, ref)
            if not att or Path(att["path"]).suffix.lower() not in IMAGE_EXT:
                # Models often guess a name ("image.jpg"): the photo on this message is what they mean.
                att = await _photo_in_request(ctx)
                if att is None:
                    names = [a["filename"] for a in await _chat_photos(ctx)]
                    return ToolResult(f"No image named {ref!r} in this chat. Photos in this chat: "
                                      + (", ".join(repr(n) for n in names) or "none") + ".", ok=False)
        else:
            att = await _photo_in_request(ctx)
        if att:
            # Apply the phone's EXIF rotation (engines ignore it) and drop metadata (GPS) first.
            info = normalize_image(Path(att["path"]), root / "video" / "inputs", max_side=1536,
                                   stem=Path(att["path"]).stem)
            image, image_name = Path(info.path), att["filename"]
            aspect = aspect_for(info.width, info.height)
        try:
            spec = gen.spec(args.get("model"))
        except VideoUnavailable as e:
            return ToolResult(str(e), ok=False)
        prompt = args["prompt"].strip()
        job = VideoJob(prompt=no_text(prompt), model=spec.id, seconds=float(args.get("seconds") or 4),
                       aspect=aspect, image=image, seed=args.get("seed"))

        async def progress(update: dict) -> None:
            await ctx.emit("tool_progress", {"id": ctx.call_id, "name": self.name, **update})

        try:
            res = await gen.generate(job, root / "video", progress, ctx.cancel)
        except VideoUnavailable as e:
            return ToolResult(f"Video generation is not available: {e}", ok=False,
                              data={"model": spec.id, "error": str(e)})
        except VideoFailed as e:
            if str(e) == "stopped":
                return ToolResult("The user stopped the video render.", ok=False, data={"model": spec.id})
            tail = "\n".join(e.log_tail.splitlines()[-15:])
            return ToolResult(f"Video generation failed: {e}\n{tail}", ok=False,
                              data={"model": spec.id, "error": str(e), "log": tail})

        info = await probe(res.path)
        name = f"{spec.id}-{res.seed}.mp4"
        dest, sha, size = ctx.files.save_bytes(res.path.read_bytes(), name)
        with contextlib.suppress(OSError):
            res.path.unlink()
        att = await ctx.store.add_attachment(
            sha256=sha, filename=name, mime="video/mp4", size=size, path=str(dest), kind="video",
            source="tool", conversation_id=ctx.conversation_id)
        files = [{"id": att["id"], "filename": name, "size": size, "mime": "video/mp4"}]
        sound = " with generated sound" if info.has_audio else ""
        source = f" animated from the photo {image_name}" if image_name else ""
        content = (f"Video{source} created and shown to the user: {name} ({res.width}x{res.height}, "
                   f"{info.duration_s:.1f}s at {res.fps} fps{sound}; {spec.display_name}; seed {res.seed}). "
                   f"Rendering took {res.elapsed_s:.0f}s. You haven't seen the clip, so don't describe what it "
                   f"shows; the user can ask for changes and you can render again (same seed to keep the "
                   f"composition).")
        return ToolResult(content, files=files, data={
            "model": spec.id, "model_name": spec.display_name, "prompt": prompt,
            "width": res.width, "height": res.height, "frames": res.frames, "fps": res.fps,
            "seconds": round(info.duration_s, 2), "audio": info.has_audio, "seed": res.seed,
            "elapsed_s": res.elapsed_s, "peak_gb": res.peak_gb, "unloaded": res.unloaded,
            "image": image_name, "files": files})
