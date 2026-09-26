"""Inference layer: model manager, providers and in-process model services."""

import os

# Runtime is offline and telemetry-free. Weights are fetched only by `make setup`
# (scripts/download_models.py sets ALLOW_MODEL_DOWNLOADS=1).
if os.environ.get("ALLOW_MODEL_DOWNLOADS") != "1":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
