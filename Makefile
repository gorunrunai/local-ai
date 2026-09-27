SHELL := /bin/bash
UV ?= uv
LLAMA_SWAP_VERSION := 257
LLAMA_SWAP_URL := https://github.com/mostlygeek/llama-swap/releases/download/v$(LLAMA_SWAP_VERSION)/llama-swap_$(LLAMA_SWAP_VERSION)_darwin_arm64.tar.gz

.PHONY: help setup deps system-deps llama-swap models models-all video-setup models-video search-setup desktop desktop-download desktop-install desktop-install-app desktop-uninstall install-app app-frontend fixtures dev backend frontend-deps build test-e2e remote remote-off chat \
        test test-integration bench bench-voice lint clean-cache gpu-limit status

help:
	@echo "make setup             first-time install: brew deps, python env, llama-swap, models, fixtures"
	@echo "make dev               start the full stack (backend + llama-swap + frontend)"
	@echo "make chat MSG='...'    send a message from the terminal (backend must be running)"
	@echo "make remote            expose the backend to your phone over Tailscale (token required)"
	@echo "make desktop-install   build the GoRunRun Local AI Mac app into Applications"
	@echo "make search-setup      install local SearXNG for web search"
	@echo "make models-video      optional video generation (LTX-2.3, Wan 2.2; ~52 GB)"
	@echo "make test              unit tests (no models needed)"
	@echo "make test-integration  integration tests against the real local models"
	@echo "make test-e2e          browser tests of the UI against the real backend and models"
	@echo "make build             build the web app (served by the backend at http://127.0.0.1:8000)"
	@echo "make bench             benchmark TTFT, tok/s, STT RTF, TTS latency, video preprocessing, memory"
	@echo "make bench-voice       voice-mode latency (end of speech → first audio) against the running backend"
	@echo "make models-all        download every configured chat model (Qwen and Gemma)"
	@echo "make gpu-limit         show how to raise the Metal wired-memory limit"

# What install.sh runs: everything needed to use the app, nothing that edits tracked files
# (no fixtures, frozen lockfiles), so later updates can fast-forward cleanly.
install-app: system-deps deps llama-swap models search-setup app-frontend

app-frontend:
	cd frontend && npm ci --no-audit --no-fund && npm run build

setup: system-deps deps llama-swap models search-setup fixtures frontend-deps build
	@echo "✔ setup complete. Start with 'make backend' and open http://127.0.0.1:8000"
	@echo "  Optional: 'make models-video', 'make desktop-install', 'make gpu-limit'."

system-deps:
	@command -v brew >/dev/null || { echo "Homebrew is required: https://brew.sh"; exit 1; }
	@command -v ffmpeg >/dev/null || brew install ffmpeg
	@command -v uv >/dev/null || brew install uv
	@command -v node >/dev/null || brew install node

deps:
	$(UV) python install 3.12
	$(UV) sync --frozen

llama-swap: bin/llama-swap
bin/llama-swap:
	mkdir -p bin
	curl -fsSL $(LLAMA_SWAP_URL) | tar xz -C bin llama-swap
	chmod +x bin/llama-swap

models:
	$(UV) run python scripts/download_models.py

models-all:
	$(UV) run python scripts/download_models.py --all

# Mac app: a native window around the web app that starts/stops the backend (LaunchAgent).
APPS_DIR := $(shell [ -w /Applications ] && echo /Applications || echo $(HOME)/Applications)
desktop:
	./desktop/build.sh

# The same app, built on GitHub (.github/workflows/mac-app.yml): no Swift compiler needed here.
desktop-download:
	./scripts/download-app.sh

desktop-install: desktop
	$(MAKE) --no-print-directory desktop-install-app

# Install the app already in desktop/build (from `make desktop` or `make desktop-download`).
desktop-install-app:
	./scripts/install-agent.sh
	mkdir -p "$(APPS_DIR)" && rm -rf "$(APPS_DIR)/GoRunRun Local AI.app"
	cp -R "desktop/build/GoRunRun Local AI.app" "$(APPS_DIR)/"
	@echo "installed $(APPS_DIR)/GoRunRun Local AI.app"

desktop-uninstall:
	./scripts/install-agent.sh --remove
	rm -rf "$(APPS_DIR)/GoRunRun Local AI.app"

# Local SearXNG for the web_search tool (native install, no Docker). The backend starts it on
# the first search and stops it on shutdown.
SEARXNG_REV := 3cd69d30e2a78dfc817be9e349e7c2e4317c92e3
search-setup:
	@if [ ! -d searxng/src/.git ]; then git clone -q https://github.com/searxng/searxng.git searxng/src; fi
	cd searxng/src && git fetch -q --depth 1 origin $(SEARXNG_REV) && git checkout -q $(SEARXNG_REV)
	cd searxng && $(UV) venv -q --allow-existing --python 3.12 .venv
	cd searxng && $(UV) pip install -q --python .venv/bin/python -r src/requirements.txt

# Optional local video generation (LTX-2.3 + Wan 2.2, ~52 GB of weights). The engines get their
# own venv in videogen/ so their dependency pins can't disturb the chat stack.
video-setup:
	cd videogen && $(UV) sync

# VIDEO="ltx-2.3 wan-2.2-5b" picks engines (default: all configured).
models-video: video-setup
	$(UV) run python scripts/download_models.py --video $(VIDEO)

fixtures:
	$(UV) run python scripts/make_fixtures.py

test:
	$(UV) run pytest
	cd frontend && npx vitest run

test-e2e: build
	cd frontend && npx playwright test

test-integration:
	$(UV) run pytest -m integration -s tests/integration

bench:
	$(UV) run python scripts/benchmark.py

# Voice round trip against the running backend (make backend): end of speech → first audio.
bench-voice:
	$(UV) run python scripts/voice_roundtrip.py --runs 5

lint:
	$(UV) run ruff check inference media orchestrator scripts tests

clean-cache:
	rm -rf data/cache

gpu-limit:
	@echo "Current: $$(sysctl -n iogpu.wired_limit_mb) MB (0 = macOS default, ~75% of RAM)"
	@echo "Raise until reboot (leaves ~10 GB for macOS on a 64 GB Mac):"
	@echo "  sudo sysctl iogpu.wired_limit_mb=54000"
	@echo "See README > Troubleshooting for making it persistent."

# Backend + Vite dev server with hot reload (http://127.0.0.1:5173).
dev:
	./scripts/dev.sh

frontend-deps:
	cd frontend && npm ci --no-audit --no-fund && npx playwright install chromium

# Production build of the PWA; the backend then serves it at http://127.0.0.1:8000
build:
	cd frontend && npm run build

backend:
	./scripts/run-backend.sh

# Phone access over your tailnet: the same switch as Settings → Phone access in the app
# (the backend must be running). Tailscale forwards HTTPS to the backend on 127.0.0.1.
remote:
	@curl -fsS -X POST http://127.0.0.1:8000/api/remote -H 'Content-Type: application/json' -d '{"enabled":true}' \
	  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("Phone access is on. Open", d["tailscale"]["url"], "on your phone; the login token is in Settings → Phone access.")' \
	  || echo "Couldn't turn it on: is the app running? Open Settings → Phone access for details."

remote-off:
	@curl -fsS -X POST http://127.0.0.1:8000/api/remote -H 'Content-Type: application/json' -d '{"enabled":false}' >/dev/null \
	  && echo "Phone access is off." || echo "Couldn't reach the app: is it running?"

chat:
	$(UV) run python scripts/chat_cli.py "$(MSG)"
