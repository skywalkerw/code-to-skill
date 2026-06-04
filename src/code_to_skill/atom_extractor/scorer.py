"""分层资格制置信度评分。

三层：资格门槛 → Tier 分档 → LLM 调整（当前仅前两层）
"""
from __future__ import annotations

from .types import SkillAtom, RawAtom


def score_atoms(raw_atoms: list[RawAtom]) -> list[SkillAtom]:
    """对候选 atom 进行三层评分，返回通过门槛的 atom。

    第一层：资格门槛
    第二层：证据质量分层（Tier 1-5）
    第三层：LLM 细化（预留）
    """
    scored: list[SkillAtom] = []
    for raw in raw_atoms:
        atom = raw.atom

        # 第一层：资格门槛
        if not atom.source_refs:
            atom.status = "rejected"
            atom.confidence = 0.3
            scored.append(atom)
            continue
        if not atom.claim.strip():
            atom.status = "rejected"
            atom.confidence = 0.3
            scored.append(atom)
            continue

        # 第二层：Tier 分档
        has_code = any(s.type == "code" for s in atom.source_refs)
        has_doc = any(s.type == "doc" for s in atom.source_refs)
        has_high_auth = any(s.authority in ("official_doc", "official_spec") for s in atom.source_refs)

        if has_code and has_doc and has_high_auth:
            tier_base = 0.85  # Tier 1
        elif has_code and has_doc:
            tier_base = 0.75  # Tier 2
        elif has_code:
            tier_base = 0.65  # Tier 3
        elif has_doc:
            tier_base = 0.55  # Tier 4
        else:
            tier_base = 0.45  # Tier 5

        # 简单基于规则的调整
        adjustment = 0.0
        if atom.checks:
            adjustment += 0.05
        if atom.negative_rule:
            adjustment += 0.03
        if atom.action:
            adjustment += 0.02

        atom.confidence = min(1.0, max(0.0, tier_base + adjustment))

        # 状态判定
        if atom.confidence >= 0.80:
            atom.status = "accepted"
        elif atom.confidence >= 0.60:
            atom.status = "candidate"
        elif atom.confidence >= 0.40:
            atom.status = "needs_review"
        else:
            atom.status = "rejected"

        scored.append(atom)

    return scored
