"""tree-sitter Query 符号提取 + 全树遍历兜底。"""
from __future__ import annotations

import logging
from typing import Any

from .types import GraphNode, NodeKind

logger = logging.getLogger(__name__)

# ── Query 模板（按 grammar）──────────────────────────────────

_QUERIES: dict[str, str] = {
    "java": """
(class_declaration name: (identifier) @name) @def
(interface_declaration name: (identifier) @name) @def
(enum_declaration name: (identifier) @name) @def
(method_declaration name: (identifier) @name) @def
(constructor_declaration name: (identifier) @name) @def
""",
    "python": """
(class_definition name: (identifier) @name) @def
(function_definition name: (identifier) @name) @def
""",
    "javascript": """
(class_declaration name: (identifier) @name) @def
(function_declaration name: (identifier) @name) @def
(method_definition name: (property_identifier) @name) @def
(lexical_declaration (variable_declarator name: (identifier) @name) @def)
""",
    "typescript": """
(class_declaration name: (identifier) @name) @def
(interface_declaration name: (identifier) @name) @def
(function_declaration name: (identifier) @name) @def
(method_definition name: (property_identifier) @name) @def
""",
    "tsx": """
(class_declaration name: (identifier) @name) @def
(interface_declaration name: (identifier) @name) @def
(function_declaration name: (identifier) @name) @def
(method_definition name: (property_identifier) @name) @def
""",
    "go": """
(function_declaration name: (identifier) @name) @def
(method_declaration name: (field_identifier) @name) @def
(type_declaration (type_spec name: (type_identifier) @name)) @def
""",
    "rust": """
(function_item name: (identifier) @name) @def
(impl_item type: (type_identifier) @name) @def
(struct_item name: (type_identifier) @name) @def
""",
    "kotlin": """
(class_declaration (type_identifier) @name) @def
(function_declaration (simple_identifier) @name) @def
""",
    "cpp": """
(class_specifier name: (type_identifier) @name) @def
(function_definition declarator: (function_declarator declarator: (identifier) @name)) @def
""",
    "c": """
(function_definition declarator: (function_declarator declarator: (identifier) @name)) @def
(struct_specifier name: (type_identifier) @name) @def
""",
    "csharp": """
(class_declaration name: (identifier) @name) @def
(interface_declaration name: (identifier) @name) @def
(method_declaration name: (identifier) @name) @def
""",
    "ruby": """
(class name: (constant) @name) @def
(method name: (identifier) @name) @def
""",
    "php": """
(class_declaration name: (name) @name) @def
(function_definition name: (name) @name) @def
""",
    "swift": """
(class_declaration name: (type_identifier) @name) @def
(function_declaration name: (simple_identifier) @name) @def
""",
    "scala": """
(class_definition name: (identifier) @name) @def
(function_definition name: (identifier) @name) @def
""",
}

_WALK_KIND_MAP: dict[str, dict[str, NodeKind]] = {
    "java": {
        "class_declaration": NodeKind.class_,
        "interface_declaration": NodeKind.interface,
        "enum_declaration": NodeKind.class_,
        "method_declaration": NodeKind.method,
        "constructor_declaration": NodeKind.method,
    },
    "python": {
        "class_definition": NodeKind.class_,
        "function_definition": NodeKind.function,
    },
    "javascript": {
        "class_declaration": NodeKind.class_,
        "function_declaration": NodeKind.function,
        "method_definition": NodeKind.method,
        "arrow_function": NodeKind.function,
    },
    "typescript": {
        "class_declaration": NodeKind.class_,
        "interface_declaration": NodeKind.interface,
        "function_declaration": NodeKind.function,
        "method_definition": NodeKind.method,
    },
    "go": {
        "function_declaration": NodeKind.function,
        "method_declaration": NodeKind.method,
        "type_declaration": NodeKind.class_,
    },
    "rust": {
        "function_item": NodeKind.function,
        "struct_item": NodeKind.class_,
        "impl_item": NodeKind.class_,
    },
    "kotlin": {
        "class_declaration": NodeKind.class_,
        "function_declaration": NodeKind.function,
    },
    "csharp": {
        "class_declaration": NodeKind.class_,
        "interface_declaration": NodeKind.interface,
        "method_declaration": NodeKind.method,
    },
}


def _node_kind_from_capture(grammar_id: str, capture: str, node_type: str) -> NodeKind | None:
    if capture == "def" or capture == "name":
        return _WALK_KIND_MAP.get(grammar_id, {}).get(node_type)
    return _WALK_KIND_MAP.get(grammar_id, {}).get(node_type)


def _compile_ts_query(lang_obj: Any, query_src: str) -> Any | None:
    """兼容 tree-sitter 0.21（Query(lang, src)）与 0.22+（lang.query(src)）。"""
    if hasattr(lang_obj, "query"):
        try:
            return lang_obj.query(query_src)
        except Exception as exc:
            logger.debug("lang.query failed: %s", exc)
    try:
        from tree_sitter import Query
        return Query(lang_obj, query_src)
    except Exception as exc:
        logger.debug("Query() failed: %s", exc)
    return None


def _capture_values(captures: dict, key: str) -> list[Any]:
    """Query 捕获可能是 Node 或 list[Node]（0.21 vs 0.22+）。"""
    val = captures.get(key)
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def _iter_query_matches(query: Any, root_node: Any):
    """统一新旧 Query 匹配迭代。"""
    if hasattr(query, "matches") and type(query).__name__ != "QueryCursor":
        for pattern_index, captures in query.matches(root_node):
            if isinstance(captures, dict):
                yield pattern_index, captures
            else:
                yield pattern_index, {}
        return
    try:
        from tree_sitter import QueryCursor
        cursor = QueryCursor()
        for pattern_index, captures in cursor.matches(query, root_node):
            yield pattern_index, captures
    except Exception as exc:
        logger.debug("QueryCursor.matches failed: %s", exc)


def extract_with_queries(
    source: bytes,
    root_node: Any,
    lang_obj: Any,
    grammar_id: str,
    *,
    rel_path: str,
    file_hash: str,
    language: str,
    scope: str,
) -> list[GraphNode]:
    """用 tree-sitter Query 提取符号。"""
    query_src = _QUERIES.get(grammar_id, "")
    if not query_src.strip():
        return []

    query = _compile_ts_query(lang_obj, query_src)
    if query is None:
        return []

    nodes: list[GraphNode] = []
    seen: set[tuple[str, int]] = set()
    class_stack: list[str] = []

    for _pattern_index, captures in _iter_query_matches(query, root_node):
        name_nodes = _capture_values(captures, "name")
        def_nodes = _capture_values(captures, "def")
        if not name_nodes:
            continue
        name_node = name_nodes[0]
        def_node = def_nodes[0] if def_nodes else name_node.parent or name_node
        name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
        if not name or name in ("<init>", "main") and grammar_id != "java":
            continue

        node_type = def_node.type
        kind = _node_kind_from_capture(grammar_id, "def", node_type)
        if kind is None:
            continue

        start_line = def_node.start_point[0] + 1
        end_line = def_node.end_point[0] + 1
        key = (name, start_line)
        if key in seen:
            continue
        seen.add(key)

        is_type = kind in (NodeKind.class_, NodeKind.interface)
        outer = class_stack.copy()
        qname = _build_qualified_name(language, scope, outer, name, kind)
        sig = source[def_node.start_byte:def_node.end_byte].decode("utf-8", errors="replace")
        sig = sig.split("\n", 1)[0][:240]

        nodes.append(GraphNode(
            id=f"{rel_path}::{name}",
            kind=kind,
            name=name,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            language=language,
            source_hash=file_hash,
            qualified_name=qname,
            signature=sig,
            metadata={"extractor": "tree-sitter-query", "grammar": grammar_id},
        ))
        if is_type:
            class_stack.append(name)

    return nodes


def extract_with_walk(
    source: bytes,
    root_node: Any,
    grammar_id: str,
    *,
    rel_path: str,
    file_hash: str,
    language: str,
    scope: str,
) -> list[GraphNode]:
    """全树遍历提取（无深度限制）。"""
    kind_map = _WALK_KIND_MAP.get(grammar_id, {})
    if not kind_map:
        return []

    nodes: list[GraphNode] = []
    class_stack: list[str] = []
    seen: set[tuple[str, int]] = set()

    def walk(node: Any) -> None:
        kind = kind_map.get(node.type)
        pushed = False
        if kind is not None:
            name = _extract_declaration_name(source, node) or _extract_name(source, node)
            if name:
                start_line = node.start_point[0] + 1
                key = (name, start_line)
                if key not in seen:
                    seen.add(key)
                    is_type = kind in (NodeKind.class_, NodeKind.interface)
                    qname = _build_qualified_name(language, scope, class_stack.copy(), name, kind)
                    sig = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                    sig = sig.split("\n", 1)[0][:240]
                    nodes.append(GraphNode(
                        id=f"{rel_path}::{name}",
                        kind=kind,
                        name=name,
                        file_path=rel_path,
                        start_line=start_line,
                        end_line=node.end_point[0] + 1,
                        language=language,
                        source_hash=file_hash,
                        qualified_name=qname,
                        signature=sig,
                        metadata={"extractor": "tree-sitter-walk", "grammar": grammar_id},
                    ))
                    if is_type:
                        class_stack.append(name)
                        pushed = True
        for child in node.children:
            walk(child)
        if pushed:
            class_stack.pop()

    walk(root_node)
    return nodes


def _extract_declaration_name(source: bytes, node: Any) -> str | None:
    """从 class/interface/enum 声明取类型名，跳过 modifiers 里的注解 identifier。"""
    if node.type not in ("class_declaration", "interface_declaration", "enum_declaration"):
        return None
    for child in node.children:
        if child.type in ("identifier", "type_identifier"):
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return None


def _extract_name(source: bytes, node: Any) -> str | None:
    decl = _extract_declaration_name(source, node)
    if decl:
        return decl
    for child in node.children:
        if child.type in (
            "identifier", "name", "property_identifier", "type_identifier",
            "field_identifier", "simple_identifier", "constant",
        ):
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        found = _extract_name(source, child)
        if found:
            return found
    return None


def _build_qualified_name(
    language: str,
    scope: str,
    outer_names: list[str],
    name: str,
    kind: NodeKind,
) -> str:
    parts: list[str] = []
    if scope:
        parts.append(scope)
    if kind in (NodeKind.class_, NodeKind.interface, NodeKind.function) and language != "java":
        parts.append(name)
        return ".".join(parts) if parts else name
    if outer_names:
        parts.extend(outer_names)
    if kind in (NodeKind.method, NodeKind.function) or (
        language == "java" and kind not in (NodeKind.class_, NodeKind.interface)
    ):
        parts.append(name)
    elif kind in (NodeKind.class_, NodeKind.interface):
        parts.append(name)
    return ".".join(parts) if parts else name
