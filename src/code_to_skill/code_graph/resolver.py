"""引用解析。

基于 import 语句、调用模式和框架约定解析引用关系。
生成 calls / imports / extends / implements / references 等边。
"""
from __future__ import annotations

import os
import re
from typing import Iterable

from .types import CodeGraph, GraphEdge, EdgeKind, GraphNode, NodeKind, UnresolvedEdge


def resolve_references(graph: CodeGraph, repo_root: str) -> list[UnresolvedEdge]:
    """在 CodeGraph 上解析引用关系，返回未解析边列表。

    解析策略：
    1. import 边：匹配 import 路径中的符号名到已知节点
    2. calls 边：在函数体内匹配已知函数/方法名
    3. extends/implements 边：匹配类继承关系
    """
    unresolved: list[UnresolvedEdge] = []

    # 构建符号索引：name / qualified_name → [node_id]
    name_index: dict[str, list[str]] = {}
    qname_index: dict[str, list[str]] = {}
    for node in graph.nodes:
        name_index.setdefault(node.name, []).append(node.id)
        if node.qualified_name:
            qname_index.setdefault(node.qualified_name, []).append(node.id)
            # 短名后缀：com.example.Foo.bar → bar / Foo.bar
            parts = node.qualified_name.split(".")
            if len(parts) >= 2:
                qname_index.setdefault(".".join(parts[-2:]), []).append(node.id)

    # 构建文件索引：file_path → [node_id]
    file_index: dict[str, list[str]] = {}
    for node in graph.nodes:
        file_index.setdefault(node.file_path, []).append(node.id)

    seen_edges: set[tuple[str, str, str]] = set()

    for node in graph.nodes:
        if node.kind not in (NodeKind.class_, NodeKind.interface, NodeKind.method, NodeKind.function):
            continue

        full_path = os.path.join(repo_root, node.file_path)
        if not os.path.exists(full_path):
            continue

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        if node.kind in (NodeKind.class_, NodeKind.interface):
            _resolve_inheritance(graph, node, content, name_index, qname_index, seen_edges)
            imports = _extract_imports(content, node.language)
            import_map = _build_java_import_map(content) if node.language == "java" else {}
            for imp_name in imports:
                fqn = import_map.get(imp_name, imp_name)
                targets = (
                    qname_index.get(fqn)
                    or qname_index.get(imp_name)
                    or name_index.get(imp_name, [])
                )
                if targets:
                    for target in targets:
                        edge_key = (node.id, target, "imports")
                        if edge_key not in seen_edges:
                            seen_edges.add(edge_key)
                            graph.edges.append(GraphEdge(
                                source=node.id or node.file_path,
                                target=target,
                                kind=EdgeKind.imports,
                                provenance="heuristic",
                                confidence=0.7,
                            ))
                else:
                    unresolved.append(UnresolvedEdge(
                        source=node.id or node.file_path,
                        attempted_target=imp_name,
                        kind="imports",
                        reason=f"Symbol '{imp_name}' not found in graph",
                    ))

        if node.kind in (NodeKind.method, NodeKind.function):
            body = _extract_body(content, node.start_line, node.end_line)
            for callee_name, targets in _find_call_targets(body, name_index, node):
                for target in targets:
                    edge_key = (node.id, target, "calls")
                    if edge_key not in seen_edges and target != node.id:
                        seen_edges.add(edge_key)
                        graph.edges.append(GraphEdge(
                            source=node.id,
                            target=target,
                            kind=EdgeKind.calls,
                            provenance="heuristic",
                            confidence=0.55,
                        ))

    unresolved = resolve_unresolved_second_pass(graph, unresolved, name_index, qname_index, seen_edges)
    return unresolved


def resolve_unresolved_second_pass(
    graph: CodeGraph,
    unresolved: list[UnresolvedEdge],
    name_index: dict[str, list[str]],
    qname_index: dict[str, list[str]],
    seen_edges: set[tuple[str, str, str]],
) -> list[UnresolvedEdge]:
    """二阶段：用完整图谱索引重试 unresolved imports。"""
    still: list[UnresolvedEdge] = []
    for item in unresolved:
        if item.kind != "imports":
            still.append(item)
            continue
        name = item.attempted_target
        targets = qname_index.get(name) or name_index.get(name, [])
        if not targets:
            # 后缀匹配：com.foo.Bar → Bar
            short = name.split(".")[-1]
            targets = qname_index.get(short) or name_index.get(short, [])
        if targets:
            for target in targets[:3]:
                key = (item.source, target, "imports")
                if key not in seen_edges:
                    seen_edges.add(key)
                    graph.edges.append(GraphEdge(
                        source=item.source,
                        target=target,
                        kind=EdgeKind.imports,
                        provenance="heuristic",
                        confidence=0.55,
                    ))
        else:
            still.append(item)
    return still


def _extract_body(content: str, start_line: int, end_line: int) -> str:
    lines = content.splitlines()
    if not lines:
        return ""
    s = max(0, start_line - 1)
    e = end_line if end_line > start_line else min(len(lines), s + 200)
    return "\n".join(lines[s:e])


def _find_call_targets(
    body: str,
    name_index: dict[str, list[str]],
    source_node: GraphNode,
) -> list[tuple[str, list[str]]]:
    """从方法体匹配 foo( 形式的调用。"""
    found: list[tuple[str, list[str]]] = []
    for m in re.finditer(r"\b([a-zA-Z_]\w*)\s*\(", body):
        name = m.group(1)
        if name in ("if", "for", "while", "switch", "catch", "new", "return", "super", "this"):
            continue
        candidates = name_index.get(name, [])
        if not candidates:
            continue
        same_file = [c for c in candidates if c.startswith(source_node.file_path)]
        targets = same_file or candidates[:3]
        found.append((name, targets))
    return found


def _extract_imports(content: str, language: str) -> list[str]:
    """从源码提取 import 的类名。"""
    imports: list[str] = []

    if language == "java":
        # import com.foo.Bar; → Bar
        for m in re.finditer(r"import\s+(?:static\s+)?([\w.]+)\.(\w+)\s*;", content):
            imports.append(m.group(2))
        # fully qualified references: new com.foo.Bar() → Bar
        for m in re.finditer(r"(?:new|extends|implements)\s+([\w.]+)\.(\w+)", content):
            imports.append(m.group(2))

    elif language == "python":
        # from foo.bar import Baz → Baz
        for m in re.finditer(r"from\s+[\w.]+\s+import\s+(\w+)", content):
            imports.append(m.group(1))
        # import foo.bar → foo
        for m in re.finditer(r"^import\s+([\w.]+)", content, re.MULTILINE):
            imports.append(m.group(1).split(".")[0])

    elif language in ("javascript", "typescript"):
        # import { Foo } from './bar' → Foo
        for m in re.finditer(r"import\s+\{?\s*(\w+)", content):
            imports.append(m.group(1))
        # const Foo = require('./bar') → Foo
        for m in re.finditer(r"const\s+(\w+)\s*=\s*require", content):
            imports.append(m.group(1))

    elif language == "go":
        for m in re.finditer(r"\"([^\"]+)\"", content):
            imports.append(m.group(1).split("/")[-1])

    return list(set(imports))


def _build_java_import_map(content: str) -> dict[str, str]:
    """短名 → 全限定名。"""
    mapping: dict[str, str] = {}
    for m in re.finditer(r"import\s+(?:static\s+)?([\w.]+)\.(\w+)\s*;", content):
        mapping[m.group(2)] = f"{m.group(1)}.{m.group(2)}"
    return mapping


def _resolve_inheritance(
    graph: CodeGraph,
    node: GraphNode,
    content: str,
    name_index: dict[str, list[str]],
    qname_index: dict[str, list[str]],
    seen_edges: set[tuple[str, str, str]],
):
    """解析 Java extends / implements 边。"""
    if node.language != "java":
        return
    import_map = _build_java_import_map(content)
    decl = re.search(
        rf"(?:public\s+)?(?:class|interface)\s+{re.escape(node.name)}\b"
        rf"(?:\s+extends\s+([\w.]+))?(?:\s+implements\s+([\w,\s]+))?",
        content,
    )
    if not decl:
        return

    parent = decl.group(1)
    if parent:
        short = parent.split(".")[-1]
        fqn = import_map.get(short, parent)
        for target in (qname_index.get(fqn) or name_index.get(short, []))[:2]:
            key = (node.id, target, "extends")
            if key not in seen_edges:
                seen_edges.add(key)
                graph.edges.append(GraphEdge(
                    source=node.id, target=target,
                    kind=EdgeKind.extends, provenance="heuristic", confidence=0.8,
                ))

    ifaces = decl.group(2)
    if ifaces:
        for iface in re.findall(r"[\w.]+", ifaces):
            short = iface.split(".")[-1]
            fqn = import_map.get(short, iface)
            for target in (qname_index.get(fqn) or name_index.get(short, []))[:2]:
                key = (node.id, target, "implements")
                if key not in seen_edges:
                    seen_edges.add(key)
                    graph.edges.append(GraphEdge(
                        source=node.id, target=target,
                        kind=EdgeKind.implements, provenance="heuristic", confidence=0.75,
                    ))
