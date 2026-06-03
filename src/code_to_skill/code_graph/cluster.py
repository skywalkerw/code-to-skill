"""模块树聚类。

三层策略：
1. 确定性粗分组（按顶层目录/包名）
2. LLM 细粒度聚类（预留，当前用确定性规则）
3. 确定性校验
"""
from __future__ import annotations

import os
from pathlib import Path

from .types import CodeGraph, ModuleTree, ModuleTreeNode, GraphNode, NodeKind


def build_module_tree(graph: CodeGraph, repo_root: str,
                      max_module_depth: int = 3,
                      max_components_per_group: int = 200) -> ModuleTree:
    """从 CodeGraph 构建模块树。

    当前实现：按文件目录层级自动分组。
    LLM 聚类为预留能力。

    Args:
        graph: 代码图谱
        repo_root: 仓库根路径
        max_module_depth: 最大模块树深度
        max_components_per_group: 单组最大组件数

    Returns:
        ModuleTree
    """
    tree = ModuleTree()

    # 按顶层目录分组
    root_dirs: dict[str, list[GraphNode]] = {}
    for node in graph.nodes:
        if node.kind == NodeKind.file:
            continue

        parts = Path(node.file_path).parts
        top_dir = parts[0] if parts else "root"

        root_dirs.setdefault(top_dir, []).append(node)

    # 构建树
    for dirname, nodes in root_dirs.items():
        child = _build_subtree(dirname, nodes, max_module_depth - 1, max_components_per_group)
        tree.root[dirname] = child

    return tree


def _build_subtree(name: str, nodes: list[GraphNode], remaining_depth: int,
                   max_components: int) -> ModuleTreeNode:
    """递归构建子树。"""
    components = [n.id for n in nodes]
    children: dict[str, ModuleTreeNode] = {}

    if remaining_depth > 1 and len(nodes) > max_components:
        # 按文件路径第二级分组
        subgroups: dict[str, list[GraphNode]] = {}
        for n in nodes:
            parts = Path(n.file_path).parts
            sub_key = parts[1] if len(parts) > 1 else "_root"
            subgroups.setdefault(sub_key, []).append(n)

        for sub_name, sub_nodes in subgroups.items():
            children[sub_name] = _build_subtree(sub_name, sub_nodes, remaining_depth - 1, max_components)

    return ModuleTreeNode(
        name=name,
        path=name,
        reason=f"Directory grouping: {len(components)} components",
        components=components[:100],  # 叶子节点截断
        children=children,
    )
