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
    graph_db_path: str = "",
    repo_root: str = "",
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

    # Step 1: 抽取（规则模式 + LLM 模式并行，自动降级）
    from .extractor.llm_extractor import extract_from_code_llm, extract_from_docs_llm

    code_atoms = extract_from_code(leaf_contexts)
    doc_atoms = extract_from_docs(document_chunks)

    # LLM 增强：当 API key 可用时，与规则模式合并
    code_llm_atoms = extract_from_code_llm(leaf_contexts)
    doc_llm_atoms = extract_from_docs_llm(document_chunks)

    raw_atoms = code_atoms + doc_atoms + code_llm_atoms + doc_llm_atoms

    # Step 2: 评分
    scored = score_atoms(raw_atoms)

    # Step 3: 合并
    merged = merge_atoms(scored)

    # Step 3.5: 证据对齐（跨来源匹配 + 置信度提升）
    merged = align_atoms(merged)

    # Step 3.6: 图谱证据增强（edge_path + evidence_index）
    evidence_index = []
    if graph_db_path:
        try:
            from code_to_skill.code_graph.evidence import EvidenceBuilder
            builder = EvidenceBuilder(graph_db_path, repo_root)
            merged = builder.enrich_atoms(merged)
            evidence_index = builder.build_evidence_index(merged)
        except (FileNotFoundError, OSError):
            pass

    # Step 4: 聚类
    clusters = cluster_by_domain(merged)

    # Step 5: 种子
    seeds = generate_benchmark_seeds(merged)

    results = {
        "raw_atoms": raw_atoms,
        "merged_atoms": merged,
        "benchmark_seeds": seeds,
        "clusters": clusters,
        "evidence_index": evidence_index,
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

    # evidence_index.json
    if results.get("evidence_index"):
        with open(os.path.join(output_root, "evidence_index.json"), "w", encoding="utf-8") as f:
            json.dump(
                [e.model_dump() for e in results["evidence_index"]],
                f, indent=2, ensure_ascii=False,
            )

    accepted_count = sum(1 for a in results["merged_atoms"] if a.status in ("accepted", "candidate"))
    ev_count = len(results.get("evidence_index") or [])
    print(
        f"[M3] Atom 抽取完成: {len(results['raw_atoms'])} raw → "
        f"{len(results['merged_atoms'])} merged → {accepted_count} accepted"
        + (f" | evidence={ev_count}" if ev_count else "")
    )
