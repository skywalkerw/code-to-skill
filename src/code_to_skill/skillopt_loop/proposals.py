"""Design 08 — 从 trace cluster 生成 success/failure proposals。"""
from __future__ import annotations

import json
import os
from typing import Any

from .self_evolution_config import SelfEvolutionConfig


def _failure_candidate_rule(cluster: dict) -> str:
    task_type = cluster.get("task_type") or "general"
    missed = cluster.get("missed_checks") or []
    if missed:
        checks = ", ".join(missed[:6])
        return (
            f"When handling {task_type} tasks, ensure the answer satisfies: {checks}."
        )
    return f"Review {task_type} handling against benchmark expected checks."


def _success_candidate_rule(cluster: dict) -> str:
    task_type = cluster.get("task_type") or "general"
    passed = cluster.get("passed_checks") or []
    if passed:
        return f"For {task_type}, preserve patterns that satisfy: {', '.join(passed[:4])}."
    return f"Retain effective {task_type} guidance validated by recent rollouts."


def build_failure_proposals(
    clusters: list[dict],
    config: SelfEvolutionConfig,
    *,
    step: int,
    evidence_refs: list[str] | None = None,
) -> list[dict]:
    proposals: list[dict] = []
    for cluster in clusters:
        support = cluster.get("support_count", 0)
        status = "ready" if support >= config.min_support_count else "needs_review"
        prop_id = f"prop-step{step:04d}-{cluster['cluster_id']}"
        proposals.append({
            "proposal_id": prop_id,
            "source": "failure_cluster",
            "cluster_id": cluster["cluster_id"],
            "support_trace_ids": list(cluster.get("trace_ids") or []),
            "support_count": support,
            "missed_checks": list(cluster.get("missed_checks") or []),
            "evidence_refs": list(evidence_refs or []),
            "root_cause": f"Cluster missed checks: {', '.join(cluster.get('missed_checks') or [])[:120]}",
            "edit_intent": "add_rule",
            "candidate_rule": _failure_candidate_rule(cluster),
            "risk": "medium" if support >= config.min_support_count else "high",
            "confidence": min(0.95, 0.5 + 0.1 * support),
            "status": status,
            "step": step,
        })
    return proposals


def _trace_id_for(record: dict) -> str | None:
    trace_id = (record.get("trace_id") or "").strip()
    return trace_id or None


def build_success_proposals(
    traces: list[dict],
    config: SelfEvolutionConfig,
    *,
    step: int,
) -> list[dict]:
    if not config.include_success:
        return []
    by_task: dict[str, list[dict]] = {}
    for t in traces:
        if t.get("hard", 0) != 1:
            continue
        task = t.get("task_type") or "general"
        by_task.setdefault(task, []).append(t)

    proposals: list[dict] = []
    for task_type, members in sorted(by_task.items(), key=lambda x: -len(x[1])):
        if len(members) < config.min_support_count:
            continue
        cluster_id = f"success-{task_type}"
        prop_id = f"prop-step{step:04d}-{cluster_id}"
        passed = sorted({c for m in members for c in (m.get("passed_checks") or [])})
        proposals.append({
            "proposal_id": prop_id,
            "source": "success_cluster",
            "cluster_id": cluster_id,
            "support_trace_ids": [
                tid for m in members if (tid := _trace_id_for(m))
            ],
            "support_count": len(members),
            "missed_checks": [],
            "passed_checks": passed,
            "evidence_refs": [],
            "root_cause": f"Consistent success on {task_type} ({len(members)} traces).",
            "edit_intent": "reinforce_rule",
            "candidate_rule": _success_candidate_rule({"task_type": task_type, "passed_checks": passed}),
            "risk": "low",
            "confidence": min(0.9, 0.4 + 0.08 * len(members)),
            "status": "ready",
            "step": step,
        })
    return proposals


def write_proposals(
    output_dir: str,
    *,
    failure_proposals: list[dict],
    success_proposals: list[dict],
    merged_proposals: list[dict] | None = None,
    step: int | None = None,
) -> dict[str, str]:
    """写入 proposals；``step`` 非空时额外落盘到 ``proposals/steps/step_NNNN/``。"""
    prop_dir = os.path.join(output_dir, "proposals")
    os.makedirs(prop_dir, exist_ok=True)
    step_dir = (
        os.path.join(prop_dir, "steps", f"step_{step:04d}")
        if step is not None
        else prop_dir
    )
    os.makedirs(step_dir, exist_ok=True)
    paths: dict[str, str] = {}
    merged = merged_proposals if merged_proposals is not None else (
        failure_proposals + success_proposals
    )

    def _write_jsonl(target_dir: str, name: str, rows: list[dict]) -> str:
        path = os.path.join(target_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        paths[name] = path
        return path

    quality = {
        "step": step,
        "failure_count": len(failure_proposals),
        "success_count": len(success_proposals),
        "ready_count": sum(1 for p in merged if p.get("status") == "ready"),
        "needs_review_count": sum(1 for p in merged if p.get("status") == "needs_review"),
        "avg_support_count": (
            sum(p.get("support_count", 0) for p in merged) / len(merged) if merged else 0
        ),
    }

    for target in ({step_dir} if step is not None else {prop_dir}) | {prop_dir}:
        _write_jsonl(target, "failure_proposals.jsonl", failure_proposals)
        _write_jsonl(target, "success_proposals.jsonl", success_proposals)
        _write_jsonl(target, "merged_proposals.jsonl", merged)
        qpath = os.path.join(target, "proposal_quality.json")
        with open(qpath, "w", encoding="utf-8") as f:
            json.dump(quality, f, indent=2, ensure_ascii=False)
        paths[f"proposal_quality.json@{target}"] = qpath

    if step is not None:
        index_path = os.path.join(prop_dir, "steps_index.jsonl")
        with open(index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "step": step,
                "dir": f"steps/step_{step:04d}",
                "failure_count": len(failure_proposals),
                "success_count": len(success_proposals),
                "ready_count": quality["ready_count"],
            }, ensure_ascii=False) + "\n")
        paths["steps_index.jsonl"] = index_path
        paths["step_dir"] = step_dir

    return paths


def generate_step_proposals(
    clusters: list[dict],
    step_traces: list[dict],
    config: SelfEvolutionConfig,
    *,
    step: int,
    evidence_refs: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    failure_props: list[dict] = []
    if config.include_failure:
        failure_props = build_failure_proposals(
            clusters, config, step=step, evidence_refs=evidence_refs,
        )
    success_props: list[dict] = []
    if config.include_success:
        success_props = build_success_proposals(step_traces, config, step=step)
    return failure_props, success_props
