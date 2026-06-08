"""模块树聚类 + 叶子上下文细化。

按包路径分层，确保每个叶子模块组件数可控。
"""
from __future__ import annotations

from pathlib import Path
from collections import defaultdict

from .types import CodeGraph, ModuleTree, ModuleTreeNode, GraphNode, NodeKind, LeafContext
from .leaf_context import estimate_tokens


def _group_key_for_node(node: GraphNode, split_strategy: str) -> str:
    parts = Path(node.file_path).parts
    if not parts:
        return "root"
    if split_strategy == "package_path":
        depth = min(4, max(1, len(parts) - 1))
        return "/".join(parts[:depth])
    return parts[0]


def build_module_tree(
    graph: CodeGraph,
    repo_root: str,
    max_module_depth: int = 3,
    max_components_per_group: int = 200,
    *,
    split_strategy: str = "top_dir",
    llm_clustering_enabled: bool = False,
) -> ModuleTree:
    """从 CodeGraph 构建模块树。按包路径层级自动分组。

    ``split_strategy``:
    - ``top_dir``: 按顶层目录分组（默认）
    - ``package_path``: 按文件路径前 3–4 段分组（更接近包路径）
    """
    if llm_clustering_enabled:
        import logging
        logging.getLogger(__name__).info(
            "[M1] llm_clustering_enabled=true（预留；当前使用规则聚类 split_strategy=%s）",
            split_strategy,
        )

    tree = ModuleTree()
    non_file_nodes = [n for n in graph.nodes if n.kind != NodeKind.file]
    if not non_file_nodes:
        return tree

    groups: dict[str, list[GraphNode]] = defaultdict(list)
    for node in non_file_nodes:
        key = _group_key_for_node(node, split_strategy)
        groups[key].append(node)

    for name, nodes in groups.items():
        tree.root[name] = _build_node(name, nodes, max_module_depth - 1, max_components_per_group)

    return tree


def _build_node(name: str, nodes: list[GraphNode], depth: int, max_comp: int) -> ModuleTreeNode:
    """递归构建树节点，按次级目录拆分。"""
    components = [n.id for n in nodes]
    children: dict[str, ModuleTreeNode] = {}

    # 如果节点数超标且还有深度，继续拆分
    if depth > 0 and len(nodes) > max_comp:
        subgroups: dict[str, list[GraphNode]] = defaultdict(list)
        for n in nodes:
            parts = Path(n.file_path).parts
            # 跳过前几级找到变化点
            sub_key = "_".join(parts[:4]) if len(parts) >= 4 else parts[-1] if parts else "_"
            subgroups[sub_key].append(n)

        # 如果拆分后组数太少，用更细的粒度
        if len(subgroups) <= 1:
            subgroups.clear()
            for n in nodes:
                file_name = Path(n.file_path).stem
                subgroups[file_name[:20]].append(n)

        for sub_name, sub_nodes in subgroups.items():
            children[sub_name] = _build_node(sub_name, sub_nodes, depth - 1, max_comp)

    return ModuleTreeNode(
        name=name,
        path=name,
        reason=f"{len(components)} components",
        components=components[:max_comp],
        children=children,
    )


def refine_leaf_contexts(
    contexts: list[LeafContext],
    graph: CodeGraph,
    repo_root: str,
    max_leaf_tokens: int = 8000,
) -> list[LeafContext]:
    """将大的叶子上下文按文件进一步拆分为更小的上下文包。"""
    refined: list[LeafContext] = []

    for ctx in contexts:
        if ctx.token_estimate <= max_leaf_tokens * 0.8:
            refined.append(ctx)
            continue

        # 按文件分组拆分
        by_file: dict[str, list[dict]] = defaultdict(list)
        for snip in ctx.source_snippets:
            fpath = snip.get("file_path", "unknown")
            by_file[fpath].append(snip)

        for fpath, snippets in by_file.items():
            total_tokens = sum(estimate_tokens(s.get("text", "")) for s in snippets)
            # 截断到预算
            kept: list[dict] = []
            kept_tokens = 0
            for s in snippets:
                st = estimate_tokens(s.get("text", ""))
                if kept_tokens + st <= max_leaf_tokens * 0.9:
                    kept.append(s)
                    kept_tokens += st

            refined.append(LeafContext(
                leaf_id=f"{ctx.leaf_id}__{Path(fpath).stem}",
                module_path=ctx.module_path + [fpath],
                component_ids=[s.get("node_id", "") for s in snippets],
                source_snippets=kept,
                token_estimate=kept_tokens,
                parent_leaf=ctx.leaf_id,
                split_reason="file_level_split",
            ))

    return refined or contexts
