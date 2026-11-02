"""Conversation persistence, tool-agent loop, and the KORA application engine."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from config import CONVERSATIONS_PATH, MAX_AGENT_STEPS
from llm import LLMClient, SYSTEM_PROMPT
from knowledge import IngestionManager, KnowledgeStore, SearchEngine
from tools import ToolRegistry, register_builtins



class ConversationManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.sessions: dict[str, list[dict[str, Any]]] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            # Sessions must be a {session_id: [messages]} dict. Older/corrupt
            # files have shown up here as a bare "[]" list, which silently
            # breaks every dict op (.setdefault, membership, etc.) the first
            # time a chat is sent. Treat anything non-dict as empty instead
            # of crashing the whole /api/chat endpoint.
            self.sessions = data if isinstance(data, dict) else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.sessions, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def create(self) -> str:
        session_id = uuid.uuid4().hex
        with self.lock:
            self.sessions[session_id] = []
            self.save()
        return session_id

    def get(self, session_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.sessions.setdefault(session_id, []))

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        message.setdefault("ts", time.time())
        with self.lock:
            self.sessions.setdefault(session_id, []).append(message)
            self.save()

    def list(self) -> list[dict[str, Any]]:
        return [{"id": sid, "messages": msgs} for sid, msgs in self.sessions.items()]



class AgentLoop:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, max_steps: int = 256, progress: Callable[[str, str], None] | None = None) -> None:
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps
        self.progress = progress or (lambda stage, message: None)

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            return f"{value:,.10f}".rstrip("0").rstrip(".")
        return str(value)

    def _run_tool_calls(self, working: list[dict[str, Any]], tool_calls: list[dict[str, Any]], used_sources: list[dict[str, Any]], assistant_content: str = "") -> None:
        working.append({"role": "assistant", "content": assistant_content, "tool_calls": tool_calls})
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name")
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                self.progress("tool", f"Using {name}.")
                result = self.tools.execute(name, arguments)
                if name in {"search_knowledge", "get_document"}:
                    source_records = (result.get("results") or result.get("records") or []) if isinstance(result, dict) else result
                    if isinstance(source_records, list):
                        known = {item.get("record_id") for item in used_sources}
                        for item in source_records:
                            if isinstance(item, dict) and item.get("record_id") and item["record_id"] not in known:
                                used_sources.append({k: item.get(k) for k in ("record_id", "source", "path", "page", "sheet", "row", "slide", "section")})
                                known.add(item["record_id"])
                if name == "calculate" and isinstance(result, dict) and "result" in result:
                    # Give the model a natural-language calculation result instead of raw JSON.
                    result_text = (
                        f"The calculation for {result.get('expression', '')} produced {self._format_value(result['result'])}. "
                        "Use that result in your final answer and do not expose this internal tool message."
                    )
                else:
                    result_text = json.dumps(result, ensure_ascii=False)
            except Exception as exc:
                result_text = json.dumps({"error": str(exc)}, ensure_ascii=False)
            working.append({"role": "tool", "tool_call_id": call.get("id", name), "content": result_text})

    def run(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        working = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
        used_sources: list[dict[str, Any]] = []

        for step in range(self.max_steps):
            self.progress("thinking", "Understanding your request...")
            response = self.llm.chat(working, self.tools.schemas_for_model())
            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                content = message.get("content") or ""
                return {"answer": content, "sources": used_sources, "steps": step + 1}

            self._run_tool_calls(working, tool_calls, used_sources, message.get("content") or "")

        return {
            "answer": "I could not complete the required information-gathering steps within the available reasoning cycle.",
            "sources": used_sources,
            "steps": self.max_steps,
        }

    def run_stream(self, messages: list[dict[str, Any]]):
        """Streaming counterpart to run().

        Yields {"type": "delta", "text": ...} chunks live as the model
        produces the *final* answer (the step with no tool_calls). Steps
        where the model calls a tool are still executed synchronously and
        are not streamed -- there's nothing user-facing to show until the
        tool result comes back and the model reacts to it, so those steps
        behave exactly like run()'s tool-calling loop, and progress is
        still reported through self.progress() for the existing "thinking"
        indicator. The loop ends with a single {"type": "final", ...} event
        carrying the same shape run() returns.
        """
        working = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
        used_sources: list[dict[str, Any]] = []

        for step in range(self.max_steps):
            self.progress("thinking", "Understanding your request...")
            content_parts: list[str] = []
            tool_calls_acc: dict[int, dict[str, Any]] = {}
            is_tool_step = False

            for chunk in self.llm.chat_stream(working, self.tools.schemas_for_model()):
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                delta_tool_calls = delta.get("tool_calls")
                delta_content = delta.get("content")

                if delta_tool_calls:
                    is_tool_step = True
                    for tc in delta_tool_calls:
                        idx = tc.get("index", 0)
                        slot = tool_calls_acc.setdefault(idx, {"id": tc.get("id") or f"call_{idx}", "type": "function", "function": {"name": "", "arguments": ""}})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]

                if delta_content:
                    content_parts.append(delta_content)
                    if not is_tool_step:
                        yield {"type": "delta", "text": delta_content}

            if is_tool_step and tool_calls_acc:
                tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
                self._run_tool_calls(working, tool_calls, used_sources, "".join(content_parts))
                continue

            content = "".join(content_parts)
            yield {"type": "final", "answer": content, "sources": used_sources, "steps": step + 1}
            return

        yield {
            "type": "final",
            "answer": "I could not complete the required information-gathering steps within the available reasoning cycle.",
            "sources": used_sources,
            "steps": self.max_steps,
        }



class KoraEngine:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.store = KnowledgeStore()
        self.conversations = ConversationManager(CONVERSATIONS_PATH)
        self.search = SearchEngine(self.store, self.llm)
        self.ingestion = IngestionManager(self.store, self.llm)
        self.tools = ToolRegistry()
        register_builtins(self.tools, self.store, self.search, self.conversations)
        self.lock = threading.RLock()
        self.last_failed = 0
        self.last_sync_report: dict[str, Any] = {
            "summary": {"files": 0, "indexed": 0, "empty": 0, "unchanged": 0, "failed": 0},
            "files": [],
        }
        self.progress = {"active": False, "stage": "idle", "message": "Ready.", "failed": 0}

    def set_progress(self, stage: str, message: str, percent: int | None = None) -> None:
        state: dict[str, Any] = {"active": True, "stage": stage, "message": message}
        if percent is not None:
            state["percent"] = max(0, min(100, int(percent)))
        self.progress = state

    def finish_progress(self) -> None:
        """Back to idle. The UI treats stage "complete" as "index is ready", so
        report that whenever documents are indexed (stage "idle" otherwise)."""
        docs = len(self.store.document_list())
        self.progress = {
            "active": False,
            "stage": "complete" if docs else "idle",
            "message": f"{docs} document{'s' if docs != 1 else ''} indexed." if docs else "Ready.",
            "failed": self.last_failed,
            "percent": 100 if docs else 0,
        }

    def sync(self) -> dict[str, Any]:
        with self.lock:
            self.set_progress("indexing", "Indexing workspace.", 0)

            def on_file(state: dict[str, Any]) -> None:
                total = max(1, int(state["total"]))
                self.set_progress("indexing", f"{state['current']}/{state['total']} — {state['status']}: {state.get('name') or state.get('file') or state.get('path', '')}",
                                  round(int(state["current"]) / total * 100))

            try:
                result = self.ingestion.sync(on_file)
                self.last_failed = int(result.get("failed", 0))
                self.last_sync_report = {
                    "summary": {key: int(result.get(key, 0)) for key in ("files", "indexed", "empty", "unchanged", "failed")},
                    "files": result.get("items", []),
                }
                return result
            finally:
                self.finish_progress()

    def _history(self, session_id: str) -> list[dict[str, Any]]:
        """Stored conversation as plain chat messages for the model."""
        return [{"role": m["role"], "content": m["content"]}
                for m in self.conversations.get(session_id)
                if m.get("role") in ("user", "assistant") and m.get("content")]

    def chat(self, session_id: str, user_text: str) -> dict[str, Any]:
        with self.lock:
            if not session_id:
                session_id = self.conversations.create()
            self.conversations.append(session_id, {"role": "user", "content": user_text})
            history = self._history(session_id)
            agent = AgentLoop(self.llm, self.tools, MAX_AGENT_STEPS, self.set_progress)
            try:
                result = agent.run(history)
                self.conversations.append(session_id, {"role": "assistant", "content": result["answer"], "sources": result.get("sources", [])})
                return {"session_id": session_id, **result}
            finally:
                self.finish_progress()

    def chat_stream(self, session_id: str, user_text: str):
        """Streaming counterpart to chat(). Yields the same {"type": "delta", ...}
        events AgentLoop.run_stream() produces, then a final {"type": "final",
        "session_id": ..., "answer": ..., "sources": ..., "steps": ...} event
        once the assistant's reply has been generated and saved to history --
        same persistence contract as chat(), just observable as it happens.
        """
        with self.lock:
            if not session_id:
                session_id = self.conversations.create()
            self.conversations.append(session_id, {"role": "user", "content": user_text})
            history = self._history(session_id)
            agent = AgentLoop(self.llm, self.tools, MAX_AGENT_STEPS, self.set_progress)
            try:
                answer, sources, steps = "", [], 0
                for event in agent.run_stream(history):
                    if event["type"] == "delta":
                        yield event
                    else:
                        answer = event["answer"]
                        sources = event.get("sources", [])
                        steps = event.get("steps", 0)
                self.conversations.append(session_id, {"role": "assistant", "content": answer, "sources": sources})
                yield {"type": "final", "session_id": session_id, "answer": answer, "sources": sources, "steps": steps}
            finally:
                self.finish_progress()
