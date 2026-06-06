"""React/JSX 组件 RENDERS 边合成（对齐 external codegraph 组件树）。"""
from __future__ import annotations

import os
import re

from .types import CodeGraph, GraphEdge, EdgeKind, GraphNode, NodeKind

_JSX_TAG = re.compile(r"<([A-Z][A-Za-z0-9_]*)\b")
_IMPORT_DEFAULT = re.compile(
    r"import\s+([A-Z][A-Za-z0-9_]*)\s+from\s+['\"]([^'\"]+)['\"]"
)
_IMPORT_NAMED = re.compile(
    r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]"
)


def synthesize_react_renders(graph: CodeGraph, repo_root: str) -> list[GraphEdge]:
    """从 JSX 标签与 import 合成 references 边（role=renders）。"""
    seen: set[tuple[str, str]] = {
        (e.source, e.target) for e in graph.edges
    }
    synthesized: list[GraphEdge] = []

    class_index: dict[str, list[str]] = {}
    file_nodes: dict[str, str] = {}
    for n in graph.nodes:
        if n.kind in (NodeKind.class_, NodeKind.function, NodeKind.method):
            class_index.setdefault(n.name, []).append(n.id)
        if n.kind == NodeKind.file:
            file_nodes[n.file_path] = n.id

    jsx_files = {
        n.file_path for n in graph.nodes
        if n.language in ("javascript", "typescript")
        and (n.file_path.endswith((".tsx", ".jsx")) or "components" in n.file_path.lower())
    }

    for rel_path in jsx_files:
        full = os.path.join(repo_root, rel_path)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        parent_id = file_nodes.get(rel_path, rel_path)
        import_map = _build_import_map(content, rel_path)

        for m in _JSX_TAG.finditer(content):
            comp = m.group(1)
            if comp in ("Fragment", "React", "Suspense"):
                continue
            targets = class_index.get(comp, [])
            if not targets and comp in import_map:
                targets = _resolve_import_target(
                    import_map[comp], rel_path, repo_root, class_index,
                )
            for target in targets[:2]:
                if target == parent_id:
                    continue
                key = (parent_id, target)
                if key in seen:
                    continue
                seen.add(key)
                synthesized.append(GraphEdge(
                    source=parent_id,
                    target=target,
                    kind=EdgeKind.references,
                    confidence=0.42,
                    provenance="heuristic",
                ))

    graph.edges.extend(synthesized)
    return synthesized


def _build_import_map(content: str, rel_path: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for m in _IMPORT_DEFAULT.finditer(content):
        mapping[m.group(1)] = m.group(2)
    for m in _IMPORT_NAMED.finditer(content):
        module = m.group(2)
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            if " as " in part:
                _orig, alias = part.split(" as ", 1)
                mapping[alias.strip()] = module
            else:
                mapping[part.strip()] = module
    return mapping


def _resolve_import_target(
    module: str,
    from_file: str,
    repo_root: str,
    class_index: dict[str, list[str]],
) -> list[str]:
    """尽力将 import 路径解析为同仓库符号。"""
    if module.startswith("."):
        base = os.path.dirname(from_file)
        candidate = os.path.normpath(os.path.join(base, module)).replace("\\", "/")
        for ext in ("", ".tsx", ".ts", ".jsx", ".js", "/index.tsx", "/index.ts"):
            stem = candidate + ext
            for name, ids in class_index.items():
                for nid in ids:
                    if stem in nid or stem.split("/")[-1].replace(ext, "") in nid:
                        return [nid]
    base_name = os.path.basename(module).replace(".tsx", "").replace(".ts", "")
    return class_index.get(base_name, [])
