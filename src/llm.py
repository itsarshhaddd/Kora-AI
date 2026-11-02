"""Local LLM HTTP client and KORA's system prompt."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from config import CHAT_URL, EMBED_BATCH_SIZE, EMBED_DOC_PREFIX, EMBED_QUERY_PREFIX, EMBED_URL



class LLMClientError(RuntimeError):
    pass

class LLMClient:
    def __init__(self, timeout: int = 300) -> None:
        self.timeout = timeout

    def _post(self, base: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{base}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LLMClientError(f"Local LLM service unavailable: {exc}") from exc
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LLMClientError(f"Invalid LLM response: {exc}") from exc

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True
        return self._post(CHAT_URL, "/v1/chat/completions", payload)

    def chat_stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None):
        """Stream chat completion chunks from llama-server's OpenAI-compatible SSE endpoint.

        Yields each parsed `data: {...}` chunk as a dict (the raw OpenAI-shaped
        delta payload) so the caller can inspect both `delta.content` and
        `delta.tool_calls` fragments itself. A `data: [DONE]` sentinel line
        ends the stream and is swallowed here rather than yielded.
        """
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True
        request = urllib.request.Request(
            f"{CHAT_URL}/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue
        except urllib.error.URLError as exc:
            raise LLMClientError(f"Local LLM service unavailable: {exc}") from exc

    def embed(self, texts: list[str], kind: str = "document") -> list[list[float]]:
        """Embed texts. kind is "document" (indexing) or "query" (searching);
        the configured Nomic task prefix is prepended automatically."""
        if not texts:
            return []
        prefix = EMBED_QUERY_PREFIX if kind == "query" else EMBED_DOC_PREFIX
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = [prefix + t for t in texts[start:start + EMBED_BATCH_SIZE]]
            result = self._post(EMBED_URL, "/v1/embeddings", {"input": batch})
            data = result.get("data") or []
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            vectors.extend(item["embedding"] for item in ordered)
        return vectors



SYSTEM_PROMPT = """You are KORA (spelled K-O-R-A), a general-purpose local assistant.
Your name is KORA (K-O-R-A). Its pronunciation starts with a hard K sound. This is identity information, not a fixed response script. When users ask who you are, what you are, why you exist, or what you can do, reason about their actual question and compose a natural, relevant answer in your own words using this identity and your real capabilities. Do not use a canned introduction, claim an alternate name, or say "QORA".

Answer normal user questions using your own knowledge when appropriate. When the user's KORA workspace is relevant, use the available Python tools to inspect it. You may use multiple tools and multiple retrieval steps before answering.
When a search_knowledge or get_document result includes has_more=true, use its next_offset to retrieve additional pages whenever the user's request requires the complete set (for example, "all", "every", "list the full set", or a complete count/listing). Do not treat one page as the complete result. Continue until has_more=false or until the user only asked for a relevant sample. The page size is not a total-result limit.

Never invent facts from workspace documents. If workspace sources conflict, explain the conflict. If requested workspace information cannot be found, say so clearly.

Important output rules:
- Always answer the user in normal, readable language.
- Never expose tool-call syntax, raw JSON, Python, internal field names, escaped control sequences, or debugging text in the final answer.
- Never repeat the internal expression format used by the calculate tool. Turn calculation results into a simple human-readable answer.
- Do not output strings such as `time\\`, `calculate\\`, `result\\`, `<tool_call>`, or other internal separators.
- For a calculation, give the result directly and briefly explain the arithmetic when useful.
- If a tool returns structured data, use it as information; do not dump the structure to the user.

When giving an answer based on workspace information, use the source metadata returned by tools when useful. Do not fabricate sources.
"""
