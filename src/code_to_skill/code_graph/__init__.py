"""模块 1：代码仓库到代码图谱与模块树。

主流水线：
    scan_repo → parse_files → resolve_references → find_entrypoints → build_module_tree → generate_leaf_contexts
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .scanner import scan_repo
from .parser import parse_files
from .resolver import resolve_references
from .entrypoints import find_entrypoints
from .cluster import build_module_tree, refine_leaf_contexts
from .leaf_context import generate_leaf_contexts
from .types import FileInventory, CodeGraph, ModuleTree, LeafContext, Entrypoint, ParseError, UnresolvedEdge


def run_code_graph_pipeline(
    repo_root: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_leaf_tokens: int = 8000,
    max_module_depth: int = 3,
    output_root: str | None = None,
) -> dict:
    """运行完整的代码图谱构建流水线。

    Args:
        repo_root: 仓库根目录
        include: 包含的 glob 模式
        exclude: 排除的 glob 模式
        max_leaf_tokens: 叶子上下文 token 上限
        max_module_depth: 模块树最大深度
        output_root: 产物输出目录（不指定则不写文件）

    Returns:
        {
            "inventory": FileInventory,
            "graph": CodeGraph,
            "entrypoints": list[Entrypoint],
            "module_tree": ModuleTree,
            "leaf_contexts": list[LeafContext],
            "errors": list[ParseError],
            "unresolved_edges": list[UnresolvedEdge],
        }
    """
    results: dict = {}

    # Step 1: 扫描
    inventory = scan_repo(repo_root, include=include, exclude=exclude)
    results["inventory"] = inventory

    # Step 2: 解析（仅源码文件）
    source_files = [f.path for f in inventory.files if f.kind == "source" and f.language]
    graph, parse_errors = parse_files(source_files, repo_root)
    results["graph"] = graph
    results["errors"] = parse_errors

    # Step 3: 引用解析
    unresolved = resolve_references(graph, repo_root)
    results["unresolved_edges"] = unresolved

    # Step 4: 入口点
    entrypoints = find_entrypoints(graph, repo_root)
    results["entrypoints"] = entrypoints

    # Step 5: 模块树
    module_tree = build_module_tree(graph, repo_root, max_module_depth=max_module_depth)
    results["module_tree"] = module_tree

    # Step 6: 叶子上下文
    leaf_contexts = generate_leaf_contexts(graph, module_tree, repo_root, max_leaf_tokens=max_leaf_tokens)
    # 细化：大上下文按文件拆分
    leaf_contexts = refine_leaf_contexts(leaf_contexts, graph, repo_root, max_leaf_tokens)
    results["leaf_contexts"] = leaf_contexts

    # 写文件
    if output_root:
        _write_outputs(results, output_root, repo_root)

    return results


def _write_outputs(results: dict, output_root: str, repo_root: str):
    """将产物序列化为文件。"""
    os.makedirs(output_root, exist_ok=True)

    # graph.json
    graph: CodeGraph = results["graph"]
    with open(os.path.join(output_root, "graph.json"), "w", encoding="utf-8") as f:
        f.write(graph.model_dump_json(indent=2))

    # entrypoints.json
    with open(os.path.join(output_root, "entrypoints.json"), "w", encoding="utf-8") as f:
        eps = [ep.model_dump() for ep in results["entrypoints"]]
        json.dump(eps, f, indent=2, ensure_ascii=False)

    # module_tree.json
    mt: ModuleTree = results["module_tree"]
    with open(os.path.join(output_root, "module_tree.json"), "w", encoding="utf-8") as f:
        f.write(mt.model_dump_json(indent=2))

    # leaf_contexts/
    ctx_dir = os.path.join(output_root, "leaf_contexts")
    os.makedirs(ctx_dir, exist_ok=True)
    for ctx in results["leaf_contexts"]:
        path = os.path.join(ctx_dir, f"{ctx.leaf_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(ctx.model_dump_json(indent=2))

    # diagnostics/
    diag_dir = os.path.join(output_root, "diagnostics")
    os.makedirs(diag_dir, exist_ok=True)
    with open(os.path.join(diag_dir, "parse_errors.json"), "w", encoding="utf-8") as f:
        json.dump([e.model_dump() for e in results["errors"]], f, indent=2, ensure_ascii=False)
    with open(os.path.join(diag_dir, "unresolved_edges.json"), "w", encoding="utf-8") as f:
        json.dump([u.model_dump() for u in results["unresolved_edges"]], f, indent=2, ensure_ascii=False)

    print(f"[M1] 产物已写入: {output_root}")
