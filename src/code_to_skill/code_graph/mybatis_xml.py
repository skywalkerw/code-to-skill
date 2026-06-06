"""MyBatis Mapper XML 提取器。"""
from __future__ import annotations

import os
import re

from .types import CodeGraph, GraphEdge, GraphNode, NodeKind, EdgeKind


_STMT_TAGS = ("select", "insert", "update", "delete", "sql")


def extract_mybatis_xml(
    file_paths: list[str],
    repo_root: str,
    graph: CodeGraph | None = None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """解析 Mapper XML，生成 SQL 语句节点并连到 Java Mapper。"""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    java_by_name: dict[str, list[str]] = {}
    java_by_qname: dict[str, list[str]] = {}
    if graph:
        for n in graph.nodes:
            if n.kind in (NodeKind.class_, NodeKind.interface, NodeKind.method):
                java_by_name.setdefault(n.name, []).append(n.id)
            if n.qualified_name:
                java_by_qname.setdefault(n.qualified_name, []).append(n.id)

    for rel_path in file_paths:
        if not rel_path.endswith(".xml"):
            continue
        full = os.path.join(repo_root, rel_path)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        if "<mapper" not in content:
            continue

        ns_m = re.search(r'<mapper\s+[^>]*namespace\s*=\s*["\']([^"\']+)["\']', content)
        if not ns_m:
            continue
        namespace = ns_m.group(1)
        mapper_simple = namespace.split(".")[-1]

        for tag in _STMT_TAGS:
            for m in re.finditer(
                rf"<{tag}\s+[^>]*\bid\s*=\s*[\"']([^\"']+)[\"']",
                content, re.IGNORECASE,
            ):
                stmt_id = m.group(1)
                node_id = f"{rel_path}::{namespace}::{stmt_id}"
                nodes.append(GraphNode(
                    id=node_id,
                    kind=NodeKind.config,
                    name=stmt_id,
                    file_path=rel_path,
                    language="xml",
                    qualified_name=f"{namespace}.{stmt_id}",
                    metadata={"framework": "mybatis", "stmt_kind": tag, "namespace": namespace},
                ))
                _add_edge(edges, seen_edges, rel_path, node_id, EdgeKind.contains)

                for java_id in java_by_qname.get(namespace, []) + java_by_name.get(mapper_simple, []):
                    _add_edge(edges, seen_edges, java_id, node_id, EdgeKind.references)
                for java_id in java_by_name.get(stmt_id, []):
                    _add_edge(edges, seen_edges, java_id, node_id, EdgeKind.references)

    return nodes, edges


def _add_edge(edges, seen, source, target, kind: EdgeKind):
    key = (source, target, kind.value)
    if key in seen or source == target:
        return
    seen.add(key)
    edges.append(GraphEdge(
        source=source, target=target, kind=kind,
        provenance="heuristic", confidence=0.72,
    ))
