"""Central logging for KORA. Everything ends up in data/logs/:

    data/logs/kora.log       app log (rotating, 5 x 2 MB) -- start here
    data/logs/crash.log      hard crashes (faulthandler) and fatal startup errors
    data/logs/chat.log       llama-server output for the chat model
    data/logs/embedding.log  llama-server output for the embedding model
"""
from __future__ import annotations

import faulthandler
import logging
import os
import platform
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from config import LOG_DIR, MODELS_DIR, ROOT, RUNTIME_DIR

_crash_file = None


class _StreamToLog:
    """File-like object so print() / stray stderr output lands in kora.log."""

    encoding = "utf-8"

    def __init__(self, logger: logging.Logger, level: int) -> None:
        self._logger, self._level, self._buf = logger, level, ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buf.strip():
            self._logger.log(self._level, self._buf.rstrip())
        self._buf = ""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no fileno")


def setup() -> logging.Logger:
    global _crash_file
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(LOG_DIR / "kora.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("werkzeug",):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    frozen = getattr(sys, "frozen", False)
    if frozen or sys.stdout is None or sys.stderr is None:
        # Windowed exe: there is no console, so route print()/tracebacks to the log.
        sys.stdout = _StreamToLog(logging.getLogger("stdout"), logging.INFO)
        sys.stderr = _StreamToLog(logging.getLogger("stderr"), logging.ERROR)
    else:
        root.addHandler(logging.StreamHandler(sys.stderr))

    try:
        _crash_file = open(LOG_DIR / "crash.log", "a", encoding="utf-8", buffering=1)
        _crash_file.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} session start =====\n")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except Exception:  # noqa: BLE001
        pass

    log = logging.getLogger("kora")

    def _excepthook(exc_type, exc, tb):
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def _thread_hook(args):
        log.critical("Uncaught exception in thread %s", getattr(args.thread, "name", "?"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook

    log.info("=" * 60)
    log.info("KORA starting | frozen=%s | python=%s | %s", frozen, platform.python_version(), platform.platform())
    log.info("root=%s", ROOT)
    runtime_paths = [RUNTIME_DIR / name for name in ("llama-server.exe", "llama-server")]
    runtime_paths.extend(
        folder / name
        for folder in RUNTIME_DIR.iterdir()
        if folder.is_dir()
        for name in ("llama-server.exe", "llama-server")
    )
    log.info("llama-server present=%s | models: %s", any(path.is_file() for path in runtime_paths),
             ", ".join(p.name for p in sorted(MODELS_DIR.glob("*.gguf"))) or "NONE")
    return log


def fatal_dialog(message: str) -> None:
    """Last-resort native error box (Windows) for when no window could be shown."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, f"{message}\n\nLogs: {LOG_DIR}", "KORA", 0x10)
    except Exception:  # noqa: BLE001
        pass
