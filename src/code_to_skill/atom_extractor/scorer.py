"""分层资格制置信度评分。

三层：资格门槛 → Tier 分档 → LLM 调整（当前仅前两层）
"""
from __future__ import annotations

from .types import SkillAtom, RawAtom


def _settings_float(settings: dict | None, key: str, default: float) -> float:
    if not settings:
        return default
    try:
        return float(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def status_for_confidence(confidence: float, settings: dict | None = None) -> str:
    """Map final confidence to atom status using configured thresholds."""
    accepted_min = _settings_float(settings, "accepted_min", 0.80)
    candidate_min = _settings_float(settings, "candidate_min", 0.60)
    needs_review_min = _settings_float(settings, "needs_review_min", 0.40)

    if confidence >= accepted_min:
        return "accepted"
    if confidence >= candidate_min:
        return "candidate"
    if confidence >= needs_review_min:
        return "needs_review"
    return "rejected"


def refresh_atom_statuses(
    atoms: list[SkillAtom],
    settings: dict | None = None,
) -> list[SkillAtom]:
    """Recompute statuses after merge/alignment confidence changes."""
    refreshed: list[SkillAtom] = []
    for atom in atoms:
        status = status_for_confidence(atom.confidence, settings)
        if status == atom.status:
            refreshed.append(atom)
        else:
            refreshed.append(atom.model_copy(update={"status": status}))
    return refreshed


def score_atoms(
    raw_atoms: list[RawAtom],
    settings: dict | None = None,
) -> list[SkillAtom]:
    """对候选 atom 进行三层评分，返回通过门槛的 atom。

    第一层：资格门槛
    第二层：证据质量分层（Tier 1-5）
    第三层：LLM 细化（预留；``llm_adjustment`` 来自 settings.atom_extractor）

    ``settings`` 键（可选，来自 ``config.settings.atom_extractor``）：
    - ``confidence_tier_1_max``: Tier 1 置信度上限
    - ``llm_adjustment``: 有 checks/action 时的额外加分
    - ``accepted_min`` / ``candidate_min`` / ``needs_review_min``: 状态分档阈值
    """
    tier_1_max = _settings_float(settings, "confidence_tier_1_max", 0.95)
    llm_adjustment = _settings_float(settings, "llm_adjustment", 0.05)
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

        adjustment = 0.0
        if atom.checks:
            adjustment += llm_adjustment
        if atom.negative_rule:
            adjustment += llm_adjustment * 0.6
        if atom.action:
            adjustment += llm_adjustment * 0.4

        atom.confidence = min(tier_1_max, max(0.0, tier_base + adjustment))

        atom.status = status_for_confidence(atom.confidence, settings)

        scored.append(atom)

    return scored
