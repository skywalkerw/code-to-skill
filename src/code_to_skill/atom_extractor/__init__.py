"""模块 3：SkillAtom 抽取。

主流水线：
    extract_from_code + extract_from_docs → score → merge → cluster → seeds
"""
from __future__ import annotations

import json
import os

from .types import SkillAtom, RawAtom, SourceRef
from .extractor import extract_from_code, extract_from_docs
from .scorer import score_atoms
from .merger import merge_atoms, cluster_by_domain, generate_benchmark_seeds
from .aligner import align_atoms


def run_atom_extraction(
    leaf_contexts: list[dict] | None = None,
    document_chunks: list[dict] | None = None,
    output_root: str | None = None,
) -> dict:
    """运行 SkillAtom 抽取流水线。

    Args:
        leaf_contexts: 从 M1 产出的叶子上下文列表
        document_chunks: 从 M2 产出的 DocumentChunk 列表
        output_root: 产物输出目录

    Returns:
        {
            "raw_atoms": list[RawAtom],
            "merged_atoms": list[SkillAtom],
            "benchmark_seeds": list[dict],
            "clusters": dict,
        }
    """
    leaf_contexts = leaf_contexts or []
    document_chunks = document_chunks or []

    # Step 1: 抽取
    code_atoms = extract_from_code(leaf_contexts)
    doc_atoms = extract_from_docs(document_chunks)
    raw_atoms = code_atoms + doc_atoms

    # Step 2: 评分
    scored = score_atoms(raw_atoms)

    # Step 3: 合并
    merged = merge_atoms(scored)

    # Step 3.5: 证据对齐（跨来源匹配 + 置信度提升）
    merged = align_atoms(merged)

    # Step 4: 聚类
    clusters = cluster_by_domain(merged)

    # Step 5: 种子
    seeds = generate_benchmark_seeds(merged)

    results = {
        "raw_atoms": raw_atoms,
        "merged_atoms": merged,
        "benchmark_seeds": seeds,
        "clusters": clusters,
    }

    # 写文件
    if output_root:
        _write_outputs(results, output_root)

    return results


def _write_outputs(results: dict, output_root: str):
    os.makedirs(output_root, exist_ok=True)

    # merged_atoms.jsonl
    with open(os.path.join(output_root, "merged_atoms.jsonl"), "w", encoding="utf-8") as f:
        for a in results["merged_atoms"]:
            f.write(a.model_dump_json() + "\n")

    # benchmark_seeds.jsonl
    with open(os.path.join(output_root, "benchmark_seeds.jsonl"), "w", encoding="utf-8") as f:
        for s in results["benchmark_seeds"]:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # rejected_atoms.jsonl
    rejected = [a for a in results["merged_atoms"] if a.status == "rejected"]
    with open(os.path.join(output_root, "rejected_atoms.jsonl"), "w", encoding="utf-8") as f:
        for a in rejected:
            f.write(a.model_dump_json() + "\n")

    accepted_count = sum(1 for a in results["merged_atoms"] if a.status in ("accepted", "candidate"))
    print(f"[M3] Atom 抽取完成: {len(results['raw_atoms'])} raw → {len(results['merged_atoms'])} merged → {accepted_count} accepted")
