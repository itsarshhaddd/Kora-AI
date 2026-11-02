from __future__ import annotations

import atexit
import json
import os
import socket
import threading
import time
from pathlib import Path

from config import API_HOST, API_PORT, LOG_DIR, WEBVIEW_STORAGE_DIR

# Logging first, and nothing heavy imported yet: the window must appear
# instantly, so server (pymupdf, models, ...) is imported later on a
# background thread while a splash screen is showing.
import app_logging

log = app_logging.setup()

# Dark background so the window doesn't flash white before the page loads,
# matching the app's own --bg color in src/web/styles.css.
WINDOW_BG = "#0b0b0c"


def _wait_for_port(host: str, port: int, timeout: float = 60.0, thread: threading.Thread | None = None) -> bool:
    """Block until something is listening on host:port, or timeout.

    Flask only starts accepting connections once KoraServer.run() reaches
    app.run() (after the model runtime has been kicked off), so this is how
    we know it's safe to point a browser/window at the URL instead of
    racing it and showing a connection-refused page.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if thread is not None and not thread.is_alive():
            return False
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


class WindowApi:
    """JS-callable bridge for the custom in-page titlebar (src/web/index.html).

    The window is created frameless (see _open_window) so there's no native
    titlebar to draw minimize/maximize/close buttons on; the web UI draws
    its own instead and calls back into these methods via
    `window.pywebview.api.<name>()`.
    """

    def __init__(self) -> None:
        # Leading underscore is load-bearing: pywebview's JS-bridge builder
        # (webview.util.get_functions) walks every *public* attribute of a
        # js_api object looking for nested method namespaces, and recurses
        # into any non-callable one. A plain `self.window = window` attribute
        # used to make it recurse into the real GUI Window object itself,
        # straight into native toolkit internals (e.g. a .NET Rectangle on
        # the Windows/Edge backend) that pythonnet can't compare against a
        # Python object -- crashing on startup with something like
        # "'WindowApi' value cannot be converted to System.Drawing.Rectangle".
        # Underscore-prefixed attributes are skipped by that traversal, so
        # keeping the window reference private avoids the whole class of bug.
        self._window = None
        self._restore_geometry: tuple[int, int, int, int] | None = None
        self._maximized = False

    def minimize(self) -> None:
        if self._window:
            self._window.minimize()

    def is_maximized(self) -> bool:
        return self._maximized

    def toggle_maximize(self) -> bool:
        """Toggle maximize/restore and report the resulting state to the UI."""
        win = self._window
        if win is None:
            return False
        try:
            if self._maximized:
                win.restore()
                self._maximized = False
            else:
                win.maximize()
                self._maximized = True
            return self._maximized
        except Exception:  # noqa: BLE001 - fallback for older pywebview backends
            pass
        try:
            import webview
            screens = webview.screens
            screen = screens[0] if screens else None
            if screen is None:
                return False
            if self._restore_geometry is None:
                self._restore_geometry = (win.x, win.y, win.width, win.height)
                win.move(0, 0)
                win.resize(screen.width, screen.height)
                self._maximized = True
            else:
                x, y, w, h = self._restore_geometry
                self._restore_geometry = None
                win.resize(w, h)
                win.move(x, y)
                self._maximized = False
        except Exception:  # noqa: BLE001
            log.exception("Could not toggle maximize")
        return self._maximized

    def close(self) -> None:
        if self._window:
            self._window.destroy()

    def open_logs(self) -> None:
        """Open the logs folder in Explorer (used by the splash error screen)."""
        try:
            if os.name == "nt":
                os.startfile(str(LOG_DIR))  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(LOG_DIR)])
        except Exception:  # noqa: BLE001
            log.exception("Could not open logs folder")

    def save_export(self, session_id: str, fmt: str = "pdf") -> dict:
        """Export a chat via a native "Save as" dialog (browser-style downloads
        aren't reliable inside the embedded window). Returns {ok, path} / {ok: False, ...}."""
        import urllib.request
        from urllib.parse import quote

        win = self._window
        if win is None:
            return {"ok": False, "error": "No window"}
        fmt = "md" if fmt == "md" else "pdf"
        try:
            with urllib.request.urlopen(f"http://{API_HOST}:{API_PORT}/api/library/{quote(session_id)}/export.{fmt}", timeout=60) as resp:
                data = resp.read()
                name = resp.headers.get("X-Export-Filename") or f"KORA_conversation.{fmt}"
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        import webview

        dialog = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
        types = ("PDF document (*.pdf)",) if fmt == "pdf" else ("Markdown (*.md)",)
        chosen = win.create_file_dialog(dialog, directory=str(Path.home() / "Documents"), save_filename=name, file_types=types)
        if not chosen:
            return {"ok": False, "cancelled": True}
        path = Path(chosen if isinstance(chosen, str) else chosen[0])
        if path.suffix.lower() != f".{fmt}":
            path = path.with_suffix(f".{fmt}")
        path.write_bytes(data)
        return {"ok": True, "path": str(path)}


SPLASH_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#0b0b0c;color:#f3f3f4;font:14.5px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;overflow:hidden;user-select:none;-webkit-font-smoothing:antialiased}
.top{height:52px;display:flex;align-items:center;justify-content:space-between;padding-left:18px;border-bottom:1px solid rgba(255,255,255,.08)}
.brand{display:flex;align-items:center;gap:10px;font-weight:600;font-size:13px;letter-spacing:.16em}.mark{display:inline-grid;grid-template-columns:repeat(2,7px);gap:3px}.mark i{width:7px;height:7px;border-radius:50%;background:#8b6cff;display:block}.mark i:nth-child(2){background:#b5a1ff}.mark.lg{grid-template-columns:repeat(2,14px);gap:6px}.mark.lg i{width:14px;height:14px}
.wc{display:flex;height:52px}.wc button{width:46px;height:100%;background:none;border:0;color:#9a9aa3;display:grid;place-items:center;cursor:pointer}.wc button:hover{background:#1f1f23;color:#f3f3f4}.wc button.x:hover{background:#e5484d;color:#fff}.wc svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.4;stroke-linecap:round;stroke-linejoin:round}
.stage{position:absolute;top:52px;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center}.in{width:340px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:14px}.mark.lg i{animation:glow 1.8s ease-in-out infinite alternate}.mark.lg i:nth-child(2){animation-delay:.2s}.mark.lg i:nth-child(3){animation-delay:.4s}.mark.lg i:nth-child(4){animation-delay:.6s}@keyframes glow{from{opacity:.45;transform:scale(.82)}to{opacity:1;transform:scale(1.08)}}
h1{margin:6px 0 0;font-size:18px;letter-spacing:.34em;padding-left:.34em;font-weight:600}#msg{font-size:13px;color:#9a9aa3;min-height:20px;line-height:1.5;max-width:420px}.bar{width:240px;height:3px;background:#1f1f23;border-radius:3px;overflow:hidden}.bar span{display:block;height:100%;width:2%;background:#8b6cff;transition:width .5s cubic-bezier(.22,1,.36,1)}
.err .bar{display:none}.err #msg{color:#ff6b7a}#actions{display:none;gap:10px;margin-top:6px}.err #actions{display:flex}#actions button{background:#121214;color:#f3f3f4;border:1px solid rgba(255,255,255,.14);border-radius:9px;padding:8px 14px;font-size:13px;cursor:pointer}#actions button:hover{background:#1f1f23}
</style></head><body><div class="top pywebview-drag-region"><div class="brand pywebview-drag-region"><span class="mark"><i></i><i></i><i></i><i></i></span>KORA</div><div class="wc"><button onclick="pywebview.api.minimize()" aria-label="Minimize"><svg viewBox="0 0 14 14"><path d="M2 7h10"/></svg></button><button class="x" onclick="pywebview.api.close()" aria-label="Close"><svg viewBox="0 0 14 14"><path d="M2.5 2.5l9 9M11.5 2.5l-9 9"/></svg></button></div></div>
<div class="stage" id="w"><div class="in"><span class="mark lg"><i></i><i></i><i></i><i></i></span><h1>KORA</h1><div id="msg">Starting…</div><div class="bar"><span id="b"></span></div><div id="actions"><button onclick="pywebview.api.open_logs()">Open logs folder</button><button onclick="pywebview.api.close()">Close</button></div></div></div>
<script>function setStatus(t,p){document.getElementById('msg').textContent=t;if(p!=null)document.getElementById('b').style.width=p+'%'}function showError(t){document.getElementById('w').classList.add('err');document.getElementById('msg').textContent=t}</script></body></html>"""


class _App:
    server = None
    window = None


def _splash(js: str) -> None:
    try:
        if _App.window is not None:
            _App.window.evaluate_js(js)
    except Exception:  # noqa: BLE001 - splash updates are cosmetic
        pass


def _status(text: str, pct: int | None = None) -> None:
    log.info("boot: %s", text)
    _splash(f"setStatus({json.dumps(text)}, {json.dumps(pct)})")


def _run_server() -> None:
    try:
        _App.server.run()
    except Exception:  # noqa: BLE001
        log.critical("Web server crashed", exc_info=True)


def _bootstrap() -> None:
    """Runs on a background thread once the splash window is on screen."""
    url = f"http://{API_HOST}:{API_PORT}/"
    try:
        _status("Checking your hardware…", 10)
        import tuning

        tuning.get_profile()  # detects CPU / RAM / GPU and logs the chosen AI settings
        _status("Loading engine…", 25)
        from server import KoraServer

        _App.server = KoraServer()
        _status("Starting server…", 50)
        thread = threading.Thread(target=_run_server, daemon=True, name="kora-server")
        thread.start()
        if not _wait_for_port(API_HOST, API_PORT, thread=thread):
            raise RuntimeError(f"The local server did not start on port {API_PORT}. It may already be in use by another KORA window or program.")
        _status("Opening KORA…", 90)
        _App.window.load_url(url)
    except Exception as exc:  # noqa: BLE001
        log.critical("Startup failed", exc_info=True)
        _splash(f"showError({json.dumps(f'KORA could not start: {exc}  Details are in the logs folder.')})")


def _shutdown() -> None:
    server = _App.server
    if server is not None:
        try:
            server.runtime.close()  # stop llama-server processes (they're hidden now, don't leave them running)
        except Exception:  # noqa: BLE001
            log.exception("Error while stopping model processes")
    log.info("KORA exited.")


def _initial_size(webview) -> tuple[int, int]:
    """Start near full-screen while preserving a small desktop margin."""
    width, height = 1440, 900
    try:
        screens = webview.screens
        screen = screens[0] if screens else None
        if screen:
            width = min(int(screen.width * 0.94), max(720, int(screen.width * 0.88)))
            height = min(int(screen.height * 0.94), max(520, int(screen.height * 0.90)))
    except Exception:  # noqa: BLE001 - retain safe defaults if display metrics are unavailable
        pass
    return width, height


def _open_window() -> bool:
    """Open the splash window immediately; returns False if pywebview is unavailable."""
    try:
        import webview
    except ImportError:
        log.warning("pywebview not installed")
        return False

    api = WindowApi()
    width, height = _initial_size(webview)
    window = webview.create_window(
        "KORA",
        html=SPLASH_HTML,
        width=width,
        height=height,
        min_size=(min(940, width), min(600, height)),
        frameless=True,
        easy_drag=False,  # dragging is handled by .pywebview-drag-region in the CSS
        background_color=WINDOW_BG,
        js_api=api,
    )
    api._window = window
    _App.window = window
    # Persistent profile so appearance settings (localStorage) survive restarts.
    webview.start(_bootstrap, private_mode=False, storage_path=str(WEBVIEW_STORAGE_DIR))
    return True


def main() -> None:
    atexit.register(_shutdown)

    # Automated tooling (src/diagnose.py) runs this with no display.
    if os.getenv("KORA_NO_GUI") == "1":
        from server import KoraServer

        _App.server = KoraServer()
        _App.server.run()
        return

    try:
        if _open_window():
            return
    except Exception as exc:  # noqa: BLE001
        log.critical("Window failed to open", exc_info=True)
        app_logging.fatal_dialog(f"KORA could not open its window:\n{exc}\n\nIf this is a fresh PC, install the Microsoft WebView2 runtime.")
        return

    # Fallback: no pywebview -> regular browser tab.
    import webbrowser

    from server import KoraServer

    _App.server = KoraServer()
    thread = threading.Thread(target=_run_server, daemon=True, name="kora-server")
    thread.start()
    if _wait_for_port(API_HOST, API_PORT, thread=thread):
        webbrowser.open(f"http://{API_HOST}:{API_PORT}/")
    thread.join()


if __name__ == "__main__":
    main()
