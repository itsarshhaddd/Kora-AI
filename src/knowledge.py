"""Indexed document storage, source metadata, retrieval, and ingestion."""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from pathlib import Path
from typing import Any, Callable

from config import EMBED_MODEL, INDEX_PATH, TOP_K, WORKSPACE_DIR
from llm import LLMClient
from extractor import SUPPORTED_EXTENSIONS, extract_file, normalize_records



class KnowledgeStore:
    def __init__(self, path: Path = INDEX_PATH) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.data: dict[str, Any] = {"version": 1, "documents": {}, "records": []}
        self.load()

    def load(self) -> None:
        with self.lock:
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("version", 1)
            self.data.setdefault("documents", {})
            self.data.setdefault("records", [])
            self._invalidate_if_embed_model_changed()

    def _invalidate_if_embed_model_changed(self) -> None:
        """Vectors from different embedding models are not comparable (different
        dimensions/spaces). If the model changed, drop old vectors and clear file
        hashes so the next sync re-embeds every document."""
        previous = self.data.get("embed_model")
        if previous == EMBED_MODEL:
            return
        if self.data["records"] or self.data["documents"]:
            for record in self.data["records"]:
                record.pop("embedding", None)
            for metadata in self.data["documents"].values():
                metadata["file_hash"] = None
            print(f"[KORA] Embedding model changed ({previous or 'unknown'} -> {EMBED_MODEL}); documents will be re-indexed.")
        self.data["embed_model"] = EMBED_MODEL
        self.save()

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)

    @staticmethod
    def file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def all_records(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.data.get("records", []))

    def records_for_document(self, document_id: str) -> list[dict[str, Any]]:
        return [r for r in self.all_records() if r.get("document_id") == document_id]

    def replace_document(self, document_id: str, metadata: dict[str, Any], records: list[dict[str, Any]]) -> None:
        with self.lock:
            self.data["documents"][document_id] = metadata
            self.data["records"] = [r for r in self.data["records"] if r.get("document_id") != document_id]
            self.data["records"].extend(records)
            self.save()

    def remove_document(self, document_id: str) -> None:
        with self.lock:
            self.data["documents"].pop(document_id, None)
            self.data["records"] = [r for r in self.data["records"] if r.get("document_id") != document_id]
            self.save()

    def document_for_path(self, path: Path) -> tuple[str, dict[str, Any]] | None:
        resolved = str(path.resolve())
        for document_id, metadata in self.data.get("documents", {}).items():
            if metadata.get("path") == resolved:
                return document_id, metadata
        return None

    def document_list(self) -> list[dict[str, Any]]:
        return list(self.data.get("documents", {}).values())



def source_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "source": record.get("source"),
        "path": record.get("path"),
        "page": record.get("page"),
        "sheet": record.get("sheet"),
        "row": record.get("row"),
        "slide": record.get("slide"),
        "section": record.get("section"),
    }



def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w]+", text.lower()))

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0

class SearchEngine:
    def __init__(self, store: KnowledgeStore, llm: LLMClient) -> None:
        self.store = store
        self.llm = llm

    def search(self, query: str, top_k: int | None = TOP_K) -> list[dict[str, Any]]:
        records = self.store.all_records()
        if not records:
            return []
        query_embedding = self.llm.embed([query], kind="query")[0]
        qtokens = _tokens(query)
        scored = []
        for record in records:
            content = record.get("content", "")
            tokens = _tokens(content)
            lexical = len(qtokens & tokens) / max(len(qtokens), 1)
            semantic = _cosine(query_embedding, record.get("embedding", []))
            score = 0.75 * semantic + 0.25 * lexical
            scored.append((score, record))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results = []
        for score, record in (scored if top_k is None else scored[:max(0, int(top_k))]):
            results.append({
                "record_id": record.get("record_id"),
                "content": record.get("content", ""),
                "source": record.get("source"),
                "path": record.get("path"),
                "page": record.get("page"),
                "sheet": record.get("sheet"),
                "row": record.get("row"),
                "slide": record.get("slide"),
                "section": record.get("section"),
                "score": round(float(score), 5),
            })
        return results

    def search_page(self, query: str, limit: int, offset: int = 0) -> dict[str, Any]:
        """Return one page of ranked search results with explicit continuation metadata.

        ``limit`` is a page size, not a maximum result count. Callers can fetch
        every result by following ``next_offset`` until ``has_more`` is false.
        """
        try:
            page_size = int(limit)
            start = int(offset)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit and offset must be integers") from exc
        if page_size < 1:
            raise ValueError("limit must be at least 1")
        if start < 0:
            raise ValueError("offset cannot be negative")

        ranked = self.search(query, top_k=None)
        total = len(ranked)
        results = ranked[start:start + page_size]
        next_offset = start + len(results) if start + len(results) < total else None
        return {
            "results": results,
            "total": total,
            "offset": start,
            "limit": page_size,
            "has_more": next_offset is not None,
            "next_offset": next_offset,
        }



class IngestionManager:
    def __init__(self, store: KnowledgeStore, llm: LLMClient) -> None:
        self.store = store
        self.llm = llm

    @staticmethod
    def document_id(path: Path) -> str:
        return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:32]

    def scan(self) -> list[Path]:
        return sorted(
            [p for p in WORKSPACE_DIR.rglob("*")
             if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS],
            key=lambda p: str(p).lower(),
        )

    def sync(self, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        files = self.scan()
        existing = {str(Path(m.get("path", "")).resolve()): m for m in self.store.document_list()}
        seen: set[str] = set()
        indexed = empty = unchanged = failed = 0
        file_results: list[dict[str, Any]] = []

        for number, path in enumerate(files, 1):
            resolved = str(path.resolve())
            seen.add(resolved)
            document_id = self.document_id(path)
            digest = self.store.file_hash(path)
            previous = existing.get(resolved)

            if previous and previous.get("file_hash") == digest:
                unchanged += 1
                file_state = {
                    "name": path.name,
                    "path": resolved,
                    "status": "unchanged",
                    "records": int(previous.get("record_count", 0)),
                }
                file_results.append(file_state)
                if progress:
                    progress({"current": number, "total": len(files), "file": str(path), **file_state})
                continue

            try:
                raw_records = normalize_records(extract_file(path))
                records = []
                for index, record in enumerate(raw_records):
                    item = dict(record)
                    item["document_id"] = document_id
                    item["record_id"] = f"{document_id}:{index}:{record.get('record_id', index)}"
                    records.append(item)

                embeddings = self.llm.embed([record["content"] for record in records]) if records else []
                for record, embedding in zip(records, embeddings):
                    record["embedding"] = embedding

                metadata = {
                    "document_id": document_id,
                    "path": resolved,
                    "source": path.name,
                    "file_type": path.suffix.lower(),
                    "file_hash": digest,
                    "record_count": len(records),
                }
                self.store.replace_document(document_id, metadata, records)
                status = "indexed" if records else "empty"
                indexed += 1
                empty += int(not records)
                file_state = {"name": path.name, "path": resolved, "status": status, "records": len(records)}
            except Exception as exc:
                failed += 1
                file_state = {
                    "name": path.name,
                    "path": resolved,
                    "status": "failed",
                    "records": 0,
                    "error": str(exc),
                }

            file_results.append(file_state)
            if progress:
                progress({"current": number, "total": len(files), "file": str(path), **file_state})

        for resolved, metadata in list(existing.items()):
            if resolved not in seen:
                self.store.remove_document(metadata["document_id"])

        return {
            "files": len(files),
            "indexed": indexed,
            "empty": empty,
            "unchanged": unchanged,
            "failed": failed,
            "items": file_results,
        }
