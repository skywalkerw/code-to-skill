"""按失败 case 生成场景化规则（generic 规则已全部 duplicate 时的兜底）。"""
from __future__ import annotations

import re

from .types import EditOp

_AMOUNT_RE = re.compile(r"^[\d.]+$")


def _is_amount_check(check: str) -> bool:
    return bool(_AMOUNT_RE.fullmatch(check.strip()))


def _scenario_rule_line(failure: dict) -> str:
    """为单条失败 rollout 生成唯一、可执行的场景规则。"""
    rid = failure.get("id") or "unknown"
    question = (failure.get("question") or failure.get("task_template") or "").strip()
    missed = [c for c in failure.get("missed_checks", []) if not _is_amount_check(c)][:6]
    checks_hint = "、".join(missed) if missed else "会计凭证、借、贷、借贷校验"
    refs = failure.get("context_refs") or failure.get("context", {}).get("refs") or []
    ref_hint = f"；代码参考 {refs[0]}" if refs else ""
    q_short = question[:48] + ("…" if len(question) > 48 else "")
    return (
        f"- **{rid}**（{q_short}）：必须输出「## 会计凭证」及借/贷分录表，"
        f"覆盖检查点：{checks_hint}{ref_hint}"
    )


def _rule_line_in_skill(line: str, skill: str) -> bool:
    stripped = line.strip()
    if stripped in skill:
        return True
    rid_match = re.search(r"\*\*([^*]+)\*\*", stripped)
    if rid_match and rid_match.group(1) in skill:
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

    heading = "### 场景分录规则（按 benchmark case）"
    section_target = "### 2.3 生成会计凭证"
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
    elif section_target in current_skill:
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
