"""Modality router: decide, per attachment, native pass-through vs preprocessing.

Decision table (caps/limits come from the loaded model's entry in config/models.yaml):

  kind        | native when                                  | otherwise
  ------------+----------------------------------------------+------------------------------------
  image       | caps.image                                   | OCR text only (+ warning)
  screenshot  | caps.image (+ OCR text if enabled)           | OCR text only
  audio       | caps.audio and duration <= limits.audio_s     | timestamped transcript (STT)
  video       | video_mode=native, caps.video,               | keyframes (scene + uniform, budget)
              | duration <= limits.video_s (proxy clip)      | as timestamped images + transcript
  document    | never (text is always extracted)             | text; scanned PDF pages as images+OCR

Every derived text (transcripts, OCR, document text) is returned as ContextBlocks that
render inside an <attachment_data> envelope, so the orchestrator can mark it as data.
Results are cached by content hash + parameters.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from inference.config import LLMSpec
from media import video as V
from media.audio import transcribe_cached
from media.cache import MediaCache
from media.detect import detect_kind
from media.documents import DocResult, extract_document
from media.ffmpeg import probe, require_ffmpeg
from media.images import ImageInfo, image_part, normalize_image
from media.ocr import OcrResult, ocr_image
from media.types import (
    Attachment,
    ContextBlock,
    Kind,
    PreparedAttachment,
    Progress,
    ProgressFn,
    no_progress,
)

log = logging.getLogger(__name__)
PIPELINE_VERSION = 1
MIN_OCR_CHARS = 12
MAX_DOC_CHARS = 60_000  # beyond this, the orchestrator's RAG stage takes over


@dataclass
class RouteOptions:
    video_mode: Literal["auto", "frames", "native"] = "auto"
    frame_budget: int | None = None       # None -> model limit
    scene_threshold: float = 0.3
    ocr_screenshots: bool = True
    ocr_all_images: bool = False
    strip_exif: bool = True
    transcript_with_native_audio: bool = False
    native_video_fps: float = 1.0


class ModalityRouter:
    def __init__(self, stt_provider: Callable[[], object], cache: MediaCache | None = None,
                 ocr: Callable[[str], OcrResult] = ocr_image):
        self.stt_provider = stt_provider
        self.cache = cache or MediaCache()
        self.ocr = ocr

    async def prepare_all(self, attachments: list[Attachment], spec: LLMSpec,
                          opts: RouteOptions | None = None,
                          progress: ProgressFn = no_progress) -> list[PreparedAttachment]:
        opts = opts or RouteOptions()
        prepared = []
        for att in attachments:  # sequential: STT/OCR share the GPU; keeps memory flat
            prepared.append(await self.prepare(att, spec, opts, progress))
        self._enforce_image_cap(prepared, spec)
        return prepared

    async def prepare(self, att: Attachment, spec: LLMSpec, opts: RouteOptions | None = None,
                      progress: ProgressFn = no_progress) -> PreparedAttachment:
        opts = opts or RouteOptions()
        kind = detect_kind(att)
        att.kind = kind
        t0 = time.perf_counter()
        await progress(Progress(att.id, "start", 0.0, f"Processing {att.filename}"))
        try:
            if kind in (Kind.IMAGE, Kind.SCREENSHOT):
                out = await self._image(att, kind, spec, opts)
            elif kind is Kind.AUDIO:
                out = await self._audio(att, spec, opts, progress)
            elif kind is Kind.VIDEO:
                out = await self._video(att, spec, opts, progress)
            elif kind is Kind.DOCUMENT:
                out = await self._document(att, spec, opts, progress)
            else:
                out = PreparedAttachment(att.id, att.filename, kind, "text",
                                         context=[ContextBlock(att.filename, "note",
                                                               "Unsupported file type; contents not shown.")],
                                         warnings=["unsupported file type"])
        except Exception as e:
            log.exception("preprocessing failed for %s", att.filename)
            out = PreparedAttachment(att.id, att.filename, kind, "text",
                                     context=[ContextBlock(att.filename, "note",
                                                           f"This attachment could not be processed ({e}).")],
                                     warnings=[f"processing failed: {e}"])
        out.meta.setdefault("timings", {})["total_s"] = round(time.perf_counter() - t0, 3)
        await progress(Progress(att.id, "done", 1.0, out.mode))
        return out

    # --- images ----------------------------------------------------------------------------
    async def _image(self, att: Attachment, kind: Kind, spec: LLMSpec,
                     opts: RouteOptions) -> PreparedAttachment:
        max_side = spec.limits.image_max_side
        params = {"v": PIPELINE_VERSION, "max_side": max_side, "strip": opts.strip_exif,
                  "png": kind is Kind.SCREENSHOT}
        entry = self.cache.entry_dir(att.sha256, "image", params)
        cached = self.cache.get(att.sha256, "image", params)
        hit = bool(cached and (entry / cached["file"]).exists())
        if hit:
            info = ImageInfo(**{**cached["info"], "path": str(entry / cached["file"])})
        else:
            work = self.cache.scratch(att.sha256)
            info = await asyncio.to_thread(normalize_image, att.path, work, max_side,
                                           opts.strip_exif, kind is Kind.SCREENSHOT)
            fname = Path(info.path).name
            self.cache.put(att.sha256, "image", params, {"info": info.to_dict(), "file": fname},
                           files_from=work)
            work.rmdir()
            info.path = str(entry / fname)
        out = PreparedAttachment(att.id, att.filename, kind, "native" if spec.capabilities.image else "text",
                                 meta={"image": info.to_dict()}, cached=hit)
        want_ocr = (kind is Kind.SCREENSHOT and opts.ocr_screenshots) or opts.ocr_all_images \
            or not spec.capabilities.image
        if spec.capabilities.image:
            out.parts.append(image_part(info.path))
        else:
            out.warnings.append(f"{spec.display_name} cannot see images; using OCR text only")
        if want_ocr:
            ocr = await self._ocr_cached(att.sha256, att.path)
            if len(ocr.text.strip()) >= MIN_OCR_CHARS:
                out.context.append(ContextBlock(att.filename, "ocr", ocr.text,
                                                {"note": "text recognized on-device; may contain OCR errors"}))
                out.meta["ocr_chars"] = len(ocr.text)
        return out

    async def _ocr_cached(self, sha: str, path: Path) -> OcrResult:
        params = {"v": PIPELINE_VERSION}
        if hit := self.cache.get(sha, "ocr", params):
            return OcrResult.from_dict(hit)
        res = await asyncio.to_thread(self.ocr, str(path))
        self.cache.put(sha, "ocr", params, res.to_dict())
        return res

    # --- audio ------------------------------------------------------------------------------
    async def _audio(self, att: Attachment, spec: LLMSpec, opts: RouteOptions,
                     progress: ProgressFn) -> PreparedAttachment:
        require_ffmpeg()
        info = await probe(att.path)
        dur = info.duration_s
        native = spec.capabilities.audio and 0 < dur <= spec.limits.audio_seconds
        out = PreparedAttachment(att.id, att.filename, Kind.AUDIO, "native" if native else "preprocessed",
                                 meta={"duration_s": round(dur, 2)})
        if native:
            wav, out.cached = await self._wav_cached(att)
            out.parts.append({"type": "input_audio", "input_audio": {"path": str(wav)}})
        if not native or opts.transcript_with_native_audio:
            await progress(Progress(att.id, "transcribe", 0.1, "Transcribing audio"))
            tr, hit = await transcribe_cached(att.path, att.sha256, self.stt_provider(), self.cache,
                                              dur)
            out.cached = hit
            out.context.append(ContextBlock(att.filename, "transcript", tr.timestamped(),
                                            {"duration": V.fmt_ts(dur), "engine": tr.engine}))
            out.meta["transcript_rtf"] = tr.rtf
            if not native and spec.capabilities.audio:
                out.warnings.append(f"audio longer than {spec.limits.audio_seconds:.0f}s; "
                                    "sent as transcript (tone of voice not conveyed)")
        return out

    async def _wav_cached(self, att: Attachment) -> tuple[Path, bool]:
        from media.ffmpeg import to_wav16k

        params = {"v": PIPELINE_VERSION}
        entry = self.cache.entry_dir(att.sha256, "wav16k", params)
        wav = entry / "audio.wav"
        hit = wav.exists()
        if not hit:
            work = self.cache.scratch(att.sha256)
            await to_wav16k(att.path, work / "audio.wav")
            self.cache.put(att.sha256, "wav16k", params, {"path": "audio.wav"}, files_from=work)
            work.rmdir()
        return wav, hit

    # --- video ------------------------------------------------------------------------------
    async def _video(self, att: Attachment, spec: LLMSpec, opts: RouteOptions,
                     progress: ProgressFn) -> PreparedAttachment:
        require_ffmpeg()
        timings: dict[str, float] = {}
        t = time.perf_counter()
        info = await probe(att.path)
        timings["probe_s"] = round(time.perf_counter() - t, 3)
        if not info.has_video:  # e.g. audio-only webm voice note
            att.kind = Kind.AUDIO
            return await self._audio(att, spec, opts, progress)
        dur = info.duration_s
        budget = min(opts.frame_budget or spec.limits.video_frames, spec.limits.video_frames)
        use_native = (opts.video_mode == "native" and spec.capabilities.video
                      and dur <= spec.limits.video_seconds)
        out = PreparedAttachment(att.id, att.filename, Kind.VIDEO, "native" if use_native else "preprocessed",
                                 meta={"duration_s": round(dur, 2), "width": info.width,
                                       "height": info.height, "fps": info.fps,
                                       "has_audio": info.has_audio, "timings": timings})

        async def transcript_task():
            if not info.has_audio:
                return None
            t1 = time.perf_counter()
            await progress(Progress(att.id, "transcribe", 0.05, "Transcribing audio track"))
            tr, _ = await transcribe_cached(att.path, att.sha256, self.stt_provider(), self.cache, dur)
            timings["transcribe_s"] = round(time.perf_counter() - t1, 3)
            return tr

        async def visual_task():
            t1 = time.perf_counter()
            if use_native:
                clip = await self._proxy_cached(att, spec, opts)
                timings["proxy_clip_s"] = round(time.perf_counter() - t1, 3)
                return clip, None
            frames, scenes, hit = await self._frames_cached(att, spec, opts, dur, budget, progress, timings)
            out.cached = hit
            return frames, scenes

        tr, (visual, scenes) = await asyncio.gather(transcript_task(), visual_task())

        if use_native:
            out.parts.append({"type": "input_video", "input_video": {"path": str(visual)}})
            out.meta["native_fps"] = opts.native_video_fps
        else:
            frames: list[V.Frame] = visual
            out.parts.append({"type": "text", "text":
                              f"[Video '{att.filename}', duration {V.fmt_ts(dur)}: {len(frames)} "
                              f"keyframes in time order, each preceded by its timestamp]"})
            for f in frames:
                out.parts.append({"type": "text", "text": f"[{V.fmt_ts(f.t)}]"})
                out.parts.append(image_part(f.path))
            out.meta.update({"frames": [f.to_dict() for f in frames], "scene_changes": scenes,
                             "frame_budget": budget})
        if tr is not None:
            out.context.append(ContextBlock(att.filename, "transcript", tr.timestamped(),
                                            {"source": "video audio track",
                                             "duration": V.fmt_ts(dur)}))
        else:
            out.context.append(ContextBlock(att.filename, "metadata", "This video has no audio track."))
        timings["total_s"] = round(time.perf_counter() - t, 3)
        if dur:
            timings["s_per_minute"] = round(timings["total_s"] / (dur / 60), 2)
        return out

    async def _frames_cached(self, att, spec, opts, dur, budget, progress, timings):
        max_side = spec.limits.video_frame_max_side or spec.limits.image_max_side
        params = {"v": PIPELINE_VERSION, "budget": budget, "thr": opts.scene_threshold,
                  "max_side": max_side}
        if (hit := self.cache.get(att.sha256, "frames", params)) is not None:
            entry = self.cache.entry_dir(att.sha256, "frames", params)
            frames = [V.Frame(t=f["t"], path=str(entry / Path(f["path"]).name), reason=f["reason"])
                      for f in hit["frames"]]
            if all(Path(f.path).exists() for f in frames):
                return frames, hit["scenes"], True
        await progress(Progress(att.id, "scenes", 0.1, "Detecting scene changes"))
        t1 = time.perf_counter()
        scenes = await V.detect_scenes(att.path, opts.scene_threshold)
        timings["scenes_s"] = round(time.perf_counter() - t1, 3)
        plan = V.plan_frames(dur, scenes, budget)
        work = self.cache.scratch(att.sha256)

        async def frame_progress(frac: float) -> None:
            await progress(Progress(att.id, "frames", 0.2 + 0.7 * frac,
                                    f"Extracting keyframes ({round(frac * len(plan))}/{len(plan)})"))

        t1 = time.perf_counter()
        frames = await V.extract_frames(att.path, plan, work, max_side, progress=frame_progress)
        timings["frames_s"] = round(time.perf_counter() - t1, 3)
        entry = self.cache.put(att.sha256, "frames", params,
                               {"frames": [f.to_dict() for f in frames], "scenes": scenes},
                               files_from=work)
        work.rmdir()
        return [V.Frame(t=f.t, path=str(entry / Path(f.path).name), reason=f.reason)
                for f in frames], scenes, False

    async def _proxy_cached(self, att, spec, opts) -> Path:
        params = {"v": PIPELINE_VERSION, "fps": opts.native_video_fps,
                  "max_side": spec.limits.image_max_side, "max_s": spec.limits.video_seconds}
        entry = self.cache.entry_dir(att.sha256, "proxy", params)
        clip = entry / "proxy.mp4"
        if not clip.exists():
            work = self.cache.scratch(att.sha256)
            await V.make_proxy_clip(att.path, work / "proxy.mp4", opts.native_video_fps,
                                    spec.limits.image_max_side, spec.limits.video_seconds)
            self.cache.put(att.sha256, "proxy", params, {"path": "proxy.mp4"}, files_from=work)
            work.rmdir()
        return clip

    # --- documents --------------------------------------------------------------------------
    async def _document(self, att: Attachment, spec: LLMSpec, opts: RouteOptions,
                        progress: ProgressFn) -> PreparedAttachment:
        params = {"v": PIPELINE_VERSION}
        entry = self.cache.entry_dir(att.sha256, "document", params)
        hit = self.cache.get(att.sha256, "document", params)
        if hit is not None:
            doc = DocResult.from_dict(hit)
            for s in doc.sections:
                if s.image_path:
                    s.image_path = str(entry / Path(s.image_path).name)
        else:
            await progress(Progress(att.id, "extract", 0.2, "Extracting text"))
            work = self.cache.scratch(att.sha256)
            doc = await asyncio.to_thread(extract_document, att.path, att.filename, work)
            for s in doc.sections:  # OCR scanned pages
                if s.image_path:
                    ocr = await asyncio.to_thread(self.ocr, s.image_path)
                    s.text = ocr.text
                    s.image_path = Path(s.image_path).name
            d = doc.to_dict()
            self.cache.put(att.sha256, "document", params, d, files_from=work)
            work.rmdir()
            for s in doc.sections:
                if s.image_path:
                    s.image_path = str(entry / s.image_path)
        text = doc.text
        out = PreparedAttachment(att.id, att.filename, Kind.DOCUMENT, "text",
                                 meta={"format": doc.format, **doc.meta, "chars": len(text)},
                                 cached=hit is not None)
        if len(text) > MAX_DOC_CHARS:
            out.meta["truncated"] = True
            out.warnings.append(f"document is {len(text):,} chars; only the start is inlined "
                                "(full text is indexed for retrieval)")
            text = text[:MAX_DOC_CHARS] + "\n… [truncated]"
        out.context.append(ContextBlock(att.filename, "document", text, {"format": doc.format}))
        scanned = [s for s in doc.sections if s.image_path]
        if scanned and spec.capabilities.image:
            for s in scanned[: spec.limits.images_per_message]:
                out.parts.append({"type": "text", "text": f"[{att.filename} — {s.label} (scanned)]"})
                out.parts.append(image_part(s.image_path))
            out.mode = "preprocessed"
        return out

    # --- per-message limits -----------------------------------------------------------------
    @staticmethod
    def _enforce_image_cap(prepared: list[PreparedAttachment], spec: LLMSpec) -> None:
        """Cap standalone images (not video keyframes) at the model's per-message limit."""
        cap = spec.limits.images_per_message
        seen = 0
        for p in prepared:
            if p.kind not in (Kind.IMAGE, Kind.SCREENSHOT):
                continue
            n = sum(1 for part in p.parts if part.get("type") == "image_url")
            if seen + n > cap:
                p.parts = [x for x in p.parts if x.get("type") != "image_url"]
                p.mode = "text"
                p.warnings.append(f"more than {cap} images in one message; this one was not shown "
                                  "to the model")
            seen += n
