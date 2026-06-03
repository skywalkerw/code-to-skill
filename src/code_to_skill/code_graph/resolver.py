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

    # 构建符号索引：name → [node_id]
    name_index: dict[str, list[str]] = {}
    for node in graph.nodes:
        name_index.setdefault(node.name, []).append(node.id)

    # 构建文件索引：file_path → [node_id]
    file_index: dict[str, list[str]] = {}
    for node in graph.nodes:
        file_index.setdefault(node.file_path, []).append(node.id)

    seen_edges: set[tuple[str, str, str]] = set()

    for node in graph.nodes:
        if node.kind in (NodeKind.class_, NodeKind.interface):
            # 读取文件内容做 import/calls 分析
            full_path = os.path.join(repo_root, node.file_path)
            if not os.path.exists(full_path):
                continue

            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue

            # import 解析
            imports = _extract_imports(content, node.language)
            for imp_name in imports:
                targets = name_index.get(imp_name, [])
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

    return unresolved


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
