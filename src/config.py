from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = project_root()
DATA_DIR = ROOT / "data"
WORKSPACE_DIR = DATA_DIR / "workspace"
MODELS_DIR = DATA_DIR / "models"
RUNTIME_DIR = DATA_DIR / "runtime"
# All logs (app, crash, llama-server chat/embedding) live here.
LOG_DIR = DATA_DIR / "logs"
# Persistent profile dir for the pywebview window (cookies/localStorage), so
# the app.js appearance settings (kora_appearance) survive app restarts
# instead of pywebview's default "private_mode" wiping them each launch.
WEBVIEW_STORAGE_DIR = RUNTIME_DIR / "webview_storage"
WEB_DIR = ROOT / "src" / "web"
INDEX_PATH = DATA_DIR / "knowledge_index.json"
CONVERSATIONS_PATH = DATA_DIR / "conversations.json"
ACTIVITY_PATH = DATA_DIR / "activity.json"
SETTINGS_PATH = DATA_DIR / "kora_new_settings.json"
# Performance choice saved from Settings > System (read by tuning at startup).
HARDWARE_PREFS_PATH = DATA_DIR / "hardware_prefs.json"

# ---------------------------------------------------------------------------
# MODEL FILES -- change these to switch models (e.g. a smaller one for
# lower-end PCs). Put the .gguf file in the "data/models" folder and set the name
# below to match the filename EXACTLY (including the .gguf extension).
# You can also override without editing this file via env vars
# KORA_CHAT_MODEL / KORA_EMBED_MODEL.
# ---------------------------------------------------------------------------
CHAT_MODEL = os.getenv("KORA_CHAT_MODEL", "Qwen3.5-0.8B-Q4_K_M.gguf")
EMBED_MODEL = os.getenv("KORA_EMBED_MODEL", "nomic-embed-text-v1.5.Q4_K_M.gguf")
# Nomic Embed Text v1.5 uses mean pooling and task-specific prefixes.
EMBED_POOLING = os.getenv("KORA_EMBED_POOLING", "mean")
EMBED_QUERY_PREFIX = os.getenv("KORA_EMBED_QUERY_PREFIX", "search_query: ")
EMBED_DOC_PREFIX = os.getenv("KORA_EMBED_DOC_PREFIX", "search_document: ")
def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Read a bounded integer environment setting with a safe default."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


# Max texts sent per /v1/embeddings request while indexing.
EMBED_BATCH_SIZE = _env_int("KORA_EMBED_BATCH", 8)
CHAT_URL = os.getenv("KORA_CHAT_URL", "http://127.0.0.1:8080")
EMBED_URL = os.getenv("KORA_EMBED_URL", "http://127.0.0.1:8081")
API_HOST = os.getenv("KORA_HOST", "127.0.0.1")
API_PORT = _env_int("KORA_PORT", 5175, minimum=1)
MAX_AGENT_STEPS = _env_int("KORA_MAX_AGENT_STEPS", 256)
TOP_K = _env_int("KORA_TOP_K", 20)

# ---------------------------------------------------------------------------
# AI RUNTIME TUNING -- AUTOMATIC.
# KORA detects CPU / RAM / GPU (NVIDIA, AMD, Intel) at startup and picks
# context size, GPU offload, and thread count in tuning.py so it can adapt to
# different machines without unnecessarily consuming system resources. The values
# below are None = "auto". Set the env var only to force a value manually.
# ---------------------------------------------------------------------------
def _opt_int(name: str) -> int | None:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


CHAT_CTX_SIZE = _opt_int("KORA_CHAT_CTX_SIZE")
EMBED_CTX_SIZE = _opt_int("KORA_EMBED_CTX_SIZE")
CHAT_GPU_LAYERS = _opt_int("KORA_CHAT_GPU_LAYERS")
EMBED_GPU_LAYERS = _opt_int("KORA_EMBED_GPU_LAYERS")
CHAT_THREADS = _opt_int("KORA_CHAT_THREADS")
EMBED_THREADS = _opt_int("KORA_EMBED_THREADS")
CHAT_PARALLEL_SLOTS = _env_int("KORA_CHAT_PARALLEL", 1)
EMBED_PARALLEL_SLOTS = _env_int("KORA_EMBED_PARALLEL", 1)

for _path in (DATA_DIR, WORKSPACE_DIR, MODELS_DIR, RUNTIME_DIR, WEBVIEW_STORAGE_DIR, LOG_DIR):
    _path.mkdir(parents=True, exist_ok=True)
