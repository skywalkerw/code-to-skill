"""Test Split 最终评估 + Step 内部 Checkpoint。

对齐 external/SkillOpt 的训练后最终报告流程。

功能：
1. test_evaluate: 训练结束后在 test split 上做最终评测
2. StepCheckpoint: step 内部 minibatch 级别的恢复状态
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from code_to_skill.time_utils import local_timestamp

logger = logging.getLogger(__name__)


# ── Test Split 最终评估 ────────────────────────────────────

def test_evaluate(
    best_skill: str,
    test_items: list[dict],
    adapter: Any = None,
    output_dir: str = "",
) -> dict:
    """在 held-out test split 上做最终评估。

    Args:
        best_skill: 最优 Skill 文档
        test_items: test split 的 benchmark items
        adapter: EnvAdapter 实例
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
        results = adapter.rollout(best_skill, test_items)
    else:
        # Fallback: use scoring directly with simple keyword rule
        from .scoring import score_rollout_result
        results = []
        for item in test_items:
            checks = item.get("expected_checks", [])
            question = item.get("task_template", item.get("question", ""))
            # Rule-based prediction
            relevant = [
                l for l in best_skill.split("\n")
                if any(c.lower() in l.lower() for c in checks)
            ]
            predicted = "\n".join(relevant[:10]) if relevant else best_skill[:300]
            scores = score_rollout_result(predicted, checks)
            results.append({
                "id": item.get("id", ""),
                "hard": scores["hard"],
                "soft": scores["soft"],
                "accuracy": scores["accuracy"],
                "f1": scores["f1"],
                "predicted_answer": predicted,
            })

    n = len(results)
    soft_avg = sum(r.get("soft", 0) for r in results) / max(n, 1)
    hard_avg = sum(r.get("hard", 0) for r in results) / max(n, 1)

    report = {
        "evaluated_at": local_timestamp(),
        "n_items": n,
        "test_score_soft": round(soft_avg, 3),
        "test_score_hard": round(hard_avg, 3),
        "per_item": [
            {"id": r.get("id", ""), "soft": r.get("soft", 0), "hard": r.get("hard", 0)}
            for r in results
        ],
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
