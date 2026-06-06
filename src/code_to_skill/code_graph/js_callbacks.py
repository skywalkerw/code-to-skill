"""JavaScript/TypeScript 回调与异步派发边合成。"""
from __future__ import annotations

import os
import re

from .types import CodeGraph, GraphEdge, EdgeKind, GraphNode, NodeKind


def synthesize_js_callbacks(graph: CodeGraph, repo_root: str) -> list[GraphEdge]:
    """从 JS/TS 源码提取回调注册并合成 calls/references 边。"""
    seen: set[tuple[str, str, str]] = {
        (e.source, e.target, e.kind.value) for e in graph.edges
    }
    synthesized: list[GraphEdge] = []
    name_index: dict[str, list[str]] = {}
    for n in graph.nodes:
        if n.kind in (NodeKind.function, NodeKind.method):
            name_index.setdefault(n.name, []).append(n.id)

    files = {n.file_path for n in graph.nodes if n.language in ("javascript", "typescript")}
    patterns = [
        (r"\.addEventListener\s*\(\s*['\"][\w-]+['\"]\s*,\s*(\w+)", "event_listener"),
        (r"\.then\s*\(\s*(\w+)", "promise_then"),
        (r"\.catch\s*\(\s*(\w+)", "promise_catch"),
        (r"setTimeout\s*\(\s*(\w+)", "set_timeout"),
        (r"setInterval\s*\(\s*(\w+)", "set_interval"),
        (r"\.map\s*\(\s*(\w+)", "array_map"),
        (r"\.forEach\s*\(\s*(\w+)", "array_foreach"),
        # React / JSX
        (r"onClick=\{(\w+)\}", "jsx_onclick"),
        (r"onChange=\{(\w+)\}", "jsx_onchange"),
        (r"onSubmit=\{(\w+)\}", "jsx_onsubmit"),
        (r"useEffect\s*\(\s*\(\s*\)\s*=>\s*\{[^}]*\b(\w+)\s*\(", "react_use_effect"),
    ]

    for rel_path in files:
        full = os.path.join(repo_root, rel_path)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        file_ids = {
            n.id for n in graph.nodes
            if n.file_path == rel_path and n.kind == NodeKind.file
        }
        parent_id = next(iter(file_ids), rel_path)

        for pat, _role in patterns:
            for m in re.finditer(pat, content):
                cb_name = m.group(1)
                if cb_name in ("function", "async", "this"):
                    continue
                for target in name_index.get(cb_name, [])[:3]:
                    tgt_node = next((n for n in graph.nodes if n.id == target), None)
                    if not tgt_node or tgt_node.file_path != rel_path:
                        continue
                    key = (parent_id, target, EdgeKind.calls.value)
                    if key in seen:
                        continue
                    seen.add(key)
                    synthesized.append(GraphEdge(
                        source=parent_id,
                        target=target,
                        kind=EdgeKind.calls,
                        confidence=0.38,
                        provenance="heuristic",
                    ))

    graph.edges.extend(synthesized)
    return synthesized
