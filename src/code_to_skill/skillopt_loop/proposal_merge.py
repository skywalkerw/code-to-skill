"""M4 self_evolution — proposal 分层合并与 EditOp 转换。"""
from __future__ import annotations

import hashlib
from typing import Any

from .reflect_helpers import SCENARIO_SECTION_HEADING, find_insert_target, PRIMARY_FOCUS
from .self_evolution_config import SelfEvolutionConfig
from .types import EditOp


def _content_fingerprint(text: str) -> str:
    norm = " ".join((text or "").split()).strip().lower()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def _last_line_in_section(skill: str, heading: str) -> str:
    idx = skill.find(heading)
    if idx < 0:
        return heading
    rest = skill[idx + len(heading):]
    last: str | None = None
    for ln in rest.splitlines():
        stripped = ln.strip()
        if stripped.startswith("#"):
            break
        if stripped.startswith("-") or stripped.startswith("|"):
            last = stripped
    return last or heading


def _prepare_rule_edit(rule: str, current_skill: str) -> tuple[str, str, str]:
    """Return (op, target, content), trimming duplicate scenario bullets."""
    if not rule.startswith(SCENARIO_SECTION_HEADING):
        return "append", "", rule

    body_lines = [
        ln.rstrip()
        for ln in rule.splitlines()[1:]
        if ln.strip()
    ]
    new_lines = [
        ln for ln in body_lines
        if not ln.strip().startswith("-") or ln.strip() not in current_skill
    ]
    if not new_lines:
        return "append", "", ""

    if SCENARIO_SECTION_HEADING in current_skill:
        return (
            "insert_after",
            _last_line_in_section(current_skill, SCENARIO_SECTION_HEADING),
            "\n".join(new_lines),
        )

    target = find_insert_target(current_skill, PRIMARY_FOCUS)
    content = "\n".join([SCENARIO_SECTION_HEADING, "", *new_lines])
    return ("insert_after" if target else "append"), target, content


def proposals_to_edits(
    proposals: list[dict],
    config: SelfEvolutionConfig,
    *,
    current_skill: str = "",
) -> list[EditOp]:
    """将 ready proposal 转为 append EditOp。"""
    ready = [p for p in proposals if p.get("status") == "ready"]
    ready.sort(key=lambda p: (-p.get("support_count", 0), -p.get("confidence", 0)))
    if config.max_merge_fan_in > 0:
        ready = ready[: config.max_merge_fan_in]

    seen: set[str] = set()
    edits: list[EditOp] = []
    new_rules = 0
    for prop in ready:
        rule = (prop.get("candidate_rule") or "").strip()
        if not rule or len(rule) < 12:
            continue
        op, target, content = _prepare_rule_edit(rule, current_skill)
        if not content:
            continue
        fp = _content_fingerprint(rule)
        if fp in seen:
            continue
        if content in current_skill:
            continue
        if config.max_new_rules_per_step and new_rules >= config.max_new_rules_per_step:
            break
        seen.add(fp)
        new_rules += 1
        edits.append(EditOp(
            op=op,  # type: ignore[arg-type]
            content=content,
            target=target,
            support_count=prop.get("support_count"),
            source_type="failure" if prop.get("source") == "failure_cluster" else "success",
            related_task_ids=[
                tid.split(":")[-1].replace("item_", "")
                for tid in (prop.get("support_trace_ids") or [])[:8]
            ],
            related_missed_checks=list(prop.get("missed_checks") or []),
        ))
    return edits


def proposals_to_patches(
    proposals: list[dict],
    config: SelfEvolutionConfig,
    *,
    current_skill: str = "",
) -> list[dict[str, Any]]:
    """将 proposals 转为 reflect 可用的 patch dict 列表。"""
    failure_props = [p for p in proposals if p.get("source") == "failure_cluster"]
    success_props = [p for p in proposals if p.get("source") == "success_cluster"]
    patches: list[dict[str, Any]] = []
    for source_type, props in (("failure", failure_props), ("success", success_props)):
        edits = proposals_to_edits(props, config, current_skill=current_skill)
        if edits:
            patches.append({
                "source_type": source_type,
                "reasoning": f"trace_cluster_merge ({len(edits)} edits from {len(props)} proposals)",
                "edits": [e.model_dump() for e in edits],
                "from_proposals": True,
            })
    return patches


def merge_proposal_patches_with_reflect(
    reflect_patches: list[dict],
    proposal_patches: list[dict],
    config: SelfEvolutionConfig,
) -> list[dict]:
    """将 proposal patches 与 reflect patches 合并（proposal 作为补充）。"""
    if not config.hierarchical_merge or not proposal_patches:
        return reflect_patches
    combined = list(reflect_patches)
    for pp in proposal_patches:
        merged = False
        for rp in combined:
            if rp.get("source_type") == pp.get("source_type"):
                existing_fps = {
                    _content_fingerprint(e.get("content", ""))
                    for e in rp.get("edits", [])
                }
                for e in pp.get("edits", []):
                    fp = _content_fingerprint(e.get("content", ""))
                    if fp not in existing_fps:
                        rp.setdefault("edits", []).append(e)
                        existing_fps.add(fp)
                merged = True
                break
        if not merged:
            combined.append(pp)
    return combined
