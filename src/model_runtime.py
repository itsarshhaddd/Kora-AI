"""Manage local llama.cpp chat and embedding server processes."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

import tuning
from config import (CHAT_MODEL, CHAT_PARALLEL_SLOTS, CHAT_URL, EMBED_MODEL,
                    EMBED_PARALLEL_SLOTS, EMBED_POOLING, EMBED_URL, LOG_DIR,
                    MODELS_DIR, RUNTIME_DIR)
from tuning import Profile



log = logging.getLogger("kora.runtime")

_EXE_NAMES = ("llama-server.exe", "llama-server")

class RuntimeErrorKora(RuntimeError):
    """fatal=True means waiting will never fix it (missing file, crashed process)."""

    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal

@dataclass
class Attempt:
    exe: Path
    profile: Profile
    label: str

def _process_kwargs() -> dict:
    """No console windows, and run below normal priority so the PC stays responsive.

    llama-server is a console program; launched from a windowed exe Windows would
    give each copy its own terminal.
    """
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
            "startupinfo": startup, "stdin": subprocess.DEVNULL}

def _open_log(name: str, max_bytes: int = 5_000_000):
    """Open data/logs/<name> for appending; roll it to <name>.1 when it gets big."""
    path = LOG_DIR / name
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            backup = path.with_name(name + ".1")
            backup.unlink(missing_ok=True)
            path.rename(backup)
    except OSError:
        pass
    handle = path.open("a", encoding="utf-8", errors="replace")
    handle.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} launching =====\n")
    handle.flush()
    return handle

class ModelRuntime:
    """Owns the two local llama.cpp services (chat + embedding) used by KORA.

    Settings come from the hardware profile selected by tuning.py. Backends are looked up in
    data/runtime/<cuda|vulkan|sycl|cpu>/ first, then data/runtime/ itself, and if the
    preferred one fails to start we fall back to the next one, ending on CPU.
    """

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen] = []
        self._names: dict[int, tuple[str, str]] = {}
        self.active: Attempt | None = None

    # ---- locating things
    @staticmethod
    def _find_exes(subdirs: tuple[str, ...]) -> list[Path]:
        found: list[Path] = []
        for folder in [RUNTIME_DIR / s for s in subdirs] + [RUNTIME_DIR]:
            for name in _EXE_NAMES:
                path = folder / name
                if path.exists() and path not in found:
                    found.append(path)
        return found

    @property
    def llama_server(self) -> Path:
        exes = self._find_exes(())
        if not exes:
            raise RuntimeErrorKora(f"llama-server runtime not found in {RUNTIME_DIR}", fatal=True)
        return exes[0]

    def _model(self, name: str) -> Path:
        path = MODELS_DIR / name
        if not path.exists():
            raise RuntimeErrorKora(f"Required model not found: {path}", fatal=True)
        return path

    @staticmethod
    def _ready(url: str) -> bool:
        try:
            with urlopen(f"{url}/v1/models", timeout=1.5) as response:
                return 200 <= response.status < 300
        except Exception:
            return False

    def _plan(self) -> list[Attempt]:
        main = tuning.get_profile()
        attempts: list[Attempt] = []
        if main.mode != "cpu":
            attempts += [Attempt(exe, main, f"{main.mode}:{exe.parent.name}") for exe in self._find_exes(main.backends)]
        cpu = tuning.get_cpu_profile()
        attempts += [Attempt(exe, cpu, f"cpu:{exe.parent.name}") for exe in self._find_exes(("cpu",))]
        if not attempts:
            raise RuntimeErrorKora(f"llama-server runtime not found in {RUNTIME_DIR}", fatal=True)
        return attempts

    # ---- launching
    def _launch(self, attempt: Attempt, chat_model: Path, embed_model: Path) -> None:
        p = attempt.profile
        env = os.environ.copy()
        if p.mode == "cpu":
            # Keep GPU backends out of the picture (also avoids crashes from broken GPU drivers).
            env["CUDA_VISIBLE_DEVICES"] = "-1"
            env["GGML_VK_VISIBLE_DEVICES"] = "-1"
        exe, cwd = str(attempt.exe), str(attempt.exe.parent)

        if not self._ready(CHAT_URL):
            cmd = [exe, "-m", str(chat_model), "--jinja", "--reasoning-format", "deepseek", "--host", "127.0.0.1",
                   "--port", CHAT_URL.rsplit(":", 1)[-1], "--no-webui", "--no-context-shift",
                   "--ctx-size", str(p.chat_ctx), "--parallel", str(CHAT_PARALLEL_SLOTS),
                   "--n-gpu-layers", str(p.chat_gpu_layers), "--threads", str(p.chat_threads)]
            if p.chat_ubatch:
                cmd += ["--batch-size", "512", "--ubatch-size", str(p.chat_ubatch)]
            log.info("Starting chat model [%s]: %s", attempt.label, " ".join(cmd))
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=_open_log("chat.log"), stderr=subprocess.STDOUT, **_process_kwargs())
            self.processes.append(proc)
            self._names[id(proc)] = ("chat", "chat.log")

        if not self._ready(EMBED_URL):
            cmd = [exe, "-m", str(embed_model), "--embedding", "--pooling", EMBED_POOLING,
                   "--batch-size", str(p.embed_ctx), "--ubatch-size", str(p.embed_ctx),
                   "--host", "127.0.0.1", "--port", EMBED_URL.rsplit(":", 1)[-1], "--no-webui",
                   "--ctx-size", str(p.embed_ctx), "--parallel", str(EMBED_PARALLEL_SLOTS),
                   "--n-gpu-layers", str(p.embed_gpu_layers), "--threads", str(p.embed_threads)]
            log.info("Starting embedding model [%s]", attempt.label)
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=_open_log("embedding.log"), stderr=subprocess.STDOUT, **_process_kwargs())
            self.processes.append(proc)
            self._names[id(proc)] = ("embedding", "embedding.log")

    def _wait(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._ready(CHAT_URL) and self._ready(EMBED_URL):
                log.info("Both model services are ready.")
                return
            for proc in self.processes:
                code = proc.poll()
                if code is not None:
                    name, logfile = self._names.get(id(proc), ("model", "chat.log"))
                    raise RuntimeErrorKora(f"The {name} model process exited with code {code}. See data\\logs\\{logfile} for the reason.", fatal=True)
            time.sleep(0.5)
        raise RuntimeErrorKora("Local model services did not become ready in time. Check the logs folder.")

    def ensure_running(self) -> None:
        chat_model = self._model(CHAT_MODEL)
        embed_model = self._model(EMBED_MODEL)
        attempts = self._plan()

        for index, attempt in enumerate(attempts):
            # Slow laptops loading a model from disk need longer than a gaming PC.
            timeout = 45 if attempt.profile.mode != "cpu" else 150
            try:
                self._launch(attempt, chat_model, embed_model)
                self._wait(timeout)
                self.active = attempt
                log.info("Running with: %s (%s)", attempt.label, attempt.profile.summary)
                return
            except RuntimeErrorKora as exc:
                if not exc.fatal or index == len(attempts) - 1:
                    raise
                log.warning("Attempt '%s' failed (%s); trying the next option.", attempt.label, exc)
                self.close()

    def close(self) -> None:
        for proc in reversed(self.processes):
            if proc.poll() is None:
                proc.terminate()
        for proc in self.processes:
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        self.processes.clear()
        log.info("Model processes stopped.")
