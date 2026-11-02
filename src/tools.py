"""Tool registry and the built-in Python tools exposed to KORA's agent."""
from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable

from config import TOP_K
from knowledge import KnowledgeStore, SearchEngine



class ToolRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.schemas: list[dict[str, Any]] = []

    def register(self, name: str, description: str, parameters: dict[str, Any], handler: Callable[[dict[str, Any]], Any]) -> None:
        self.handlers[name] = handler
        self.schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self.handlers:
            raise ValueError(f"Unknown tool: {name}")
        return self.handlers[name](arguments)

    def schemas_for_model(self) -> list[dict[str, Any]]:
        return self.schemas



_ALLOWED = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

def safe_calculate(expression: str) -> float | int:
    expression = str(expression or "").strip()
    if not expression:
        raise ValueError("Expression is empty")
    if len(expression) > 1024:
        raise ValueError("Expression is too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, MemoryError) as exc:
        raise ValueError(f"Invalid arithmetic expression: {exc}") from exc
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("Expression is too complex")

    def visit(node: ast.AST, depth: int = 0):
        if depth > 20:
            raise ValueError("Expression nesting is too deep")
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            value = node.value
            if isinstance(value, int) and value.bit_length() > 4096:
                raise ValueError("Numeric literal is too large")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Numeric literal must be finite")
            return value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED:
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                if not isinstance(right, (int, float)):
                    raise ValueError("Exponent is outside the allowed range")
                if isinstance(right, int):
                    if abs(right) > 1000:
                        raise ValueError("Exponent is outside the allowed range")
                elif not math.isfinite(right) or abs(right) > 1000:
                    raise ValueError("Exponent is outside the allowed range")
            if isinstance(node.op, ast.Pow) and isinstance(left, int) and left.bit_length() > 256 and abs(right) > 16:
                raise ValueError("Exponentiation would produce an oversized result")
            try:
                result = _ALLOWED[type(node.op)](left, right)
            except (ArithmeticError, OverflowError, ValueError) as exc:
                raise ValueError(f"Invalid arithmetic operation: {exc}") from exc
            if isinstance(result, complex) or not isinstance(result, (int, float)):
                raise ValueError("Only real numeric results are allowed")
            if isinstance(result, int) and result.bit_length() > 4096:
                raise ValueError("Calculated integer is too large")
            if isinstance(result, float) and not math.isfinite(result):
                raise ValueError("Calculated result is not finite")
            return result
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED:
            result = _ALLOWED[type(node.op)](visit(node.operand, depth + 1))
            if isinstance(result, int) and result.bit_length() > 4096:
                raise ValueError("Calculated integer is too large")
            if isinstance(result, float) and not math.isfinite(result):
                raise ValueError("Calculated result is not finite")
            return result
        raise ValueError("Only numeric arithmetic is allowed")

    result = visit(tree.body)
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result

def format_number(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.10f}".rstrip("0").rstrip(".")
    return str(value)

def _document_page(store: KnowledgeStore, args: dict[str, Any]) -> dict[str, Any]:
    try:
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", TOP_K))
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and offset must be integers") from exc
    if offset < 0:
        raise ValueError("offset cannot be negative")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    records = store.records_for_document(args["document_id"])
    page = records[offset:offset + limit]
    next_offset = offset + len(page) if offset + len(page) < len(records) else None
    # Embeddings are an internal retrieval detail and should never be sent to the model.
    clean = [{k: v for k, v in record.items() if k != "embedding"} for record in page]
    return {
        "records": clean,
        "total": len(records),
        "offset": offset,
        "limit": limit,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
    }

def register_builtins(registry, store: KnowledgeStore, search: SearchEngine, conversations: Any) -> None:
    registry.register(
        "search_knowledge",
        "Search the user's KORA workspace for relevant information. Use this whenever workspace data may be needed.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "description": "Page size. Use offset to fetch subsequent pages; there is no maximum result count."},
                "offset": {"type": "integer", "minimum": 0},
                "top_k": {"type": "integer", "minimum": 1, "description": "Backward-compatible alias for limit."},
            },
            "required": ["query"],
        },
        lambda args: search.search_page(
            args["query"],
            int(args.get("limit", args.get("top_k", TOP_K))),
            int(args.get("offset", 0)),
        ),
    )

    registry.register(
        "get_document",
        "Get indexed records belonging to one document. Results are paginated; follow next_offset while has_more is true to inspect the complete document.",
        {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "description": "Page size; there is no maximum result count."},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["document_id"],
        },
        lambda args: _document_page(store, args),
    )

    registry.register(
        "list_documents",
        "List documents currently indexed in the KORA workspace.",
        {"type": "object", "properties": {}},
        lambda args: store.document_list(),
    )

    registry.register(
        "calculate",
        "Perform a basic numeric calculation after the needed values have been established. The result is exact. Do not expose internal tool syntax in the final answer.",
        {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        lambda args: {"expression": str(args["expression"]), "result": safe_calculate(str(args["expression"]))},
    )

    registry.register(
        "get_conversation_context",
        "Retrieve earlier messages from the current conversation when the current question depends on previous turns.",
        {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]},
        lambda args: conversations.get(args["session_id"]),
    )
