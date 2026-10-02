"""Process-level settings (env / .env). User-facing preferences live in the DB `settings` table."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from inference.config import ROOT


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    data_dir: Path = ROOT / "data"
    # Remote access: requests proxied by `tailscale serve` must carry the bearer token.
    remote_access: bool = False
    auth_token_file: Path = ROOT / "data" / "auth_token"
    # Allowed browser origins for the dev frontend (Vite) — everything else is same-origin.
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "assistant.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


# User preferences (DB `settings` table) and their defaults.
DEFAULT_PREFS: dict = {
    "custom_instructions": "",
    "memory_enabled": True,
    "style": "default",
    "temperature": 0.6,
    "max_tokens": 4096,
    "context_tokens": None,        # None = model's context_length
    "video_frame_budget": 24,
    "video_max_seconds": {},       # video model id -> longest clip in seconds (missing = models.yaml)
    "tool_overrides": {},          # tool name -> {"enabled": bool, "policy": "allow|confirm|deny"}
    "locale": "en-US",
    "timezone": None,              # None = system timezone
    "voice": {"stt": "parakeet-v2", "tts_voice": "af_heart", "speed": 1.0, "vad_sensitivity": 0.5,
              "push_to_talk_key": "AltRight"},
}


def ensure_dirs(s: AppSettings) -> None:
    for d in (s.data_dir, s.files_dir, s.tmp_dir):
        Path(d).mkdir(parents=True, exist_ok=True)
