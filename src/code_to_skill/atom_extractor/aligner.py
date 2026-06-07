"""证据对齐：跨代码/文档来源匹配同一概念的 atom。"""
from __future__ import annotations

from .keywords import extract_alignment_tokens
from .types import SkillAtom, SourceRef


def align_atoms(atoms: list[SkillAtom]) -> list[SkillAtom]:
    """对齐同一概念但来自不同来源的原子。

    策略：按 kind + 关键词重叠度分组，合并 source_refs 并提升 confidence。
    """
    if len(atoms) <= 1:
        return atoms

    aligned: list[SkillAtom] = []
    used: set[int] = set()

    for i, a in enumerate(atoms):
        if i in used:
            continue

        group = [a]
        used.add(i)

        a_keywords = _atom_keywords(a)
        for j, b in enumerate(atoms):
            if j in used or j == i:
                continue
            if a.kind != b.kind:
                continue

            overlap = a_keywords & _atom_keywords(b)
            if len(overlap) >= 2:
                group.append(b)
                used.add(j)

        if len(group) == 1:
            aligned.append(a)
        else:
            aligned.append(_merge_group(group))

    return aligned


def _atom_keywords(atom: SkillAtom) -> set[str]:
    parts = [atom.claim, atom.action, atom.negative_rule, *atom.checks]
    tokens: set[str] = set()
    for part in parts:
        tokens.update(extract_alignment_tokens(part or ""))
    return tokens


def _merge_group(group: list[SkillAtom]) -> SkillAtom:
    """合并一组同概念原子。"""
    base = group[0]

    seen_sources: set[tuple[str, str]] = set()
    for s in base.source_refs:
        seen_sources.add((s.type, s.id))
    for a in group[1:]:
        for s in a.source_refs:
            key = (s.type, s.id)
            if key not in seen_sources:
                base.source_refs.append(s)
                seen_sources.add(key)

    seen_checks = set(base.checks)
    for a in group[1:]:
        for c in a.checks:
            if c not in seen_checks:
                base.checks.append(c)
                seen_checks.add(c)

    n_files = len({s.id for s in base.source_refs})
    bonus = min(0.15, (n_files - 1) * 0.05)
    base.confidence = min(1.0, base.confidence + bonus)

    has_code = any(s.type == "code" for s in base.source_refs)
    has_doc = any(s.type == "doc" for s in base.source_refs)
    if has_code and has_doc:
        base.confidence = min(1.0, base.confidence + 0.1)
        base.evidence_summary = f"Code+Doc aligned ({n_files} sources)"

    base.evidence_summary = f"Merged from {len(group)} atoms across {n_files} files"
    return base
