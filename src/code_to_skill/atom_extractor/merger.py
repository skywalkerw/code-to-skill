"""Atom 合并与聚类。"""
from __future__ import annotations

import os
from collections import defaultdict

from .keywords import extract_seed_check_tokens
from .types import SkillAtom


def merge_atoms(atoms: list[SkillAtom]) -> list[SkillAtom]:
    """合并相似 atom：同一 claim 合并 source_refs，去重。"""
    merged: list[SkillAtom] = []
    seen_claims: dict[str, int] = {}  # claim → index in merged

    for atom in atoms:
        claim_key = atom.claim[:80]  # 取前80字符作为去重键

        if claim_key in seen_claims:
            idx = seen_claims[claim_key]
            existing = merged[idx]
            # 合并 source_refs
            existing_ids = {(s.type, s.id) for s in existing.source_refs}
            for s in atom.source_refs:
                if (s.type, s.id) not in existing_ids:
                    existing.source_refs.append(s)
                    existing_ids.add((s.type, s.id))
            # 取更高置信度
            existing.confidence = max(existing.confidence, atom.confidence)
            # 合并 checks
            for c in atom.checks:
                if c not in existing.checks:
                    existing.checks.append(c)
        else:
            seen_claims[claim_key] = len(merged)
            merged.append(atom)

    return merged


def cluster_by_domain(atoms: list[SkillAtom]) -> dict[str, list[SkillAtom]]:
    """按领域聚类。"""
    clusters: dict[str, list[SkillAtom]] = defaultdict(list)
    for atom in atoms:
        domain = atom.applicability.get("domain", "general")
        clusters[domain].append(atom)
    return dict(clusters)


def _context_refs_from_atom(atom: SkillAtom) -> list[str]:
    """从 atom source_refs / edge_path 生成 items.json 兼容的 context_refs。"""
    refs: list[str] = []
    for source in atom.source_refs:
        if source.type != "code":
            continue
        sid = (source.id or "").strip()
        if not sid:
            continue
        if "/" in sid or "." in os.path.basename(sid):
            refs.append(sid)
        elif source.edge_path:
            symbol = source.edge_path[0]
            refs.append(f"{sid}#{symbol}" if "#" not in sid else sid)
        elif "::" in sid:
            path_part, sym = sid.rsplit("::", 1)
            refs.append(f"{path_part}#{sym}")
        else:
            refs.append(sid)
    return refs[:4]


def generate_benchmark_seeds(atoms: list[SkillAtom]) -> list[dict]:
    """从高价值 atom 生成 benchmark 种子。

    输出对齐 ``items.json`` schema：``id``、``question``、``expected_checks``、``context_refs``。
    """
    seeds: list[dict] = []
    for atom in atoms:
        if atom.confidence < 0.6:
            continue

        checks = list(atom.checks)
        existing = {c.lower() for c in checks}
        for token in extract_seed_check_tokens(atom.claim, atom.action, limit=5):
            if token.lower() not in existing:
                checks.append(token)
                existing.add(token.lower())

        item_id = f"seed-{atom.atom_id}"
        seeds.append({
            "id": item_id,
            "question": atom.claim[:200],
            "expected_checks": checks[:5],
            "context_refs": _context_refs_from_atom(atom),
            "source_atom_ids": [atom.atom_id],
            "risk": atom.risk,
        })
    return seeds
