"""证据对齐：跨代码/文档来源匹配同一概念的 atom。"""
from __future__ import annotations

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

        # 寻找相同 kind + 高关键词重叠的姐妹 atom
        a_keywords = _extract_keywords(a.claim)
        for j, b in enumerate(atoms):
            if j in used or j == i:
                continue
            if a.kind != b.kind:
                continue

            b_keywords = _extract_keywords(b.claim)
            overlap = a_keywords & b_keywords
            if len(overlap) >= 2:  # 至少2个共同关键词
                group.append(b)
                used.add(j)

        if len(group) == 1:
            aligned.append(a)
        else:
            # 合并 group
            merged = _merge_group(group)
            aligned.append(merged)

    return aligned


def _extract_keywords(text: str) -> set[str]:
    """从文本提取关键术语。"""
    keywords = {
        "审计", "audit", "journal", "利率", "interest", "accrual", "计提",
        "摊销", "amortization", "费用", "charge", "fee", "penalty", "罚金",
        "重试", "retry", "幂等", "idempotency", "transaction", "事务",
        "定时", "scheduled", "cron", "job", "调度",
        "loan", "贷款", "savings", "储蓄", "deposit", "存款",
        "transfer", "转账", "interop", "互操作",
        "command", "handler", "validator", "validate",
    }
    lower = text.lower()
    return {kw for kw in keywords if kw.lower() in lower}


def _merge_group(group: list[SkillAtom]) -> SkillAtom:
    """合并一组同概念原子。"""
    base = group[0]

    # 合并 source_refs
    seen_sources: set[tuple[str, str]] = set()
    for s in base.source_refs:
        seen_sources.add((s.type, s.id))
    for a in group[1:]:
        for s in a.source_refs:
            key = (s.type, s.id)
            if key not in seen_sources:
                base.source_refs.append(s)
                seen_sources.add(key)

    # 合并 checks
    seen_checks = set(base.checks)
    for a in group[1:]:
        for c in a.checks:
            if c not in seen_checks:
                base.checks.append(c)
                seen_checks.add(c)

    # 提升置信度（跨文件证据）
    n_files = len({s.id for s in base.source_refs})
    bonus = min(0.15, (n_files - 1) * 0.05)
    base.confidence = min(1.0, base.confidence + bonus)

    # 如果有代码+文档双重证据，进一步提升
    has_code = any(s.type == "code" for s in base.source_refs)
    has_doc = any(s.type == "doc" for s in base.source_refs)
    if has_code and has_doc:
        base.confidence = min(1.0, base.confidence + 0.1)
        base.evidence_summary = f"Code+Doc aligned ({n_files} sources)"

    base.evidence_summary = f"Merged from {len(group)} atoms across {n_files} files"
    return base
