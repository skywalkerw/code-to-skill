"""Edit 与 rollout 失败 case 的追溯辅助。"""
from __future__ import annotations

from typing import Any

from .types import EditOp, RankedEdit


def missed_check_summary(failed: list[dict]) -> list[dict]:
    """聚合 failed rollout 的 missed_checks 计数。"""
    counts: dict[str, int] = {}
    for r in failed:
        for check in r.get("missed_checks", []):
            counts[check] = counts.get(check, 0) + 1
    return [
        {"type": check, "count": count}
        for check, count in sorted(counts.items(), key=lambda x: -x[1])
    ]


def rollout_failure_records(results: list[dict]) -> list[dict]:
    """Step artifact 用的精简失败 case 列表。"""
    records: list[dict] = []
    for r in results:
        if r.get("hard", 0) != 0:
            continue
        records.append({
            "id": r.get("id", ""),
            "task_type": r.get("task_type", ""),
            "question": (r.get("question") or "")[:300],
            "context_refs": list(r.get("context_refs") or []),
            "missed_checks": list(r.get("missed_checks", [])),
            "passed_checks": list(r.get("passed_checks", [])),
            "fail_reason": r.get("fail_reason", ""),
            "predicted_excerpt": (r.get("predicted_answer") or "")[:400],
        })
    return records


def annotate_rule_edit(
    edit: dict,
    *,
    task_ids: list[str],
    missed_checks: list[str],
) -> dict:
    edit["related_task_ids"] = sorted({tid for tid in task_ids if tid})
    edit["related_missed_checks"] = sorted({c for c in missed_checks if c})
    return edit


def infer_edit_traceability(edit: dict, failed_results: list[dict]) -> dict:
    """根据 edit content 与 failed rollout 推断关联 task / missed checks。"""
    from .reflect_helpers import semantic_rule_for_check

    content = (edit.get("content") or "").lower()
    task_ids: set[str] = set()
    missed: set[str] = set()

    for r in failed_results:
        rid = r.get("id")
        r_missed = r.get("missed_checks", [])
        matched = False
        for check in r_missed:
            check_l = check.lower()
            rule = semantic_rule_for_check(check)
            if (
                check_l in content
                or rule.lower() in content
                or any(tok in content for tok in check_l.split() if len(tok) > 1)
            ):
                missed.add(check)
                matched = True
        if matched and rid:
            task_ids.add(rid)

    edit["related_task_ids"] = sorted(task_ids)
    edit["related_missed_checks"] = sorted(missed)
    return edit


def merge_edit_traceability(target: EditOp, source: EditOp) -> None:
    """去重合并时保留两侧追溯信息。"""
    target.related_task_ids = sorted(
        set(target.related_task_ids) | set(source.related_task_ids)
    )
    target.related_missed_checks = sorted(
        set(target.related_missed_checks) | set(source.related_missed_checks)
    )


def edit_to_dict(edit: EditOp) -> dict[str, Any]:
    return {
        "op": edit.op,
        "content": edit.content,
        "target": edit.target,
        "source_type": edit.source_type,
        "related_task_ids": list(edit.related_task_ids),
        "related_missed_checks": list(edit.related_missed_checks),
    }


def ranked_edit_to_proposal(ranked: RankedEdit) -> dict[str, Any]:
    record = edit_to_dict(ranked.edit)
    record["rank"] = ranked.rank
    record["score"] = ranked.score
    record["support_count"] = ranked.support_count
    return record
