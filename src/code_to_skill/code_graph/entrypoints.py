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
        "rest": [r"@(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\b"],
        "job": [r"@Scheduled\b", r"@Quartz\b"],
        "config": [r"@Configuration\b", r"@ConfigurationProperties\b"],
        "test": [r"@Test\b", r"@SpringBootTest\b"],
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

        # 按行检查（入口点相关的行）
        lines = content.split("\n")
        start = max(0, node.start_line - 1)
        end = min(len(lines), node.end_line)
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
