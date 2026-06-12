"""按失败 case 生成场景化规则（generic 规则已全部 duplicate 时的兜底）。"""
from __future__ import annotations

import re

from .reflect_helpers import (
    SCENARIO_SECTION_HEADING,
    find_insert_target,
    is_numeric_check,
    PRIMARY_FOCUS,
)
from .types import EditOp

_AMOUNT_RE = re.compile(r"^[\d.,]+$")


def _is_amount_check(check: str) -> bool:
    return bool(_AMOUNT_RE.fullmatch(check.strip())) or is_numeric_check(check)


def _business_outcome(missed: list[str]) -> str:
    """将 missed checks 转为业务输出要求，不写 checks/scorer 语汇。"""
    reqs = [c for c in missed if not _is_amount_check(c)]
    if not reqs:
        return "输出应覆盖该场景所需的完整业务字段，金额只取用户输入"
    joined = "、".join(reqs[:6])
    return f"输出须明确体现：{joined}；金额只取用户输入"


def _trigger_from_question(question: str) -> str:
    """从 question 提取业务触发条件，不逐字复述 benchmark id。"""
    q = (question or "").strip()
    if not q:
        return "当用户描述同类业务场景"
    q_short = q[:72] + ("…" if len(q) > 72 else "")
    return f"当用户描述「{q_short}」时"


def _scenario_rule_line(failure: dict) -> str:
    """为单条失败 rollout 生成业务触发条件规则（不含 benchmark id / scorer 语汇）。"""
    question = (failure.get("question") or "").strip()
    missed = [c for c in failure.get("missed_checks", []) if not _is_amount_check(c)][:6]
    refs = failure.get("context_refs") or failure.get("context", {}).get("refs") or []
    ref_hint = f"；可参考 {refs[0]}" if refs else ""
    hint = str(failure.get("reflect_hint") or failure.get("rollout_hint") or "").strip()
    hint_suffix = f"；{hint}" if hint else ""
    trigger = _trigger_from_question(question)
    outcome = _business_outcome(missed)
    return f"- {trigger}，{outcome}{ref_hint}{hint_suffix}"


def _rule_line_in_skill(line: str, skill: str) -> bool:
    stripped = line.strip()
    if stripped in skill:
        return True
    if stripped.startswith("- 当用户描述"):
        prefix = stripped[: min(len(stripped), 48)]
        if prefix and prefix in skill:
            return True
    return stripped.lstrip("- ").strip() in skill


def _anchor_in_section(skill: str, heading: str) -> str:
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


def build_scenario_edits(
    rollout_results: list[dict],
    current_skill: str,
    *,
    max_cases: int = 5,
) -> list[EditOp]:
    """从失败 case 生成场景化 EditOp（按 missed 数量降序，跳过已在 skill 中的条目）。"""
    failed = [r for r in rollout_results if r.get("hard", 0) == 0]
    failed.sort(key=lambda r: -len(r.get("missed_checks", [])))

    heading = SCENARIO_SECTION_HEADING
    section_target = find_insert_target(current_skill, PRIMARY_FOCUS)
    lines: list[str] = []
    task_ids: list[str] = []
    missed_all: list[str] = []

    for r in failed[:max_cases]:
        line = _scenario_rule_line(r)
        if _rule_line_in_skill(line, current_skill) or line in lines:
            continue
        lines.append(line)
        if r.get("id"):
            task_ids.append(r["id"])
        missed_all.extend(
            c for c in r.get("missed_checks", []) if not _is_amount_check(c)
        )

    if not lines:
        return []

    body = "\n".join(lines)
    if heading in current_skill:
        anchor = _anchor_in_section(current_skill, heading)
        content = body
        target = anchor
    elif section_target:
        content = f"{heading}\n\n{body}"
        target = section_target
    else:
        content = f"{heading}\n\n{body}"
        target = ""

    return [
        EditOp(
            op="insert_after" if target else "append",
            target=target,
            content=content,
            source_type="failure",
            related_task_ids=sorted(set(task_ids)),
            related_missed_checks=sorted(set(missed_all)),
        )
    ]
