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
from .framework import extract_framework_metadata, extract_spring_metadata, merge_custom_patterns
from .resolver import resolve_references
from .callback_synthesis import synthesize_interface_dispatch
from .mybatis_xml import extract_mybatis_xml
from .js_callbacks import synthesize_js_callbacks
from .react_renders import synthesize_react_renders
from .entrypoints import attach_entrypoints_to_graph, find_entrypoints
from .cluster import build_module_tree, refine_leaf_contexts
from .leaf_context import generate_leaf_contexts
from .traversal import GraphTraverser
from .db import GraphDB
from .graph_queries import GraphQueryEngine
from .context_builder import ContextBuilder
from .evidence import EvidenceBuilder
from .registry import GraphRegistry, GraphSource
from code_to_skill.time_utils import local_timestamp

from .types import (
    FileInventory, CodeGraph, ModuleTree, LeafContext, Entrypoint,
    ParseError, UnresolvedEdge, CodeGraphManifest,
)


def run_code_graph_pipeline(
    repo_root: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_leaf_tokens: int = 8000,
    max_module_depth: int = 3,
    output_root: str | None = None,
    use_cache: bool = False,
    repo_id: str = "",
    snapshot_ref: str = "HEAD",
    custom_patterns: dict[str, dict[str, str]] | None = None,
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

    db_path = os.path.join(output_root, "graph.db") if output_root else ""
    db = GraphDB(db_path) if (use_cache and output_root) else None

    # Step 1: 扫描
    inventory = scan_repo(repo_root, include=include, exclude=exclude)
    results["inventory"] = inventory
    source_files = [f.path for f in inventory.files if f.kind == "source" and f.language]
    file_hashes = {f.path: f.source_hash for f in inventory.files if f.source_hash}

    # Step 0/2: 增量或全量解析
    parse_errors: list = []
    if db and db.is_fresh():
        changed = db.get_changed_files(file_hashes)
        if not changed:
            cached_graph = db.load_graph()
            if cached_graph.nodes:
                entrypoints = find_entrypoints(cached_graph, repo_root)
                ep_attached = attach_entrypoints_to_graph(cached_graph, entrypoints)
                if ep_attached:
                    print(f"[M1] 缓存图谱补挂入口点: {ep_attached} entry_to 边")
                results["graph"] = cached_graph
                results["errors"] = []
                results["unresolved_edges"] = []
                results["entrypoints"] = entrypoints
                module_tree = build_module_tree(cached_graph, repo_root, max_module_depth=max_module_depth)
                results["module_tree"] = module_tree
                results["leaf_contexts"] = generate_leaf_contexts(cached_graph, module_tree, repo_root, max_leaf_tokens)
                print(f"[M1] 从缓存加载: {len(cached_graph.nodes)} nodes")
                return results
            to_parse = source_files
            graph = CodeGraph()
        else:
            print(f"[M1] 增量解析: {len(changed)}/{len(source_files)} 文件变更")
            db.remove_nodes_for_files(changed)
            graph = db.load_graph()
            to_parse = changed
    else:
        to_parse = source_files
        graph = CodeGraph()

    partial, parse_errors = parse_files(to_parse, repo_root)
    graph = _merge_graph(graph, partial, to_parse)
    results["graph"] = graph
    results["errors"] = parse_errors

    # Step 2.5: 框架提取（Spring/MyBatis + project 自定义模式）
    java_files = [f for f in source_files if f.endswith(".java")]
    if java_files:
        fw_nodes, fw_edges = extract_framework_metadata(
            java_files, repo_root, graph, custom_patterns=custom_patterns,
        )
        graph.nodes.extend(fw_nodes)
        graph.edges.extend(fw_edges)
        if custom_patterns and fw_nodes:
            custom_count = sum(1 for n in fw_nodes if n.metadata.get("custom"))
            if custom_count:
                print(f"[M1] 自定义框架节点: {custom_count}")

    xml_files = [f.path for f in inventory.files if f.path.endswith(".xml")]
    if xml_files:
        mx_nodes, mx_edges = extract_mybatis_xml(xml_files, repo_root, graph)
        if mx_nodes:
            graph.nodes.extend(mx_nodes)
            graph.edges.extend(mx_edges)
            print(f"[M1] MyBatis XML: {len(mx_nodes)} statements")

    # Step 3: 引用解析 + 派发合成
    unresolved = resolve_references(graph, repo_root)
    synthesized = synthesize_interface_dispatch(graph)
    js_syn = synthesize_js_callbacks(graph, repo_root)
    react_syn = synthesize_react_renders(graph, repo_root)
    if synthesized:
        print(f"[M1] 合成派发边: {len(synthesized)}")
    if js_syn:
        print(f"[M1] JS 回调边: {len(js_syn)}")
    if react_syn:
        print(f"[M1] React RENDERS 边: {len(react_syn)}")
    results["unresolved_edges"] = unresolved
    results["synthesized_edges"] = synthesized

    # Step 4: 入口点 + entry_to 边（供 trace 从 REST/CLI 追到 handler）
    entrypoints = find_entrypoints(graph, repo_root)
    ep_attached = attach_entrypoints_to_graph(graph, entrypoints)
    if ep_attached:
        print(f"[M1] 入口点挂接: {ep_attached} entry_to 边")
    results["entrypoints"] = entrypoints

    # Step 5: 模块树
    module_tree = build_module_tree(graph, repo_root, max_module_depth=max_module_depth)
    results["module_tree"] = module_tree

    # Step 6: 叶子上下文
    leaf_contexts = generate_leaf_contexts(graph, module_tree, repo_root, max_leaf_tokens=max_leaf_tokens)
    # 细化：大上下文按文件拆分
    leaf_contexts = refine_leaf_contexts(leaf_contexts, graph, repo_root, max_leaf_tokens)
    results["leaf_contexts"] = leaf_contexts

    # 写文件 + 缓存
    if output_root:
        _write_outputs(
            results, output_root, repo_root,
            repo_id=repo_id,
            snapshot_ref=snapshot_ref,
            include=include or [],
            exclude=exclude or [],
        )
        if use_cache and db:
            db.save_graph(results["graph"])
            db.save_unresolved_refs(results.get("unresolved_edges", []))
            print(f"[M1] 缓存已保存: {db_path}")

    return results


def _merge_graph(base: CodeGraph, incoming: CodeGraph, file_paths: list[str]) -> CodeGraph:
    """将新解析结果合并进已有图谱（按文件路径替换）。"""
    fps = set(file_paths)
    removed_ids = {n.id for n in base.nodes if n.file_path in fps}
    kept_nodes = [n for n in base.nodes if n.file_path not in fps]
    kept_edges = [
        e for e in base.edges
        if e.source not in removed_ids and e.target not in removed_ids
    ]
    return CodeGraph(nodes=kept_nodes + incoming.nodes, edges=kept_edges + incoming.edges)


def _write_outputs(
    results: dict,
    output_root: str,
    repo_root: str,
    *,
    repo_id: str = "",
    snapshot_ref: str = "HEAD",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
):
    """将产物序列化为文件。"""
    os.makedirs(output_root, exist_ok=True)

    graph: CodeGraph = results["graph"]
    inventory: FileInventory = results["inventory"]
    from .parser import get_last_parse_stats
    from .ts_backend import backend_status

    parse_stats = get_last_parse_stats().to_dict()
    parse_stats["runtime"] = backend_status()
    stats = {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "files": len(inventory.files),
        "parse_backend": parse_stats.get("by_backend", {}),
    }

    manifest = CodeGraphManifest(
        repo_id=repo_id,
        repo_root=os.path.abspath(repo_root),
        snapshot_ref=snapshot_ref,
        analyzed_at=local_timestamp(),
        include_patterns=include or [],
        exclude_patterns=exclude or [],
        stats=stats,
    )
    with open(os.path.join(output_root, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(manifest.model_dump_json(indent=2))

    with open(os.path.join(output_root, "file_inventory.json"), "w", encoding="utf-8") as f:
        f.write(inventory.model_dump_json(indent=2))

    # graph.json
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
    generated = [f.path for f in inventory.files if f.kind == "generated"]
    with open(os.path.join(diag_dir, "generated_files.json"), "w", encoding="utf-8") as f:
        json.dump(generated, f, indent=2, ensure_ascii=False)
    with open(os.path.join(diag_dir, "parse_stats.json"), "w", encoding="utf-8") as f:
        json.dump(parse_stats, f, indent=2, ensure_ascii=False)

    print(f"[M1] 产物已写入: {output_root}")
