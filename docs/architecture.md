# KORA architecture

KORA is a local web application for Windows. A local Flask API serves a static web UI (shown in its own desktop window or the browser), a Python orchestration layer, and two local llama.cpp model services. Python owns file handling, indexing, retrieval, persistence, runtime selection, and tool execution. The language model is used for natural-language understanding and response generation, with tool calls delegated to Python.

## Runtime flow

1. `src/main.py` initializes logging and opens the desktop splash window through pywebview. When pywebview is unavailable, it can run the local server and open the browser instead.
2. `src/tuning.py` inspects the available hardware and produces resource/backend settings.
3. `src/model_runtime.py` locates and starts local llama.cpp chat and embedding servers. Model services bind to loopback URLs configured in `src/config.py`.
4. `src/server.py` creates the Flask application, registers API routes, serves `src/web/`, and connects HTTP requests to the engine.
5. `src/engine.py` coordinates conversations, the tool-agent loop, indexing, retrieval, and model calls.

## Flat Python module responsibilities

| Module | Responsibility |
|---|---|
| `config.py` | Project/data paths, environment settings, model names, and local service URLs. |
| `app_logging.py` | File logging and fatal startup dialogs. |
| `main.py` | Desktop/browser startup, splash screen, window bridge, and shutdown. |
| `server.py` | Flask API routes and static UI serving. |
| `engine.py` | Conversation manager, agent loop, and application engine. |
| `llm.py` | HTTP client for local chat and embedding endpoints, plus system prompt. |
| `tools.py` | Tool registry, JSON schemas, and Python tool implementations. |
| `knowledge.py` | Knowledge store, provenance helpers, retrieval, and ingestion orchestration. |
| `extractor.py` | Supported file formats, text extraction, OCR, normalization, and stable record identifiers. |
| `exporter.py` | Conversation export to PDF and Markdown. |
| `model_runtime.py` | llama.cpp process discovery, startup, readiness, and shutdown. |
| `tuning.py` | Hardware detection and runtime resource profiles. |
| `diagnose.py` | Safe preflight and optional full local diagnostics. |

The web interface lives in `src/web/`. Persistent user data belongs under `data/`, which is created at runtime and excluded from source control. Developer packaging files live in `build/`, helper scripts in `scripts/`.

## Data flow

- Documents are uploaded or placed in `data/workspace/`.
- `extractor.py` extracts text and metadata into normalized records.
- `knowledge.py` stores source metadata, content hashes, embeddings, and retrieval results in the local index under `data/`.
- `llm.py` sends embedding and chat requests to local endpoints. The default ports are 8081 and 8080.
- `tools.py` exposes bounded Python operations to the agent loop. Search and document retrieval return provenance so answers can be associated with their source records.
- Conversation history, app settings, activity, and the knowledge index are persisted in `data/`.

## Extension guidance

Keep paths and model defaults in `config.py`; avoid scattering project-root assumptions through modules. New persistent state should be stored under `DATA_DIR`. Keep UI assets under `src/web/`. Keep developer packaging changes under `build/`. When adding a Python module, update the explicit module-import preflight in `diagnose.py` and the PyInstaller hidden-import list in `build/kora.spec` when the module is dynamically imported.
