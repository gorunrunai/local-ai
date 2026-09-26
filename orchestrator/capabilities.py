"""What this Mac and this installation can do, for the home screen and to switch features off.

The memory rules match install.sh and the table on local.gorunrun.ai/macs:
  32–47 GB  Gemma 4, no video
  48–63 GB  Gemma 4; LTX-2.3 video optional (untested at this size)
  64 GB +   Qwen 3.5, Gemma 4 and both video engines (tested on 64 GB)
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import asdict, dataclass

from inference.config import ModelsConfig
from inference.manager import llm_downloaded
from inference.video import installed as video_installed
from orchestrator.remote import tailscale_binary
from orchestrator.search import Searxng

TESTED_GB = [(64, 96)]      # memory sizes tested end to end (an M5 Max with 64 GB)
MACS_URL = "https://local.gorunrun.ai/macs/"


@dataclass
class Feature:
    id: str
    label: str
    available: bool
    reason: str = ""          # why it's unavailable


@dataclass
class Limitation:
    text: str                 # short, shown in red
    detail: str               # longer explanation, shown on hover


def _sysctl(name: str) -> str:
    try:
        return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5,
                              check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def machine() -> dict:
    mem = _sysctl("hw.memsize")
    memory_gb = round(int(mem) / 1024 ** 3) if mem.isdigit() else 0
    return {"chip": _sysctl("machdep.cpu.brand_string") or platform.machine(),
            "memory_gb": memory_gb, "macos": platform.mac_ver()[0] or platform.release()}


def report(cfg: ModelsConfig, mac: dict | None = None, *, web_enabled: bool = True) -> dict:
    """web_enabled: the user's Web access switch (web_search and web_fetch)."""
    mac = mac or machine()
    gb = mac["memory_gb"]
    tested = any(lo <= gb < hi for lo, hi in TESTED_GB)
    chat = cfg.llm()
    listener = cfg.llms.get(cfg.defaults.audio_listener or "")
    listener_ok = bool(listener and listener.id != chat.id and listener.capabilities.audio
                       and llm_downloaded(listener))
    hears = chat.capabilities.audio or listener_ok
    engines = {vid: video_installed(spec)[0] for vid, spec in cfg.video.items()}
    ltx, wan = engines.get("ltx-2.3", False), engines.get("wan-2.2-5b", False)
    search_ok = Searxng.installed()

    installed = [{"role": "Chat", "name": chat.display_name}]
    if listener_ok:
        installed.append({"role": "Hears voice messages", "name": listener.display_name})
    for vid, ok in engines.items():
        if ok:
            installed.append({"role": "Video" + (" with sound" if vid == "ltx-2.3" else ", no sound"),
                              "name": cfg.video[vid].display_name.split(" ·")[0]})
    installed.append({"role": "Speech", "name": "Parakeet, Whisper and Kokoro"})
    if search_ok:
        installed.append({"role": "Web search", "name": "SearXNG (local)"})

    no_video_reason = (f"Video creation needs 48 GB of memory or more; this Mac has {gb} GB." if gb < 48 else
                       "Not installed. Run the installer again and choose video creation.")
    features = [
        Feature("chat", "Chat, photos, screenshots and documents", True),
        Feature("voice_mode", "Talk out loud (voice conversation)", True),
        Feature("voice_messages", "Voice messages, including tone of voice", hears,
                "" if hears else f"{chat.display_name} can't hear audio, and no model that can "
                "(Gemma 4) is installed. Voice messages would only be transcribed."),
        Feature("video_creation", "Create videos", ltx or wan, "" if (ltx or wan) else no_video_reason),
        Feature("animate_photo", "Animate a photo", ltx or wan,
                "" if (ltx or wan) else no_video_reason),
        Feature("video_sound", "Videos with sound and speech", ltx,
                "" if ltx else ("Only LTX-2.3 creates sound, and it isn't installed." if gb >= 48
                                else no_video_reason)),
        Feature("web_search", "Search the web", search_ok and web_enabled,
                "The local search engine isn't installed (run `make search-setup`)." if not search_ok else
                "" if web_enabled else "Web access is turned off, so nothing goes online. Switch it on from the home screen."),
        Feature("phone", "Use it from your phone", tailscale_binary() is not None,
                "" if tailscale_binary() else "Install Tailscale on this Mac to reach it from your phone."),
    ]

    limits: list[Limitation] = []
    if not tested:
        limits.append(Limitation(
            f"{gb} GB Macs haven't been tested yet",
            f"This setup is our best estimate for {gb} GB of memory. If something is slow or fails, please "
            f"report it: {MACS_URL}"))
    if chat.id.startswith("gemma"):
        limits += [
            Limitation("Less capable than Qwen 3.5 at reasoning and coding",
                       "Gemma 4 is a 12-billion-parameter model. Qwen 3.5 (35B) gives stronger answers but needs "
                       f"64 GB of memory{'' if gb >= 64 else f'; this Mac has {gb} GB'}."),
            Limitation("Writes about half as fast as Qwen 3.5",
                       "Gemma uses all 12B parameters for every word; Qwen only uses 3B of its 35B, so it's faster."),
            Limitation(f"Remembers up to {chat.context_length // 1024}K tokens of a chat",
                       "Long chats and documents are trimmed sooner than with Qwen 3.5 (64K)."),
            Limitation(f"Up to {chat.limits.images_per_message} images per message",
                       f"Images are shrunk to {chat.limits.image_max_side} px on the longest side."),
        ]
    elif not hears:
        limits.append(Limitation("Can't hear voice messages",
                                 f"{chat.display_name} doesn't take audio. Install Gemma 4 to hear voice "
                                 "messages and tone of voice; otherwise they're transcribed."))
    if not (ltx or wan):
        limits.append(Limitation("No video creation", no_video_reason))
    if ltx:
        limits.append(Limitation("LTX-2.3 videos: up to 10 s at 768×512",
                                 "Short spoken phrases work well; long sentences can garble and lip-sync is "
                                 "approximate. Counting (\"three knocks\") isn't reliable."))
    if wan:
        limits.append(Limitation("Wan 2.2 videos have no sound",
                                 "Clips are silent: no speech, music or effects, and photos can't be made to talk."))
        limits.append(Limitation("Wan 2.2 takes 6–7 minutes per clip",
                                 "It renders at 960×544." + (" The chat model is paused while it renders and "
                                                            "reloads afterwards." if gb < 96 else "")))
    return {"machine": mac, "tested": tested, "report_url": MACS_URL, "installed": installed, "web_access": web_enabled,
            "features": [asdict(f) for f in features], "limitations": [asdict(x) for x in limits]}
