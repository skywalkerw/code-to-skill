"""Edit 质量校验 — 过滤占位注释和低质量编辑。"""
from __future__ import annotations

import re

from .types import EditOp
from .skill_quality import QualityGateConfig, edit_has_leakage

MIN_CONTENT_LEN = 20

_META_PATTERNS = [
    re.compile(r"#\s*Verify", re.I),
    re.compile(r"need improvement", re.I),
    re.compile(r"^TODO\b", re.I | re.M),
    re.compile(r"tasks failed", re.I),
]

_ACTIONABLE_MARKERS = ("-", "|", "必须", "不得", "应", "需", "禁止", "检查", "输出")


def _content_already_in_skill(content: str, skill: str) -> bool:
    """判断编辑内容是否已存在于 skill（全量或逐条规则）。"""
    content = content.strip()
    if not content:
        return True
    if content in skill:
        return True
    bullets = [ln.strip() for ln in content.splitlines() if ln.strip().startswith("-")]
    if bullets and all(b in skill for b in bullets):
        return True
    return False


def validate_edit(
    edit: EditOp,
    current_skill: str,
    *,
    quality_config: QualityGateConfig | None = None,
) -> tuple[bool, str]:
    """校验单条编辑是否可应用。返回 (is_valid, reject_reason)。"""
    content = (edit.content or "").strip()
    if len(content) < MIN_CONTENT_LEN:
        return False, "too_short"
    for pat in _META_PATTERNS:
        if pat.search(content):
            return False, "meta_comment"
    if quality_config and quality_config.enabled and quality_config.reject_on_leakage:
        leaked, reason = edit_has_leakage(content, quality_config)
        if leaked:
            return False, reason
    if _content_already_in_skill(content, current_skill):
        return False, "duplicate"
    if not any(m in content for m in _ACTIONABLE_MARKERS):
        return False, "not_actionable"
    return True, ""


def filter_valid_edits(
    edits: list[EditOp],
    current_skill: str,
    *,
    quality_config: QualityGateConfig | None = None,
) -> tuple[list[EditOp], list[tuple[EditOp, str]]]:
    """过滤低质量编辑，返回 (valid_edits, rejected_with_reason)。"""
    valid: list[EditOp] = []
    rejected: list[tuple[EditOp, str]] = []
    for edit in edits:
        ok, reason = validate_edit(edit, current_skill, quality_config=quality_config)
        if ok:
            valid.append(edit)
        else:
            rejected.append((edit, reason))
    return valid, rejected
