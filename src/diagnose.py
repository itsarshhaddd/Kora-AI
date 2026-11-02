"""Safe preflight checks and optional live KORA system diagnostics."""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request

from config import WORKSPACE_DIR, RUNTIME_DIR, MODELS_DIR

ROOT = Path(__file__).resolve().parent.parent

PYTHON = Path(sys.executable)

BASE = "http://127.0.0.1:5175"

TEMP_NAME = "__KORA_FULL_DIAGNOSTIC_V4__.txt"

UPLOAD_NAME = "__KORA_UPLOAD_DIAGNOSTIC_V4__.txt"

TEMP = WORKSPACE_DIR / TEMP_NAME

PASS = WARN = FAIL = 0

SERVER_PROC: subprocess.Popen | None = None

SERVER_STARTED_BY_TEST = False

def banner(title: str):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72, flush=True)

def ok(msg):
    global PASS
    PASS += 1
    print(f"[OK] {msg}", flush=True)

def warn(msg):
    global WARN
    WARN += 1
    print(f"[WARN] {msg}", flush=True)

def fail(msg):
    global FAIL
    FAIL += 1
    print(f"[FAIL] {msg}", flush=True)

def request(method, path, data=None, files=None, timeout=8):
    try:
        if files:
            boundary = "----KORADIAGBOUNDARYV4"
            body = bytearray()
            for name, filename, content in files:
                body.extend(f"--{boundary}\r\n".encode())
                body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
                body.extend(b"Content-Type: text/plain\r\n\r\n")
                body.extend(content)
                body.extend(b"\r\n")
            body.extend(f"--{boundary}--\r\n".encode())
            payload = bytes(body)
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        else:
            payload = None if data is None else json.dumps(data).encode()
            headers = {} if payload is None else {"Content-Type": "application/json"}
        req = urllib.request.Request(BASE + path, data=payload, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return r.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return exc.code, parsed
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

def api_reachable(timeout=1.5):
    code, body = request("GET", "/api/health", timeout=timeout)
    return code == 200, body

def start_server_if_needed():
    global SERVER_PROC, SERVER_STARTED_BY_TEST
    alive, _ = api_reachable()
    if alive:
        ok("KORA API server already reachable on port 5175")
        return True

    print("[INFO] KORA API server is not running; starting the actual main.py server for API tests...", flush=True)
    try:
        env = dict(os.environ)
        env["KORA_NO_GUI"] = "1"  # headless: no window/browser tab during automated diagnostics
        SERVER_PROC = subprocess.Popen(
            [str(PYTHON), str(ROOT / "src" / "main.py")],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        SERVER_STARTED_BY_TEST = True
    except Exception as exc:
        fail(f"Could not start KORA API server: {type(exc).__name__}: {exc}")
        return False

    deadline = time.time() + 30
    while time.time() < deadline:
        alive, body = api_reachable(timeout=1.5)
        if alive:
            ok("KORA API server started and /api/health is reachable")
            return True
        if SERVER_PROC.poll() is not None:
            fail(f"KORA API server exited during startup with code {SERVER_PROC.returncode}")
            return False
        time.sleep(0.5)
    fail("KORA API server did not become reachable within 30 seconds")
    return False

def stop_server_if_started():
    global SERVER_PROC
    if not SERVER_STARTED_BY_TEST or SERVER_PROC is None:
        return
    print("[INFO] Stopping KORA API server started by this diagnostic...", flush=True)
    try:
        SERVER_PROC.terminate()
        SERVER_PROC.wait(timeout=5)
    except Exception:
        try:
            SERVER_PROC.kill()
        except Exception:
            pass

def quick_diagnostic(run_tests: bool = True) -> int:
    """Safe local preflight: no server launch, model load, or user-file mutation."""
    import importlib.util
    import platform
    import shutil

    passed = warnings = failed = 0
    def report(label: str, good: bool, detail: str = "", warning: bool = False):
        nonlocal passed, warnings, failed
        status = "PASS" if good else ("WARN" if warning else "FAIL")
        print(f"[{status}] {label}" + (f": {detail}" if detail else ""), flush=True)
        if good: passed += 1
        elif warning: warnings += 1
        else: failed += 1

    print("=" * 72)
    print("KORA QUICK DIAGNOSTIC (SAFE / NO USER FILE CHANGES)")
    print(f"Project: {ROOT}\nPython:  {sys.executable} ({platform.python_version()}, {platform.architecture()[0]})")
    report("Python 3.11+", sys.version_info >= (3, 11), sys.version.split()[0])
    report("64-bit Python", platform.architecture()[0] == "64bit", platform.architecture()[0])
    required = ["src/config.py", "src/main.py", "src/server.py", "src/engine.py", "src/knowledge.py", "src/tools.py", "src/extractor.py", "src/web/index.html", "src/web/app.js", "src/web/styles.css", "setup_kora.bat", "run_kora.bat", "requirements.txt", "scripts/diagnose_kora.bat", "scripts/download_assets.ps1"]
    missing = [name for name in required if not (ROOT / name).is_file()]
    report("Required project files", not missing, ", ".join(missing) if missing else f"{len(required)} files present")
    modules = {"flask":"API server", "webview":"desktop window (optional browser fallback)", "werkzeug":"HTTP routing", "pypdf":"PDF extraction", "pymupdf":"PDF rendering", "docx":"DOCX", "pptx":"PPTX", "openpyxl":"XLSX", "xlrd":"XLS", "PIL":"image support", "pytesseract":"OCR bridge", "requests":"HTTP client", "reportlab":"PDF export"}
    for mod, purpose in modules.items():
        present = importlib.util.find_spec(mod) is not None
        report(f"Package {mod}", present, purpose, warning=(mod == "webview" and not present))
    try:
        import config
        for key in ("CHAT_MODEL", "EMBED_MODEL"):
            filename = getattr(config, key)
            exists = (config.MODELS_DIR / filename).is_file()
            report(f"Model {key}", exists, filename, warning=True)
    except Exception as exc:
        report("Model configuration", False, f"{type(exc).__name__}: {exc}")
    runtime_candidates = [RUNTIME_DIR / name for name in ("llama-server.exe", "llama-server")]
    runtime_candidates.extend(RUNTIME_DIR / backend / name for backend in ("cuda", "vulkan", "sycl", "cpu") for name in ("llama-server.exe", "llama-server"))
    server = next((candidate for candidate in runtime_candidates if candidate.is_file()), None)
    report("llama-server runtime", server is not None, str(server or (RUNTIME_DIR / "llama-server.exe")), warning=True)
    for command, label in (("tesseract", "Tesseract OCR"), ("soffice", "LibreOffice")):
        found = shutil.which(command)
        report(label, bool(found), found or "not found; optional for some file types", warning=True)
    # Import every application module from the same flat src path used by the
    # Windows launch scripts. This catches missing names and circular imports,
    # not just syntax errors.
    sys.path.insert(0, str(ROOT / "src"))
    for module_name in (
        "config", "app_logging", "llm", "extractor", "knowledge", "tools",
        "engine", "exporter", "tuning", "model_runtime", "server", "main",
    ):
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            report(f"Import {module_name}", False, f"{type(exc).__name__}: {exc}")
        else:
            report(f"Import {module_name}", True)

    compile_cmd = [sys.executable, "-m", "compileall", "-q", "src"]
    result = subprocess.run(compile_cmd, cwd=ROOT, capture_output=True, text=True)
    report("Python compilation", result.returncode == 0, (result.stdout + result.stderr).strip() or "all modules compiled")
    node = shutil.which("node")
    if node:
        for js in ("src/web/app.js", "src/web/structured-result.js"):
            r = subprocess.run([node, "--check", js], cwd=ROOT, capture_output=True, text=True)
            report(f"JavaScript syntax: {js}", r.returncode == 0, (r.stdout + r.stderr).strip() or "syntax valid")
    else:
        report("Node.js syntax checks", False, "Node.js not installed", warning=True)
    if run_tests:
        report("Automated tests", False, "No automated test suite is included in this source archive.", warning=True)
    print(f"\nSUMMARY: {passed} PASS | {warnings} WARN | {failed} FAIL")
    print("This quick mode does not start KORA or load models. Importing config may create empty data directories.")
    return 1 if failed else 0

def main():
    parser = argparse.ArgumentParser(description="KORA system diagnostics")
    parser.add_argument("--quick", action="store_true", help="safe preflight only; does not start server or load models")
    parser.add_argument("--models-only", action="store_true", help="check only the configured model files")
    parser.add_argument("--no-tests", action="store_true", help="compatibility option; no test suite is bundled")
    args = parser.parse_args()
    if args.models_only:
        return check_models()
    if args.quick:
        return quick_diagnostic(run_tests=False)
    global FAIL
    print("=" * 72)
    print("KORA FULL SYSTEM / API DIAGNOSTIC")
    print("=" * 72)
    print(f"Project: {ROOT}")
    print(f"Python:  {PYTHON}")

    banner("1. PROJECT STRUCTURE")
    required = [
        "src/config.py", "src/main.py", "src/server.py", "src/engine.py", "src/llm.py",
        "src/knowledge.py", "src/extractor.py", "src/tools.py", "src/model_runtime.py",
        "src/exporter.py", "src/web/index.html", "src/web/app.js", "src/web/structured-result.js", "src/web/styles.css",
    ]
    for rel in required:
        (ok if (ROOT / rel).exists() else fail)(rel)
    for d in ["data", "data/workspace", "data/models", "data/runtime", "data/logs"]:
        (ok if (ROOT / d).is_dir() else fail)(f"Directory exists: {d}/")

    banner("2. IMPORTS / ACTUAL FUNCTION CONTRACTS")
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from config import DATA_DIR, WORKSPACE_DIR, INDEX_PATH, CHAT_URL, EMBED_URL, API_PORT, CHAT_MODEL, EMBED_MODEL, MODELS_DIR, RUNTIME_DIR
        from extractor import extract_file, normalize_records
        from engine import KoraEngine
        from knowledge import KnowledgeStore
        from knowledge import SearchEngine
        from llm import LLMClient
        from tools import ToolRegistry
        ok("Core modules imported")
        print(f"[INFO] normalize_records signature: {inspect.signature(normalize_records)}")
        print(f"[INFO] LLMClient.chat signature: {inspect.signature(LLMClient.chat)}")
        if "max_tokens" in str(inspect.signature(LLMClient.chat)):
            ok("LLMClient.chat accepts max_tokens")
        else:
            ok("LLMClient.chat contract verified; diagnostic will call it without unsupported max_tokens")
    except Exception as exc:
        fail(f"Core imports failed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    banner("3. PATHS / MODELS / RUNTIME")
    for p in [DATA_DIR, WORKSPACE_DIR, MODELS_DIR, RUNTIME_DIR]:
        (ok if p.exists() else fail)(f"Path accessible: {p}")
    for m in [CHAT_MODEL, EMBED_MODEL]:
        (ok if (MODELS_DIR / m).exists() else fail)(f"Model present: {m}")
    runtime_candidates = [RUNTIME_DIR / name for name in ("llama-server.exe", "llama-server")]
    runtime_candidates += [RUNTIME_DIR / backend / name for backend in ("cuda", "vulkan", "sycl", "cpu") for name in ("llama-server.exe", "llama-server")]
    runtime_found = next((path for path in runtime_candidates if path.is_file()), None)
    (ok if runtime_found else fail)(f"llama-server found ({runtime_found or 'not found'})")
    for mod, why in [("reportlab", "PDF chat export"), ("PIL", "image OCR")]:
        try:
            __import__(mod)
            ok(f"Python package '{mod}' available ({why})")
        except Exception:
            fail(f"Python package '{mod}' is missing ({why}) -- re-run setup_kora.bat")
    try:
        import exporter as chat_export
        pdf = chat_export.to_pdf([{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "# Hi\n- one\n- two\n\n**bold** text", "sources": [{"source": "a.pdf", "page": 2}]}])
        (ok if pdf[:5] == b"%PDF-" else fail)(f"PDF export renders a valid PDF ({len(pdf):,} bytes)")
    except Exception as exc:
        fail(f"PDF export self-test failed: {type(exc).__name__}: {exc}")
    print(f"[INFO] Chat URL: {CHAT_URL} | Embed URL: {EMBED_URL} | API: http://127.0.0.1:{API_PORT}")

    banner("4. LOCAL MODEL SERVICES")
    try:
        from model_runtime import ModelRuntime
        rt = ModelRuntime()
        rt.ensure_running()
        ok("KORA runtime ensure_running() completed")
    except Exception as exc:
        warn(f"Runtime ensure_running() returned: {type(exc).__name__}: {exc}")
    for label, url in [("Chat", CHAT_URL), ("Embedding", EMBED_URL)]:
        try:
            with urllib.request.urlopen(url + "/v1/models", timeout=10) as r:
                (ok if r.status == 200 else fail)(f"{label} service /v1/models -> HTTP {r.status}")
        except Exception as exc:
            fail(f"{label} service unreachable: {exc}")

    banner("5. LLM CLIENT")
    try:
        q = LLMClient(timeout=30)
        emb = q.embed(["KORA embedding diagnostic"])
        (ok if emb and emb[0] else fail)(f"Embedding endpoint works; vector length={len(emb[0]) if emb else 0}")
        if emb and emb[0] and len(emb[0]) != 768 and "nomic-embed-text" in EMBED_MODEL.lower():
            warn(f"Nomic Embed Text v1.5 normally returns 768 dimensions, got {len(emb[0])} -- check the embedding model/pooling settings")
        qv = q.embed(["a query"], kind="query")
        (ok if qv and qv[0] else fail)("Query-mode embedding (task prefix) works")
        # IMPORTANT: this matches the real V13 LLMClient.chat contract.
        chat = q.chat([{"role": "user", "content": "Reply with exactly OK"}])
        text = json.dumps(chat, ensure_ascii=False)
        (ok if chat else fail)("LLMClient.chat() returned a response")
        if "OK" in text.upper():
            ok("LLMClient.chat() response contains OK")
        else:
            warn(f"LLMClient.chat() responded but did not contain literal OK: {text[:300]}")
    except Exception as exc:
        fail(f"LLM client test failed: {type(exc).__name__}: {exc}")

    banner("6. REAL EXTRACTION + INGESTION")
    TEMP.parent.mkdir(parents=True, exist_ok=True)
    TEMP.write_text("KORA FULL DIAGNOSTIC V4\nDiagnostic revenue value: 405000\nCompany: DiagnosticCo\n", encoding="utf-8")
    print(f"[INFO] Created temporary document: {TEMP}")
    try:
        raw = extract_file(TEMP)
        records = normalize_records(raw)
        ok(f"Extraction + normalization succeeded: raw={len(raw)}, normalized={len(records)}")
        if any("405000" in str(r.get("content", "")) for r in records):
            ok("Diagnostic content survived extraction/normalization")
        else:
            fail("Diagnostic content missing after normalization")
    except Exception as exc:
        fail(f"Extraction/normalization failed: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    banner("7. REAL ENGINE SYNC + INDEX")
    engine = None
    try:
        engine = KoraEngine()
        result = engine.sync()
        print(f"[INFO] sync() -> {result}")
        (ok if result.get("failed", 0) == 0 else fail)("KoraEngine.sync() completed with zero failed files")
        idx = KnowledgeStore().data
        matches = [r for r in idx.get("records", []) if r.get("source") == TEMP_NAME or str(r.get("path", "")).endswith(TEMP_NAME)]
        (ok if matches else fail)(f"Diagnostic record persisted ({len(matches)} matching record(s))")
        (ok if matches and matches[0].get("embedding") else fail)(f"Persisted embedding length={len(matches[0]['embedding']) if matches and matches[0].get('embedding') else 0}")
    except Exception as exc:
        fail(f"Engine/index test failed: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    banner("8. RETRIEVAL + TOOLS")
    try:
        store = KnowledgeStore()
        search = SearchEngine(store, LLMClient())
        hits = search.search("What is the diagnostic revenue value 405000?", top_k=5)
        (ok if hits else fail)("SearchEngine retrieved diagnostic data")
        from tools import register_builtins
        reg = ToolRegistry()
        register_builtins(reg, store, search, engine.conversations if engine else None)
        ok("Built-in tools registered")
    except Exception as exc:
        fail(f"Retrieval/tools test failed: {type(exc).__name__}: {exc}")

    banner("9. FLASK API CONTRACTS")
    api_ok = start_server_if_needed()
    if api_ok:
        safe = [
            ("GET", "/"), ("GET", "/index.html"), ("GET", "/api/health"), ("GET", "/api/activity"),
            ("GET", "/api/documents"), ("GET", "/api/library"), ("GET", "/api/progress"), ("GET", "/api/index/report"), ("GET", "/api/overview"),
            ("GET", "/api/profile"), ("GET", "/api/audit"), ("GET", "/api/pins"), ("GET", "/api/setup"),
            ("GET", "/api/settings"), ("GET", "/api/ui-settings"), ("GET", "/api/sources"), ("GET", "/api/gaps"),
            ("GET", "/app.js"), ("GET", "/structured-result.js"), ("GET", "/styles.css"), ("GET", "/api/capabilities"),
        ]
        for method, path in safe:
            print(f"[TEST] {method} {path}", flush=True)
            code, body = request(method, path)
            (ok if code == 200 else fail)(f"{method} {path} -> HTTP {code}")
        posts = [("/api/profile", {}), ("/api/ui-settings", {}), ("/api/settings", {}), ("/api/pins", {})]
        for path, payload in posts:
            print(f"[TEST] POST {path}", flush=True)
            code, _ = request("POST", path, payload)
            (ok if code == 200 else fail)(f"POST {path} -> HTTP {code}")
        for method, path in [("DELETE", "/api/pins"), ("DELETE", "/api/audit")]:
            print(f"[TEST] {method} {path}", flush=True)
            code, _ = request(method, path)
            (ok if code == 200 else fail)(f"{method} {path} -> HTTP {code}")
        for path in ["/api/ai/start", "/api/ai/stop"]:
            print(f"[TEST] POST {path}", flush=True)
            code, _ = request("POST", path)
            (ok if code == 405 else fail)(f"POST {path} -> HTTP {code} (expected: manual control unsupported)")
        code, _ = request("GET", "/api/documents/__missing_v4__.txt")
        (ok if code == 404 else fail)(f"GET missing document -> HTTP {code}")

        banner("10. UPLOAD -> REINDEX -> SOURCE -> PREVIEW")
        upload_content = b"Uploaded diagnostic value: 918273. Company: UploadCo."
        print("[TEST] POST /api/sources (multipart upload)", flush=True)
        code, _ = request("POST", "/api/sources", files=[("file", UPLOAD_NAME, upload_content)])
        (ok if code == 200 else fail)(f"POST /api/sources upload -> HTTP {code}")
        print("[TEST] POST /api/reindex", flush=True)
        code, _ = request("POST", "/api/reindex")
        (ok if code == 200 else fail)(f"POST /api/reindex -> HTTP {code}")
        print("[TEST] GET /api/sources", flush=True)
        code, body = request("GET", "/api/sources")
        found = code == 200 and isinstance(body, dict) and any(s.get("source") == UPLOAD_NAME for s in body.get("sources", []))
        (ok if found else fail)("Uploaded document visible through /api/sources")
        print("[TEST] GET uploaded document", flush=True)
        code, _ = request("GET", "/api/documents/" + UPLOAD_NAME)
        (ok if code == 200 else fail)(f"GET uploaded document -> HTTP {code}")
        print("[TEST] GET uploaded preview", flush=True)
        code, _ = request("GET", "/api/preview/" + UPLOAD_NAME)
        (ok if code == 200 else fail)(f"GET uploaded preview -> HTTP {code}")
        p = WORKSPACE_DIR / UPLOAD_NAME
        if p.exists():
            p.unlink()
        print("[TEST] POST /api/reindex after deleting uploaded file", flush=True)
        code, _ = request("POST", "/api/reindex")
        (ok if code == 200 else fail)(f"Reindex after deleting uploaded file -> HTTP {code}")

        banner("11. REAL CHAT API")
        print("[TEST] POST /api/chat", flush=True)
        code, body = request("POST", "/api/chat", {"message": "What is the diagnostic revenue value in the KORA diagnostic document?", "session_id": ""}, timeout=120)
        if code == 200 and isinstance(body, dict) and body.get("answer"):
            answer = str(body["answer"])
            ok("POST /api/chat returned an answer")
            normalized = "".join(ch for ch in answer if ch.isdigit())
            (ok if "405000" in normalized else warn)(f"Chat answer expected 405000; answer={answer[:300]}")
        else:
            fail(f"/api/chat failed: HTTP {code} {body}")

    chat_session = body.get("session_id") if api_ok and code == 200 and isinstance(body, dict) else None

    if api_ok:
        banner("11b. EXPORT")
        if chat_session:
            for fmt in ("pdf", "md"):
                try:
                    with urllib.request.urlopen(f"{BASE}/api/library/{chat_session}/export.{fmt}", timeout=60) as r:
                        raw_ = r.read()
                    good = raw_[:5] == b"%PDF-" if fmt == "pdf" else len(raw_) > 20
                    (ok if good else fail)(f"Export chat as {fmt.upper()} -> {len(raw_):,} bytes")
                except Exception as exc:
                    fail(f"Export chat as {fmt.upper()} failed: {type(exc).__name__}: {exc}")
        else:
            warn("No chat session from step 11 -- skipping export endpoint test")

    banner("12. DELETION RECONCILIATION")
    try:
        if TEMP.exists():
            TEMP.unlink()
        result = KoraEngine().sync()
        print(f"[INFO] cleanup sync() -> {result}")
        stale = [d for d in KnowledgeStore().document_list() if d.get("source") == TEMP_NAME or str(d.get("path", "")).endswith(TEMP_NAME)]
        (ok if not stale else fail)("Deleted diagnostic file was removed from knowledge_index.json")
        stale2 = [d for d in KnowledgeStore().document_list() if d.get("source") == UPLOAD_NAME]
        (ok if not stale2 else fail)("Uploaded diagnostic cleanup is clean")
    except Exception as exc:
        fail(f"Deletion reconciliation test failed: {type(exc).__name__}: {exc}")

    banner("FINAL RESULT")
    print(f"PASSED : {PASS}\nWARNINGS: {WARN}\nFAILED : {FAIL}")
    if FAIL:
        print("\n[FAILURE] One or more real checks failed. Review the first [FAIL] above.")
        return 1
    if WARN:
        print("\n[PASS WITH WARNINGS] Core system checks passed; review warnings above.")
    else:
        print("\n[SUCCESS] KORA full-system diagnostic passed.")
    return 0



def check_models() -> int:
    """Check configured model files without starting any runtime services."""
    from config import CHAT_MODEL, EMBED_MODEL, MODELS_DIR

    missing = [name for name in (CHAT_MODEL, EMBED_MODEL) if not (MODELS_DIR / name).is_file()]
    for name in missing:
        print(f"[ERROR] Missing data/models/{name} (check the configured filename)")
    if missing:
        return 2
    print("[OK] Configured model files are present.")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        code = 130
    except Exception:
        traceback.print_exc()
        code = 1
    finally:
        stop_server_if_started()
    raise SystemExit(code)