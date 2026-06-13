"""分层资格制置信度评分。

三层：资格门槛 → Tier 分档 → LLM 调整 → 角色感知过滤（设计 09 §9）。
"""
from __future__ import annotations

import os
import re
from pathlib import PurePath

from .types import SkillAtom, RawAtom

# 设计 09 §7.3：业务角色 vs glue code 启发式
_BUSINESS_ROLES = frozenset({
    "processor", "service", "domain", "dto", "enum", "helper", "util",
    "validator", "mapper", "event", "listener", "hook",
})
_GLUE_ROLES = frozenset({
    "handler_only", "swagger", "configuration", "starter",
    "controller", "resource_api", "api_resource",
    "rest_controller", "repository", "config",
})
_CLASS_ROLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Processor(?:Impl)?$", re.I), "processor"),
    (re.compile(r"Service(?:Impl)?$", re.I), "service"),
    (re.compile(r"Handler$", re.I), "handler_only"),
    (re.compile(r"CommandHandler$", re.I), "handler_only"),
    (re.compile(r"ApiResource(?:Swagger)?$", re.I), "swagger"),
    (re.compile(r"Config(?:uration)?$", re.I), "configuration"),
    (re.compile(r"DTO$", re.I), "dto"),
    (re.compile(r"Enum$", re.I), "enum"),
    (re.compile(r"Constants?$", re.I), "enum"),
    (re.compile(r"Domain(?:Service)?$", re.I), "domain"),
    (re.compile(r"Validator$", re.I), "validator"),
    (re.compile(r"Mapper$", re.I), "mapper"),
    (re.compile(r"Starter$", re.I), "starter"),
]


def _classify_source_ref_role(ref_id: str) -> str:
    """从 source_ref id（路径#符号）推断代码角色。"""
    if not ref_id:
        return "unknown"
    path = ref_id.rsplit("#", 1)[0] if "#" in ref_id else ref_id
    p = PurePath(path)
    name = p.stem
    for pattern, role in _CLASS_ROLE_PATTERNS:
        if pattern.search(name):
            return role
    for part in p.parts:
        low = part.lower()
        if low in ("processor", "service", "domain", "dto", "handler",
                    "controller", "config", "configuration", "resource"):
            return low if low not in ("handler", "controller", "config",
                                      "configuration", "resource") else {
                "handler": "handler_only", "controller": "handler_only",
                "config": "configuration", "configuration": "configuration",
                "resource": "resource_api",
            }.get(low, "unknown")
    return "unknown"


def _apply_role_aware_filter(
    atom: SkillAtom,
    accepted_roles: list[str] | None,
    downrank: bool,
) -> tuple[float, str | None]:
    """根据 source_ref 角色调整置信度并返回新状态建议（设计 09 §9）。

    Returns (confidence_adjustment, override_status_or_None).
    """
    if not downrank or not atom.source_refs:
        return 0.0, None
    accepted = set(accepted_roles or [])
    roles: list[str] = []
    for ref in atom.source_refs:
        if ref.type == "code":
            roles.append(_classify_source_ref_role(ref.id))
    if not roles:
        return 0.0, None
    glue_count = sum(1 for r in roles if r in _GLUE_ROLES)
    biz_count = sum(1 for r in roles if r in _BUSINESS_ROLES)
    total = len(roles)
    # 全部是 glue code → 大幅降权
    if glue_count == total:
        return -0.30, "rejected"
    # 多数是 glue code → 降为 needs_review
    if glue_count > biz_count and glue_count >= total * 0.6:
        return -0.15, "needs_review"
    # 至少有一个业务角色 → 微小加分
    if biz_count > 0 and glue_count == 0:
        return 0.05, None
    return 0.0, None


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
    """Recompute statuses after merge/alignment confidence changes.

    如果 ``settings.code_first.downrank_handlers`` 为 true，对 glue-code 原子降权
    （设计 09 §9）。
    """
    code_first = (settings or {}).get("code_first") or {}
    downrank = bool(code_first.get("enabled")) or bool(code_first.get("downrank_handlers"))
    accepted_roles: list[str] | None = code_first.get("accepted_roles")

    refreshed: list[SkillAtom] = []
    for atom in atoms:
        confidence = atom.confidence

        # 设计 09 角色过滤
        if downrank:
            adjust, override_status = _apply_role_aware_filter(
                atom, accepted_roles=accepted_roles, downrank=downrank,
            )
            confidence = max(0.0, min(0.95, confidence + adjust))
            if override_status:
                refreshed.append(atom.model_copy(
                    update={"confidence": round(confidence, 2), "status": override_status},
                ))
                continue

        status = status_for_confidence(confidence, settings)
        if status == atom.status and confidence == atom.confidence:
            refreshed.append(atom)
        else:
            refreshed.append(atom.model_copy(
                update={"confidence": round(confidence, 2), "status": status},
            ))
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

    # 设计 09 角色感知过滤
    code_first = (settings or {}).get("code_first") or {}
    downrank = bool(code_first.get("enabled")) or bool(code_first.get("downrank_handlers"))
    accepted_roles: list[str] | None = code_first.get("accepted_roles")

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

        # 设计 09 角色过滤（第四层）
        if downrank:
            role_adj, override_status = _apply_role_aware_filter(
                atom, accepted_roles=accepted_roles, downrank=downrank,
            )
            atom.confidence = max(0.0, min(tier_1_max, atom.confidence + role_adj))
            if override_status:
                atom.status = override_status
                scored.append(atom)
                continue

        atom.status = status_for_confidence(atom.confidence, settings)
        scored.append(atom)

    return scored
