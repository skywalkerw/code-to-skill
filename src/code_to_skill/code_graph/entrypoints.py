"""入口点识别。

识别 REST/RPC/CLI/Job/Message/SPI/Test 等系统入口。
"""
from __future__ import annotations

import os
import re

from .types import CodeGraph, GraphNode, NodeKind, Entrypoint


# 入口识别模式（按语言）
_ENTRY_PATTERNS = {
    "java": {
        # Spring MVC + JAX-RS REST annotations
        "rest": [
            r"@(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\b",
            r"@Path\s*\(\s*[\"'].*[\"']\s*\)",
            r"@(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b",
        ],
        "job": [r"@Scheduled\b", r"@Quartz\b"],
        "config": [r"@Configuration\b", r"@ConfigurationProperties\b", r"@Component\b", r"@Service\b"],
        "test": [r"@Test\b", r"@SpringBootTest\b"],
        "service": [r"@Service\b", r"@Repository\b", r"@Component\b"],
    },
    "python": {
        "rest": [r"@app\.(route|get|post|put|delete)\b", r"@router\.(get|post)\b", r"@api\.(get|post)\b"],
        "cli": [r"def\s+main\s*\(", r"click\.command\b", r"argparse\.ArgumentParser"],
        "job": [r"@celery\.task\b", r"@scheduled\b"],
        "test": [r"def\s+test_\w+", r"class\s+Test\w+"],
    },
    "javascript": {
        "rest": [r"(app|router)\.(get|post|put|delete|patch)\("],
        "test": [r"test\(|it\(|describe\("],
    },
    "typescript": {
        "rest": [r"@(Controller|Get|Post|Put|Delete|Patch|Module)\b"],
    },
    "go": {
        "rest": [r"\.(GET|POST|PUT|DELETE|PATCH)\("],
        "cli": [r"func\s+main\s*\("],
        "test": [r"func\s+Test\w+\("],
    },
}


def find_entrypoints(graph: CodeGraph, repo_root: str) -> list[Entrypoint]:
    """扫描 CodeGraph 的节点，识别入口点。"""
    entrypoints: list[Entrypoint] = []

    for node in graph.nodes:
        if node.kind in (NodeKind.file, NodeKind.module):
            continue

        full_path = os.path.join(repo_root, node.file_path)
        if not os.path.exists(full_path):
            continue

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        patterns = _ENTRY_PATTERNS.get(node.language, {})

        # 按行检查（扩大范围以覆盖注解）
        lines = content.split("\n")
        start = max(0, node.start_line - 6)  # 扩展前5行覆盖注解
        end = min(len(lines), node.end_line + 1)
        node_text = "\n".join(lines[start:end])

        for entry_kind, pats in patterns.items():
            for pat in pats:
                if re.search(pat, node_text):
                    entrypoints.append(Entrypoint(
                        id=f"entry:{node.id}",
                        kind=entry_kind,
                        handler_node_id=node.id,
                        path=node.file_path,
                        protocol="http" if entry_kind == "rest" else "cli" if entry_kind == "cli" else "",
                        confidence=0.8,
                    ))

    # 去重
    seen: set[str] = set()
    unique: list[Entrypoint] = []
    for ep in entrypoints:
        key = f"{ep.kind}:{ep.handler_node_id}"
        if key not in seen:
            seen.add(key)
            unique.append(ep)

    return unique


def attach_entrypoints_to_graph(graph: CodeGraph, entrypoints: list[Entrypoint]) -> int:
    """将入口点挂入图谱：route 节点 + entry_to 边，供 trace 从 REST/CLI 追到 handler。"""
    from .types import EdgeKind, GraphEdge, GraphNode, NodeKind

    existing = {n.id for n in graph.nodes}
    added = 0
    for ep in entrypoints:
        if not ep.handler_node_id or ep.id in existing:
            continue
        handler = next((n for n in graph.nodes if n.id == ep.handler_node_id), None)
        graph.nodes.append(GraphNode(
            id=ep.id,
            kind=NodeKind.route,
            name=f"{ep.kind}:{handler.name if handler else ep.path}",
            file_path=ep.path,
            language=handler.language if handler else "",
            start_line=handler.start_line if handler else 0,
            end_line=handler.end_line if handler else 0,
            metadata={"entry_kind": ep.kind, "protocol": ep.protocol},
        ))
        graph.edges.append(GraphEdge(
            source=ep.id,
            target=ep.handler_node_id,
            kind=EdgeKind.entry_to,
            provenance="static",
            confidence=ep.confidence,
        ))
        existing.add(ep.id)
        added += 1
    return added
