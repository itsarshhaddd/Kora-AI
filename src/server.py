"""Flask API server and static web application routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import re
import subprocess
import sys
import unicodedata
import logging
import threading
from urllib.parse import quote
from flask import Flask, jsonify, request, send_from_directory, Response
from werkzeug.utils import secure_filename

from config import (API_HOST, API_PORT, WEB_DIR,
                    WORKSPACE_DIR, CHAT_MODEL, EMBED_MODEL, CHAT_URL, EMBED_URL,
                    SETTINGS_PATH, LOG_DIR)
from engine import KoraEngine
import exporter as chat_export
from model_runtime import ModelRuntime, RuntimeErrorKora
import tuning


class KoraServer:
    def __init__(self) -> None:
        self.app = Flask(__name__, static_folder=None)
        # Document locators are full file paths ("/home/x/a.pdf" arrives as "//home/x/a.pdf").
        self.app.url_map.merge_slashes = False

        from werkzeug.routing import PathConverter

        class AnyPathConverter(PathConverter):
            regex = ".+"  # unlike "path", also accepts a leading "/" (absolute POSIX paths)
            part_isolating = False

        self.app.url_map.converters["anypath"] = AnyPathConverter
        self.engine = KoraEngine()
        self.runtime = ModelRuntime()
        self.boot_error: str | None = None
        self._reindex_lock = __import__("threading").Lock()
        self._reindex_thread = None
        self._routes()

    def _find_document(self, name: str) -> dict | None:
        """Look a document up by filename or by full path (the UI sends the path).
        Tolerates the leading "/" a URL adds and either slash direction."""
        def norm(value: str) -> str:
            return str(value or "").replace("\\", "/").strip().lstrip("/").lower()

        wanted = norm(name)
        docs = self.engine.store.document_list()
        exact = next((d for d in docs if d.get("path") == name or d.get("source") == name), None)
        if exact:
            return exact
        return next((d for d in docs if norm(d.get("path")) == wanted), None)

    def _indexing(self) -> bool:
        progress = self.engine.progress
        return bool(progress.get("active")) and progress.get("stage") == "indexing"

    @staticmethod
    def _title(messages) -> str:
        first = next((m for m in messages if m.get("role") == "user"), None)
        if not first:
            return "Conversation"
        return first.get("content") or "Conversation"

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize an uploaded filename for safe on-disk storage.

        This mirrors werkzeug's secure_filename() (ASCII-fold, drop path
        separators and unsafe characters) but does NOT strip leading/
        trailing '_' characters the way secure_filename()'s final
        .strip('._') does. That stripping is meant to guard against
        dotfile/traversal tricks ('.', '..', '.hidden') but it also
        silently mangles ordinary filenames that start or end with
        underscores (e.g. "__report__.txt" -> "report__.txt"), which
        breaks any client that expects the returned/indexed source name
        to match what it uploaded. We only strip leading dots (the
        actual traversal/hidden-file risk) and leave underscores intact.
        """
        name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
        name = name.replace("/", " ").replace("\\", " ")
        name = re.sub(r"[^A-Za-z0-9_.\- ]", "", name)
        name = "_".join(name.split())
        name = name.lstrip(".")
        return name

    def _routes(self) -> None:
        app = self.app

        @app.get("/")
        def index():
            return send_from_directory(WEB_DIR, "index.html")

        @app.get("/<path:filename>")
        def static_file(filename: str):
            return send_from_directory(WEB_DIR, filename)

        @app.get("/api/health")
        def health():
            chat_ok = self.runtime._ready(CHAT_URL)
            embed_ok = self.runtime._ready(EMBED_URL)
            indexing_active = self._indexing()
            ready = chat_ok and embed_ok

            if not ready:
                step, percent, label = "ai", 55, "Starting the local AI"
                message = "Waiting for the local models to come online…"
            elif indexing_active:
                step, percent, label = "index", 85, "Indexing your files"
                message = self.engine.progress.get("message") or "Indexing your knowledge base…"
            else:
                step, percent, label = "ready", 100, "Ready"
                message = "KORA is ready."

            boot = {
                "ready": ready,
                "step": step,
                "percent": percent,
                "cap": min(99, percent + 8),
                "label": label,
                "message": message,
            }
            if not ready and self.boot_error:
                boot.update({"label": "Couldn't start the local AI", "message": self.boot_error})
            payload = {"ok": True, "engine": "python-agent", "chat_runtime": chat_ok, "embedding_runtime": embed_ok, "llm_online": chat_ok, "ready": ready, "starting": not ready, "ai_starting": not ready, "boot": boot, "model": CHAT_MODEL, "embedding_model": EMBED_MODEL}
            if not ready and self.boot_error:
                payload["error"] = self.boot_error
            return jsonify(payload)

        @app.post("/api/chat")
        def chat():
            body = request.get_json(silent=True) or {}
            try:
                result = self.engine.chat(str(body.get("session_id") or ""), str(body.get("message", body.get("question", ""))).strip())
                return jsonify(result)
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        @app.post("/api/chat/stream")
        def chat_stream():
            # Streaming needs the request body read up-front: it's parsed on
            # this (request) thread, but the generator below runs lazily as
            # the WSGI server pulls chunks to write to the socket, by which
            # point Flask's request context may already be gone.
            body = request.get_json(silent=True) or {}
            session_id = str(body.get("session_id") or "")
            message = str(body.get("message", body.get("question", ""))).strip()

            def generate():
                try:
                    for event in self.engine.chat_stream(session_id, message):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception as exc:
                    yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"

            return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        @app.get("/api/activity")
        def activity():
            return jsonify(self.engine.progress)

        @app.post("/api/reindex")
        def reindex():
            with self._reindex_lock:
                if self._indexing() or (self._reindex_thread and self._reindex_thread.is_alive()):
                    return jsonify({"ok": True, "status": "already_running"})

                def work() -> None:
                    try:
                        self.engine.sync()
                    except Exception:  # noqa: BLE001
                        logging.getLogger("kora.server").exception("Reindex failed")

                self._reindex_thread = threading.Thread(target=work, daemon=True, name="kora-reindex")
                self.engine.set_progress("indexing", "Starting…", 0)
                self._reindex_thread.start()
            return jsonify({"ok": True, "status": "started"})

        @app.get("/api/documents")
        def documents():
            docs = self.engine.store.document_list()
            return jsonify({"documents": docs, "duplicates": []})

        @app.get("/api/library")
        def library():
            sessions = []
            items = [i for i in self.engine.conversations.list() if i.get("messages")]
            items.sort(key=lambda i: max((m.get("ts") or 0) for m in i["messages"]), reverse=True)
            for item in items:
                messages = item.get("messages", [])
                title = self._title(messages)
                sessions.append({"id": item["id"], "title": title[:80], "messages": messages})
            return jsonify({"sessions": sessions})

        @app.get("/api/library/<session_id>")
        def library_get(session_id: str):
            messages = self.engine.conversations.sessions.get(session_id)
            if messages is None:
                return jsonify({"error": "Conversation not found"}), 404
            title = self._title(messages)
            return jsonify({"id": session_id, "title": title[:80], "messages": messages})

        @app.get("/api/library/<session_id>/export.<fmt>")
        def library_export(session_id: str, fmt: str):
            messages = self.engine.conversations.sessions.get(session_id)
            if not messages:
                return jsonify({"error": "Conversation not found or empty"}), 404
            title = chat_export.conversation_title(messages)
            try:
                if fmt == "pdf":
                    data, mime = chat_export.to_pdf(messages), "application/pdf"
                elif fmt in ("md", "markdown"):
                    data, mime, fmt = chat_export.to_markdown(messages).encode("utf-8"), "text/markdown; charset=utf-8", "md"
                else:
                    return jsonify({"error": "Unsupported format"}), 400
            except ImportError as exc:
                return jsonify({"error": f"PDF export needs the 'reportlab' package ({exc}). Run setup_kora.bat again."}), 500
            except Exception as exc:
                return jsonify({"error": f"Export failed: {exc}"}), 500
            name = chat_export.safe_filename(title, fmt)
            # HTTP headers must be latin-1: a Hindi/emoji title in the filename would
            # otherwise crash the response with "Internal Server Error". Send an
            # ASCII-safe name plus the full UTF-8 name via RFC 5987 filename*.
            ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or f"KORA_conversation.{fmt}"
            ascii_name = ascii_name.replace('"', "")
            return Response(data, mimetype=mime, headers={
                "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
                "X-Export-Filename": ascii_name,
            })

        @app.delete("/api/library/<session_id>")
        def library_delete(session_id: str):
            self.engine.conversations.sessions.pop(session_id, None)
            self.engine.conversations.save()
            return jsonify({"ok": True})

        @app.post("/api/sources")
        def upload():
            file = request.files.get("file")
            if not file or not file.filename:
                return jsonify({"error": "No file supplied"}), 400

            safe = self._sanitize_filename(file.filename)
            if not safe:
                return jsonify({"error": "Invalid filename"}), 400

            # Optional company/workspace folder.  If omitted, keep the file
            # at the workspace root so existing clients remain compatible.
            company = str(request.form.get("company", "")).strip()
            if company:
                company = secure_filename(company)
                if not company:
                    return jsonify({"error": "Invalid company name"}), 400
                target_dir = (WORKSPACE_DIR / company).resolve()
            else:
                target_dir = WORKSPACE_DIR.resolve()

            # Optional subfolder ("Reports/2026"), sanitised segment by segment.
            folder = str(request.form.get("folder", "")).replace("\\", "/")
            if folder.strip():
                for part in folder.split("/"):
                    part = secure_filename(part.strip())
                    if part:
                        target_dir = target_dir / part

            if WORKSPACE_DIR.resolve() not in target_dir.parents and target_dir != WORKSPACE_DIR.resolve():
                return jsonify({"error": "Invalid destination"}), 400

            target_dir.mkdir(parents=True, exist_ok=True)
            target = (target_dir / safe).resolve()
            if WORKSPACE_DIR.resolve() not in target.parents:
                return jsonify({"error": "Invalid destination"}), 400

            file.save(target)

            # Upload is an ingestion operation, not merely a filesystem copy.
            # Sync immediately so the same KoraEngine instance updates the
            # knowledge store before the API reports success.
            try:
                result = self.engine.sync()
            except Exception as exc:
                try:
                    target.unlink(missing_ok=True)
                except Exception:
                    pass
                return jsonify({"ok": False, "filename": safe, "error": str(exc)}), 500

            resolved = str(target.resolve())
            document = next(
                (doc for doc in self.engine.store.document_list()
                 if str(doc.get("path", "")) == resolved),
                None,
            )

            if document is None:
                return jsonify({
                    "ok": False,
                    "filename": safe,
                    "status": "not_indexed",
                    "sync": result,
                    "error": "File was saved but KORA did not register it in the knowledge index.",
                }), 500

            return jsonify({
                "ok": True,
                "filename": safe,
                "company": company or None,
                "folder": folder.strip() or None,
                "status": "indexed",
                "document": document,
                "sync": result,
            })

        @app.get("/api/index/report")
        def index_report():
            return jsonify(self.engine.last_sync_report)

        @app.get("/api/progress")
        def progress():
            # "active" here means *indexing* (chat turns also set engine.progress).
            state = dict(self.engine.progress)
            state["active"] = self._indexing()
            return jsonify(state)

        @app.get("/api/overview")
        def overview():
            docs = self.engine.store.document_list()
            return jsonify({"documents": len(docs), "indexed_documents": len(docs), "records": len(self.engine.store.all_records()), "model": CHAT_MODEL, "embedding_model": EMBED_MODEL})

        @app.get("/api/profile")
        def profile_get():
            return jsonify({"profile": {"name": "", "role": "user"}})

        @app.post("/api/profile")
        def profile_post():
            return jsonify({"profile": request.get_json(silent=True) or {}})

        @app.get("/api/audit")
        def audit():
            return jsonify({"events": []})

        @app.delete("/api/audit")
        def clear_audit():
            return jsonify({"ok": True})

        @app.get("/api/pins")
        def pins_get():
            return jsonify({"pins": []})

        @app.post("/api/pins")
        def pins_post():
            return jsonify({"pins": []})

        @app.delete("/api/pins")
        def pins_delete():
            return jsonify({"pins": []})

        @app.get("/api/setup")
        def setup():
            return jsonify({"ready": self.runtime._ready(CHAT_URL) and self.runtime._ready(EMBED_URL), "models": [CHAT_MODEL], "recommended_model": CHAT_MODEL, "settings": {"model": CHAT_MODEL}})

        @app.get("/api/settings")
        def settings_get():
            data = {"model": CHAT_MODEL, "embedding_model": EMBED_MODEL}
            if SETTINGS_PATH.exists():
                try:
                    data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
                except Exception:
                    pass
            return jsonify(data)

        @app.post("/api/settings")
        def settings_post():
            body = request.get_json(silent=True) or {}
            body["model"] = CHAT_MODEL
            body["embedding_model"] = EMBED_MODEL
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            return jsonify({"ok": True, **body})

        @app.get("/api/system")
        def system_info():
            return jsonify(tuning.describe(self.runtime.active))

        @app.post("/api/system/performance")
        def system_performance():
            body = request.get_json(silent=True) or {}
            prefs = tuning.save_prefs(str(body.get("profile", "auto")), bool(body.get("force_gpu", False)))
            return jsonify({"ok": True, "prefs": prefs, "restart_required": True})

        @app.post("/api/open-logs")
        def open_logs():
            try:
                if os.name == "nt":
                    os.startfile(LOG_DIR)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(LOG_DIR)])
                else:
                    subprocess.Popen(["xdg-open", str(LOG_DIR)])
                return jsonify({"ok": True})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        @app.get("/api/ui-settings")
        def ui_settings_get():
            return jsonify({"settings": {}})

        @app.post("/api/ui-settings")
        def ui_settings_post():
            return jsonify({"settings": request.get_json(silent=True) or {}})

        @app.post("/api/clear")
        def clear_chat():
            return jsonify({"ok": True})

        @app.post("/api/open-workspace")
        def open_workspace():
            try:
                if os.name == "nt":
                    os.startfile(WORKSPACE_DIR)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(WORKSPACE_DIR)])
                else:
                    subprocess.Popen(["xdg-open", str(WORKSPACE_DIR)])
                return jsonify({"ok": True})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        @app.get("/api/documents/<anypath:name>")
        def document_detail(name: str):
            doc = self._find_document(name)
            if doc:
                return jsonify(doc)
            return jsonify({"error": "Document not found"}), 404

        @app.get("/api/source-context/<record_id>")
        def source_context(record_id: str):
            for record in self.engine.store.all_records():
                if record.get("record_id") == record_id:
                    parts = [f"Page {record['page']}" if record.get("page") is not None else None,
                             f"Sheet {record['sheet']}" if record.get("sheet") else None,
                             f"Row {record['row']}" if record.get("row") is not None else None,
                             f"Slide {record['slide']}" if record.get("slide") is not None else None,
                             record.get("section")]
                    location = " · ".join(str(x) for x in parts if x) or record.get("path") or "Workspace source"
                    return jsonify({"source": record.get("source"), "location": location, "path": record.get("path"), "content": record.get("content", "")})
            return jsonify({"error": "Source not found"}), 404

        @app.get("/api/preview/<anypath:name>")
        def preview(name: str):
            doc = self._find_document(name)
            if not doc:
                return jsonify({"error": "Document not found"}), 404
            
            try:
                offset = int(request.args.get("offset", "0"))
                page = build_preview_page(
                    self.engine.store.all_records(), doc["document_id"], offset=offset,
                )
            except (TypeError, ValueError) as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify(page)

        @app.delete("/api/sources/<anypath:name>")
        def delete_source(name: str):
            doc = self._find_document(name)
            if not doc:
                return jsonify({"error": "Document not found"}), 404
            document_id = doc["document_id"]
            physical_path = Path(str(doc.get("path", ""))).resolve()
            self.engine.store.remove_document(document_id)
            # Keep the filesystem and knowledge index consistent.
            # Never allow deletion outside workspace.
            workspace_root = WORKSPACE_DIR.resolve()
            if workspace_root in physical_path.parents and physical_path.exists():
                try:
                    physical_path.unlink()
                except OSError as exc:
                    return jsonify({
                        "ok": False,
                        "error": f"Index entry removed but physical file could not be deleted: {exc}",
                    }), 500
            self.engine.finish_progress()
            return jsonify({"ok": True, "document_id": document_id})

        @app.get("/api/sources")
        def sources():
            return jsonify({"sources": self.engine.store.document_list()})

        @app.post("/api/ai/start")
        def ai_start():
            return jsonify({"ok": False, "error": "AI runtime is managed automatically by KORA; manual start/stop is not supported."}), 405

        @app.post("/api/ai/stop")
        def ai_stop():
            return jsonify({"ok": False, "error": "AI runtime is managed automatically by KORA; manual start/stop is not supported."}), 405

        @app.get("/api/gaps")
        def gaps():
            return jsonify({"gaps": []})

    def _start_models(self) -> None:
        """Start the llama.cpp services in the background so the HTTP server (and
        therefore the window) is available immediately; /api/health reports
        progress to the boot screen."""
        log = logging.getLogger("kora.server")
        try:
            self.runtime.ensure_running()
            threading.Thread(target=self.engine.sync, daemon=True, name="kora-sync").start()
        except RuntimeErrorKora as exc:
            log.error("Runtime not ready: %s", exc)
            if exc.fatal:
                # Missing file / crashed process: will never resolve on its own,
                # so surface it to the UI instead of an endless boot screen.
                self.boot_error = str(exc)
            else:
                log.warning("Model processes are still starting in the background; the UI updates once they respond.")
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected failure starting the model runtime")
            self.boot_error = f"Unexpected error starting the AI: {exc}"

    def run(self) -> None:
        threading.Thread(target=self._start_models, daemon=True, name="kora-models").start()
        self.app.run(host=API_HOST, port=API_PORT, threaded=True)



PREVIEW_PAGE_CHARS = 40_000

def build_preview_page(records: list[dict[str, Any]], document_id: str, offset: int = 0,
                       page_size: int = PREVIEW_PAGE_CHARS) -> dict[str, Any]:
    """Build a bounded document preview page with explicit continuation metadata."""
    try:
        start = int(offset)
        size = int(page_size)
    except (TypeError, ValueError) as exc:
        raise ValueError("offset and page_size must be integers") from exc
    if start < 0:
        raise ValueError("offset cannot be negative")
    if size < 1:
        raise ValueError("page_size must be at least 1")

    chunks = [
        str(record.get("content", "")).strip()
        for record in records
        if record.get("document_id") == document_id and str(record.get("content", "")).strip()
    ]
    full_text = "\n\n".join(chunks)
    page = full_text[start:start + size]
    next_offset = start + len(page) if start + len(page) < len(full_text) else None
    return {
        "text": page,
        "truncated": next_offset is not None,
        "has_more": next_offset is not None,
        "offset": start,
        "limit": size,
        "total_chars": len(full_text),
        "next_offset": next_offset,
    }
