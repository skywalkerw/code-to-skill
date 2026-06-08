"""从 CodeGraph 生成 role_index.json sidecar（M4 按 role 限定检索）。"""
from __future__ import annotations

from collections import defaultdict

from .types import CodeGraph, GraphNode, NodeKind


def _infer_role(node: GraphNode) -> tuple[str, str]:
    """从节点 metadata / kind 推断 (framework, role)。"""
    meta = node.metadata or {}
    framework = str(meta.get("framework") or "").strip()
    role = str(meta.get("role") or "").strip()

    if role:
        return framework or "custom", role

    name = (node.name or "").lower()
    if node.kind == NodeKind.route or "rest" in name or "controller" in name:
        return framework or "spring", "api_resource"
    if node.kind == NodeKind.job or "scheduled" in name:
        return framework or "spring", "scheduled_job"
    if "service" in name:
        return framework or "spring", "service"
    if "repository" in name or "mapper" in name:
        return framework or "spring", "repository"
    if node.kind == NodeKind.config:
        return framework or "spring", "config"

    if framework:
        return framework, meta.get("feature") or meta.get("annotation", "component")
    return "", ""


def build_role_index(graph: CodeGraph) -> dict:
    """构建 ``role_index.json`` 载荷。"""
    grouped: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    for node in graph.nodes:
        if node.kind in (NodeKind.file, NodeKind.module):
            continue
        if not node.file_path:
            continue
        framework, role = _infer_role(node)
        if not role:
            continue
        symbol = node.qualified_name or node.name
        if symbol:
            grouped[(framework, role, node.file_path)].add(symbol)

    entries = []
    for (framework, role, file_path), symbols in sorted(grouped.items()):
        entries.append({
            "framework": framework,
            "role": role,
            "file_path": file_path,
            "symbols": sorted(symbols)[:20],
        })

    return {
        "schema_version": "1.0",
        "entry_count": len(entries),
        "entries": entries,
    }
