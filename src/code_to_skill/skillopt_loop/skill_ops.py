"""高级 Skill 操作：slow-update 保护区域，per-edit 报告，智能回退。

对齐 external/SkillOpt skillopt/optimizer/skill.py
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import EditOp

logger = logging.getLogger(__name__)

SLOW_UPDATE_START = "<!-- SLOW_UPDATE_START -->"
SLOW_UPDATE_END = "<!-- SLOW_UPDATE_END -->"


def _is_in_slow_update_region(skill: str, target: str) -> bool:
    """检查 target 是否落在 slow-update 保护区域内。"""
    start_idx = skill.find(SLOW_UPDATE_START)
    end_idx = skill.find(SLOW_UPDATE_END)
    if start_idx == -1 or end_idx == -1:
        return False
    target_idx = skill.find(target)
    if target_idx == -1:
        return False
    region_end = end_idx + len(SLOW_UPDATE_END)
    return start_idx <= target_idx < region_end


def _strip_markers(text: str) -> str:
    """移除 SLOW_UPDATE 标记防止重复。"""
    return text.replace(SLOW_UPDATE_START, "").replace(SLOW_UPDATE_END, "")


def apply_edit(skill: str, edit: "EditOp") -> tuple[str, dict]:
    """应用单条编辑，返回 (更新后的 skill, 状态报告)。

    slow-update 区域内的编辑会被跳过。
    """
    op = getattr(edit, "op", "")
    content = _strip_markers(
        getattr(edit, "content", "").strip()
    )
    target = getattr(edit, "target", "")

    report = {
        "op": op,
        "target": target[:200],
        "content_preview": content[:200],
        "status": "unknown",
    }

    # slow-update 保护
    if target and _is_in_slow_update_region(skill, target):
        report["status"] = "skipped_protected"
        return skill, report

    if op == "append":
        su_start = skill.find(SLOW_UPDATE_START)
        if su_start != -1:
            before = skill[:su_start].rstrip()
            after = skill[su_start:]
            report["status"] = "applied_before_slow_update"
            return before + "\n\n" + content + "\n\n" + after, report
        report["status"] = "applied"
        return skill.rstrip() + "\n\n" + content + "\n", report

    if op == "insert_after":
        if not target or target not in skill:
            su_start = skill.find(SLOW_UPDATE_START)
            if su_start != -1:
                before = skill[:su_start].rstrip()
                after = skill[su_start:]
                report["status"] = "fallback_before_slow_update"
                return before + "\n\n" + content + "\n\n" + after, report
            report["status"] = "fallback_append"
            return skill.rstrip() + "\n\n" + content + "\n", report

        idx = skill.index(target) + len(target)
        newline = skill.find("\n", idx)
        insert_at = newline + 1 if newline != -1 else len(skill)
        report["status"] = "applied"
        return skill[:insert_at] + "\n" + content + "\n" + skill[insert_at:], report

    if op == "replace":
        if not target:
            report["status"] = "skipped_no_target"
            return skill, report
        if target not in skill:
            report["status"] = "skipped_not_found"
            return skill, report
        report["status"] = "applied"
        return skill.replace(target, content, 1), report

    if op == "delete":
        if not target:
            report["status"] = "skipped_no_target"
            return skill, report
        if target not in skill:
            report["status"] = "skipped_not_found"
            return skill, report
        report["status"] = "applied"
        return skill.replace(target, "", 1), report

    report["status"] = "skipped_unknown_op"
    return skill, report


def apply_edits(skill: str, edits: list["EditOp"]) -> tuple[str, list[dict]]:
    """应用编辑列表，返回 (更新后的 skill, per-edit 报告列表)。

    对齐 external/SkillOpt apply_patch_with_report。
    """
    reports: list[dict] = []
    for idx, edit in enumerate(edits, 1):
        try:
            skill, report = apply_edit(skill, edit)
            report["index"] = idx
            logger.debug("Edit #%d [%s] → %s", idx, report["op"], report["status"])
        except Exception as exc:
            report = {
                "index": idx,
                "op": getattr(edit, "op", "?"),
                "status": "error",
                "error": str(exc),
            }
        reports.append(report)
    return skill, reports
