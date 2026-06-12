"""Test Split 最终评估 + Step 内部 Checkpoint。

对齐 external/SkillOpt 的训练后最终报告流程。

功能：
1. evaluate_test_split: 训练结束后在 test split 上做最终评测
2. StepCheckpoint: step 内部 minibatch 级别的恢复状态
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from code_to_skill.time_utils import local_timestamp

logger = logging.getLogger(__name__)


# ── Test Split 最终评估 ────────────────────────────────────

def _scorer_name(item: dict) -> str:
    scorer = str(item.get("scorer") or "keyword").strip().lower()
    if scorer in ("python", "py", "script", "python_script"):
        return "python_script"
    if scorer in ("llm", "judge", "llm_judge"):
        return "llm_judge"
    return scorer or "keyword"


def _script_path(item: dict) -> str:
    scorer_config = item.get("scorer_config") or {}
    return str(
        item.get("score_script")
        or item.get("scorer_script")
        or scorer_config.get("script")
        or scorer_config.get("path")
        or ""
    )


def _find_trace_dir(output_dir: str) -> Path | None:
    if not output_dir:
        return None
    out_dir = Path(output_dir).resolve()
    for base in (out_dir, *out_dir.parents):
        candidate = base / "traces"
        if (candidate / "traces.jsonl").is_file():
            return candidate
        # Avoid walking all the way to filesystem root for normal run layouts.
        if base.name == "runs":
            break
    return None


def _load_trace_index(output_dir: str) -> tuple[dict[str, list[dict]], str]:
    """Build request_id -> trace call summaries for final eval observability."""
    trace_dir = _find_trace_dir(output_dir)
    if trace_dir is None:
        return {}, ""

    trace_path = trace_dir / "traces.jsonl"
    by_request: dict[str, list[dict]] = {}
    try:
        with open(trace_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                request = record.get("request") or {}
                response = record.get("response") or {}
                request_id = str(
                    request.get("request_id")
                    or response.get("request_id")
                    or ""
                )
                if not request_id:
                    continue
                call_file = str(record.get("call_file") or "")
                call_summary = {
                    "call_index": record.get("call_index"),
                    "call_file": call_file,
                    "call_path": str(trace_dir / "calls" / call_file) if call_file else "",
                    "created_at": record.get("created_at", ""),
                    "backend_id": record.get("backend_id", ""),
                    "role": request.get("role", ""),
                    "stage": request.get("stage", ""),
                    "status": response.get("status", ""),
                    "finish_reason": response.get("finish_reason", ""),
                    "tool_call_count": len(response.get("tool_calls") or []),
                }
                by_request.setdefault(request_id, []).append(call_summary)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[TestEval] Failed to load trace index: %s", exc)
        return {}, str(trace_dir)
    return by_request, str(trace_dir)


def _result_report_item(
    result: dict,
    item: dict,
    trace_index: dict[str, list[dict]],
) -> dict:
    expected_checks = list(
        result.get("expected_checks")
        or item.get("expected_checks")
        or []
    )
    passed_checks = list(result.get("passed_checks") or [])
    missed_checks = list(result.get("missed_checks") or [])
    predicted = str(result.get("predicted_answer") or "")
    trace_request_id = str(result.get("trace_request_id") or "")
    trace_calls = trace_index.get(trace_request_id, []) if trace_request_id else []
    return {
        "id": result.get("id") or item.get("id", ""),
        "question": result.get("question") or item.get("question", ""),
        "task_type": result.get("task_type") or item.get("task_type", ""),
        "response_mode": result.get("response_mode") or item.get("response_mode", "answer"),
        "scorer": _scorer_name(item),
        "score_type": result.get("score_type") or _scorer_name(item),
        "scorer_script": _script_path(item),
        "soft": result.get("soft", 0),
        "hard": result.get("hard", 0),
        "accuracy": result.get("accuracy", result.get("hard", 0)),
        "precision": result.get("precision", 0.0),
        "recall": result.get("recall", 0.0),
        "f1": result.get("f1", 0.0),
        "passed": result.get("passed", len(passed_checks)),
        "total": result.get("total", len(expected_checks) if expected_checks else 1),
        "expected_checks": expected_checks,
        "passed_checks": passed_checks,
        "missed_checks": missed_checks,
        "fail_reason": result.get("fail_reason", ""),
        "scorer_justification": result.get("scorer_justification", ""),
        "score_error": result.get("score_error", ""),
        **({"scorer_diagnostics": result.get("scorer_diagnostics")} if result.get("scorer_diagnostics") else {}),
        "context_refs": list(result.get("context_refs") or item.get("context_refs") or []),
        "predicted_answer": predicted,
        "predicted_preview": predicted[:500],
        "trace_request_id": trace_request_id,
        "trace_call_files": [c.get("call_file", "") for c in trace_calls if c.get("call_file")],
        "trace_calls": trace_calls,
        "trace_missing": bool(trace_request_id and not trace_calls),
        "response_status": result.get("response_status", ""),
        "finish_reason": result.get("finish_reason", ""),
        "backend_id": result.get("backend_id", ""),
    }


def _refresh_report_trace_links(report: dict, output_dir: str) -> dict:
    """Refresh trace fields in an already-built eval report.

    Selection reports are written during training, while the run-level trace
    file may still be growing. Refreshing at the end avoids false
    trace_missing rows without rerunning rollout or scoring.
    """
    trace_index, trace_dir = _load_trace_index(output_dir)
    per_item = report.get("per_item")
    if not isinstance(per_item, list):
        return report

    for row in per_item:
        if not isinstance(row, dict):
            continue
        trace_request_id = str(row.get("trace_request_id") or "")
        trace_calls = trace_index.get(trace_request_id, []) if trace_request_id else []
        row["trace_call_files"] = [
            c.get("call_file", "") for c in trace_calls if c.get("call_file")
        ]
        row["trace_calls"] = trace_calls
        row["trace_missing"] = bool(trace_request_id and not trace_calls)
    report["trace_dir"] = trace_dir
    report["summary"] = _report_summary([r for r in per_item if isinstance(r, dict)])
    return report


def _report_summary(per_item: list[dict]) -> dict:
    scorer_counts: dict[str, int] = {}
    failed_ids: list[str] = []
    missing_trace_ids: list[str] = []
    for row in per_item:
        scorer = str(row.get("scorer") or "keyword")
        scorer_counts[scorer] = scorer_counts.get(scorer, 0) + 1
        if int(row.get("hard") or 0) == 0:
            failed_ids.append(str(row.get("id") or ""))
        if row.get("trace_missing"):
            missing_trace_ids.append(str(row.get("id") or ""))
    return {
        "hard_passed": len(per_item) - len(failed_ids),
        "hard_failed": len(failed_ids),
        "failed_ids": failed_ids,
        "scorer_counts": scorer_counts,
        "trace_missing_ids": missing_trace_ids,
    }


def evaluate_test_split(
    best_skill: str,
    test_items: list[dict],
    adapter: Any = None,
    target_backend: Any = None,
    output_dir: str = "",
) -> dict:
    """在 held-out test split 上做最终评估。

    Args:
        best_skill: 最优 Skill 文档
        test_items: test split 的 benchmark items
        adapter: EnvAdapter 实例
        target_backend: rollout 后端（与主训练 target 一致）
        output_dir: 产物输出目录

    Returns:
        {
            "test_score": float,
            "test_hard": float,
            "n_items": int,
            "report_path": str,
        }
    """
    if not test_items:
        logger.info("[TestEval] No test items, skipping")
        return {"test_score": 0.0, "test_hard": 0.0, "n_items": 0, "report_path": ""}

    logger.info("[TestEval] Evaluating best skill on %d test items", len(test_items))

    if adapter:
        results = adapter.rollout(
            best_skill, test_items, target_backend=target_backend,
        )
    else:
        # Fallback 也走统一 scorer 路由，避免绕过 python_script / llm_judge。
        from .scoring import score_benchmark_item
        results = []
        for item in test_items:
            checks = item.get("expected_checks", [])
            question = item.get("question", "")
            # Rule-based prediction
            relevant = [
                l for l in best_skill.split("\n")
                if any(c.lower() in l.lower() for c in checks)
            ]
            predicted = "\n".join(relevant[:10]) if relevant else best_skill[:300]
            scores = score_benchmark_item(predicted, item)
            missed = scores.get("missed_checks", [])
            results.append({
                "id": item.get("id", ""),
                "question": question,
                "task_type": item.get("task_type", ""),
                "response_mode": item.get("response_mode", "answer"),
                "context_refs": list(item.get("context_refs") or []),
                "expected_checks": checks,
                "hard": scores["hard"],
                "soft": scores["soft"],
                "accuracy": scores.get("accuracy", float(scores["hard"])),
                "precision": scores.get("precision", 0.0),
                "recall": scores.get("recall", 0.0),
                "f1": scores.get("f1", 0.0),
                "passed": scores.get("passed", len(scores.get("passed_checks", []))),
                "total": scores.get("total", len(checks) if checks else 1),
                "passed_checks": scores.get("passed_checks", []),
                "missed_checks": missed,
                "predicted_answer": predicted,
                "fail_reason": "missed: " + ", ".join(missed) if scores["hard"] == 0 and missed else "",
                "score_type": scores.get("score_type", item.get("scorer", "keyword")),
                "scorer_justification": scores.get("justification", ""),
                "score_error": scores.get("error", ""),
            })

    n = len(results)
    soft_avg = sum(r.get("soft", 0) for r in results) / max(n, 1)
    hard_avg = sum(r.get("hard", 0) for r in results) / max(n, 1)
    item_by_id = {str(item.get("id") or ""): item for item in test_items}
    trace_index, trace_dir = _load_trace_index(output_dir)
    per_item = [
        _result_report_item(
            r,
            item_by_id.get(str(r.get("id") or ""), {}),
            trace_index,
        )
        for r in results
    ]

    report = {
        "schema_version": "1.1",
        "evaluated_at": local_timestamp(),
        "n_items": n,
        "n_expected_items": len(test_items),
        "test_score_soft": round(soft_avg, 3),
        "test_score_hard": round(hard_avg, 3),
        "trace_dir": trace_dir,
        "summary": _report_summary(per_item),
        "per_item": per_item,
    }

    report_path = ""
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = str(out_dir / "test_eval_report.json")
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info("[TestEval] Report saved: %s", report_path)

    return {
        "test_score": round(soft_avg, 3),
        "test_hard": round(hard_avg, 3),
        "n_items": n,
        "report_path": report_path,
    }


def build_selection_eval_report(
    results: list[dict],
    items: list[dict],
    *,
    step: int,
    skill_hash: str = "",
    gate_score: float = 0.0,
    hard: float = 0.0,
    soft: float = 0.0,
    output_dir: str = "",
) -> dict:
    """Build per-step selection eval report (same schema as test eval per_item)."""
    item_by_id = {str(item.get("id") or ""): item for item in items}
    trace_index, trace_dir = _load_trace_index(output_dir) if output_dir else ({}, "")
    per_item = [
        _result_report_item(
            r,
            item_by_id.get(str(r.get("id") or ""), {}),
            trace_index,
        )
        for r in results
    ]
    return {
        "schema_version": "1.0",
        "step": step,
        "skill_hash": skill_hash,
        "hard": round(hard, 3),
        "soft": round(soft, 3),
        "gate_score": round(gate_score, 3),
        "trace_dir": trace_dir,
        "summary": _report_summary(per_item),
        "per_item": per_item,
    }


def write_selection_eval_report(
    output_dir: str,
    step: int,
    report: dict,
) -> str:
    step_dir = Path(output_dir) / "steps" / f"step_{step:04d}"
    step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / "selection_eval_report.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return str(path)


def refresh_selection_eval_trace_links(output_dir: str) -> dict:
    """Refresh all step selection reports with the final run trace index."""
    steps_dir = Path(output_dir) / "steps"
    summary = {
        "reports": 0,
        "trace_missing_before": 0,
        "trace_missing_after": 0,
    }
    if not steps_dir.is_dir():
        return summary

    for path in sorted(steps_dir.glob("step_*/selection_eval_report.json")):
        try:
            with open(path, encoding="utf-8") as f:
                report = json.load(f)
            if not isinstance(report, dict):
                continue
            before = (report.get("summary") or {}).get("trace_missing_ids") or []
            summary["trace_missing_before"] += len(before)
            refreshed = _refresh_report_trace_links(report, output_dir)
            after = (refreshed.get("summary") or {}).get("trace_missing_ids") or []
            summary["trace_missing_after"] += len(after)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(refreshed, f, indent=2, ensure_ascii=False)
            summary["reports"] += 1
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[TestEval] Failed to refresh selection report %s: %s", path, exc)
    return summary


# ── Step 内部 Checkpoint ────────────────────────────────────

class StepCheckpoint:
    """Step 内部 minibatch 级别检查点。

    论文的 step-internal checkpoint：当 batch_size=40 且 minibatch=8 时，
    每个 minibatch 完成后写入检查点，恢复时可跳过已完成的 minibatch。

    Attributes:
        step: 当前 step 编号
        phase: 当前阶段 (rollout / reflect / aggregate / select / update / evaluate)
        rollout_completed: rollout 已完成的任务数
        rollout_total: rollout 批次任务总数
        last_minibatch_completed: 最后完成的 minibatch 编号
    """

    def __init__(
        self,
        step: int = 0,
        phase: str = "",
        rollout_completed: int = 0,
        rollout_total: int = 0,
        last_minibatch_completed: int = 0,
    ):
        self.step = step
        self.phase = phase
        self.rollout_completed = rollout_completed
        self.rollout_total = rollout_total
        self.last_minibatch_completed = last_minibatch_completed

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "phase": self.phase,
            "rollout_completed": self.rollout_completed,
            "rollout_total": self.rollout_total,
            "last_minibatch_completed": self.last_minibatch_completed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StepCheckpoint":
        return cls(
            step=data.get("step", 0),
            phase=data.get("phase", ""),
            rollout_completed=data.get("rollout_completed", 0),
            rollout_total=data.get("rollout_total", 0),
            last_minibatch_completed=data.get("last_minibatch_completed", 0),
        )

    def save(self, path: str) -> None:
        """保存检查点到磁盘。"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.debug("[Checkpoint] Saved step=%d phase=%s rollout=%d/%d",
                      self.step, self.phase, self.rollout_completed, self.rollout_total)

    @classmethod
    def load(cls, path: str) -> "StepCheckpoint | None":
        """从磁盘加载检查点（不存在时返回 None）。"""
        p = Path(path)
        if not p.exists():
            return None
        try:
            with open(p) as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception as e:
            logger.warning("Failed to load checkpoint: %s", e)
            return None

    @property
    def is_rollout_incomplete(self) -> bool:
        """rollout 阶段是否未完成。"""
        return self.phase == "rollout" and self.rollout_completed < self.rollout_total

    @property
    def is_reflect_incomplete(self) -> bool:
        """reflect 阶段是否未完成。"""
        return self.phase == "reflect" and self.last_minibatch_completed > 0
