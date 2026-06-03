"""叶子上下文包生成。

从模块树和代码图谱中为每个叶子模块生成上下文包。
含 token 估算和超预算拆分。
"""
from __future__ import annotations

import os
from collections import Counter

from .types import CodeGraph, ModuleTree, ModuleTreeNode, LeafContext, GraphNode

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

    def estimate_tokens(text: str) -> int:
        return len(_enc.encode(text))
except ImportError:
    def estimate_tokens(text: str) -> int:
        # 粗略估算：英文 1 token ≈ 4 chars，中文 1 token ≈ 1.5 chars
        return len(text) // 4


def generate_leaf_contexts(
    graph: CodeGraph,
    module_tree: ModuleTree,
    repo_root: str,
    max_leaf_tokens: int = 8000,
) -> list[LeafContext]:
    """为每个叶子模块生成上下文包。

    Args:
        graph: 代码图谱
        module_tree: 模块树
        repo_root: 仓库根路径
        max_leaf_tokens: 单叶子 token 上限

    Returns:
        LeafContext 列表
    """
    contexts: list[LeafContext] = []

    # 构建 node lookup
    node_map: dict[str, GraphNode] = {n.id: n for n in graph.nodes}

    def _walk_tree(tree: dict[str, ModuleTreeNode], path: list[str]):
        for name, node in tree.items():
            current_path = path + [name]
            if not node.children:
                # 叶子模块
                ctx = _build_leaf(node, current_path, graph, node_map, repo_root, max_leaf_tokens)
                contexts.append(ctx)
            else:
                _walk_tree(node.children, current_path)

    _walk_tree(module_tree.root, [])
    return contexts


def _build_leaf(
    tree_node: ModuleTreeNode,
    path: list[str],
    graph: CodeGraph,
    node_map: dict[str, GraphNode],
    repo_root: str,
    max_leaf_tokens: int,
) -> LeafContext:
    """构建单个叶子上下文包。"""
    leaf_id = "_".join(path)

    # 收集源码片段
    snippets: list[dict] = []
    total_tokens = 0
    component_ids: list[str] = []

    for comp_id in tree_node.components:
        gn = node_map.get(comp_id)
        if not gn:
            continue

        component_ids.append(comp_id)

        full_path = os.path.join(repo_root, gn.file_path)
        if not os.path.exists(full_path):
            continue

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue

        # 截取节点所在行 + 上下文
        start = max(0, gn.start_line - 1)
        end = min(len(lines), gn.end_line)
        snippet_text = "".join(lines[start:end])
        snippet_tokens = estimate_tokens(snippet_text)

        if total_tokens + snippet_tokens <= max_leaf_tokens * 0.9:
            snippets.append({
                "node_id": comp_id,
                "file_path": gn.file_path,
                "start_line": gn.start_line,
                "end_line": gn.end_line,
                "text": snippet_text,
            })
            total_tokens += snippet_tokens

    # 收集相关边
    important_edges: list[dict] = []
    comp_set = set(component_ids)
    for edge in graph.edges:
        if edge.source in comp_set or edge.target in comp_set:
            important_edges.append({
                "source": edge.source,
                "target": edge.target,
                "kind": edge.kind.value,
                "provenance": edge.provenance,
            })

    return LeafContext(
        leaf_id=leaf_id,
        module_path=path,
        component_ids=component_ids,
        source_snippets=snippets,
        important_edges=important_edges[:50],  # 截断
        token_estimate=total_tokens,
    )
