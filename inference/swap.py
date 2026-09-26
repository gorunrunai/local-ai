"""Generate the llama-swap config from config/models.yaml.

Every LLM becomes an isolated upstream process bound to 127.0.0.1. llama-swap runs one
LLM at a time by default, so asking for a different model unloads the current one; that
is exactly the memory policy we want (two big LLMs together exceed the budget).
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import yaml

from inference.config import ROOT, LLMSpec, ModelsConfig

SAFE_ENV = ["HF_HUB_OFFLINE=1", "HF_HUB_DISABLE_TELEMETRY=1", "TOKENIZERS_PARALLELISM=false"]


def upstream_cmd(spec: LLMSpec, python: str = sys.executable) -> list[str]:
    return [python, "-m", "mlx_vlm.server", "--host", "127.0.0.1", "--port", "${PORT}",
            "--model", spec.repo, "--max-kv-size", str(spec.context_length)]


def _prefix_cache_env(spec: LLMSpec) -> list[str]:
    """Reuse KV for repeated prompt prefixes (system prompt, tools, history) across requests.

    Kept in RAM only: a disk tier would persist conversation-derived state (incl. incognito).
    """
    if spec.prefix_cache_gb <= 0:
        return []
    return ["APC_ENABLED=1", "APC_DISK_ENABLED=0", f"APC_MEMORY_MAX_GB={spec.prefix_cache_gb:g}"]


def build_swap_config(cfg: ModelsConfig, python: str = sys.executable) -> dict:
    models = {}
    for mid, spec in cfg.llms.items():
        try:
            cmd = upstream_cmd(spec, python)
        except FileNotFoundError:
            continue  # weights not downloaded; model simply isn't offered
        entry = {
            "cmd": " ".join(shlex.quote(c) if c != "${PORT}" else c for c in cmd),
            "proxy": "http://127.0.0.1:${PORT}",
            "checkEndpoint": "/health",
            "ttl": spec.ttl_s,
            "env": list(SAFE_ENV) + _prefix_cache_env(spec),
            "name": spec.display_name,
            "useModelName": spec.repo,
        }
        models[mid] = entry
    out = {
        "healthCheckTimeout": cfg.proxy.health_timeout_s,
        "logLevel": "info",
        "logToStdout": "both",  # include the mlx-vlm servers' logs in data/logs
        "startPort": 18100,
        "sendLoadingState": False,
        "models": models,
    }
    sets = {name: expr for name, expr in cfg.concurrency.sets.items()
            if all(m.strip(" ()") in models for alt in expr.split("|") for m in alt.split("&"))}
    if sets:
        out["routing"] = {"router": {"use": "matrix", "settings": {"matrix": {
            "evict_costs": {k: v for k, v in cfg.concurrency.evict_costs.items() if k in models},
            "sets": sets}}}}
    return out


def write_swap_config(cfg: ModelsConfig, dest: Path = ROOT / "data" / "llama-swap.yaml") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(build_swap_config(cfg), sort_keys=False))
    return dest
