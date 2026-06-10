"""评估门禁模块。

对齐 external/SkillOpt skillopt/evaluation/gate.py
支持三种度量: hard / soft / mixed
"""
from __future__ import annotations

from typing import Literal

GateAction = Literal["accept_new_best", "accept", "reject"]
GateMetric = Literal["hard", "soft", "mixed"]


def select_gate_score(
    hard: float,
    soft: float,
    metric: GateMetric = "soft",
    mixed_weight: float = 0.5,
) -> float:
    """将 (hard, soft) 投影到单一比较分数。

    对齐 external/SkillOpt select_gate_score。
    """
    if metric == "hard":
        return float(hard)
    if metric == "soft":
        return float(soft)
    if metric == "mixed":
        w = max(0.0, min(1.0, float(mixed_weight)))
        return (1.0 - w) * float(hard) + w * float(soft)
    return float(soft)


class GateDecision:
    """门禁决策结果。"""

    def __init__(
        self,
        action: GateAction,
        candidate_score: float,
        best_score: float,
        current_score: float,
        reason: str = "",
    ):
        self.action = action
        self.candidate_score = candidate_score
        self.best_score = best_score
        self.current_score = current_score
        self.reason = reason

    def __repr__(self) -> str:
        return f"Gate({self.action}, score={self.candidate_score:.3f}, reason={self.reason})"


class GateManager:
    """门禁管理器：阈值 + patience 双重控制。

    对齐 external/SkillOpt select_gate_score + gate 逻辑。

    行为：
    - candidate_score > best_score → accept_new_best
    - candidate_score > current_score → accept
    - 连续 reject 超过 patience → 触发早停信号

    metric="hard" 按论文默认：hard pass rate 严格门控。
    小 selection 集（< 20 条）hard 会自动降级为 mixed。
    """

    def __init__(
        self,
        patience: int = 10,
        delta: float = 0.01,
        metric: GateMetric = "hard",
        mixed_weight: float = 0.5,
        *,
        strict_improvement: bool = False,
        reject_ties: bool = False,
    ):
        self.patience = patience
        self.delta = delta
        self.metric: GateMetric = metric
        self.mixed_weight = mixed_weight
        self.strict_improvement = strict_improvement
        self.reject_ties = reject_ties
        self._consecutive_rejects = 0
        self._total_accepts = 0
        self._total_rejects = 0

    def evaluate(
        self,
        candidate_hard: float,
        candidate_soft: float,
        best_score: float,
        current_score: float,
        *,
        train_rollout: float | None = None,
        prev_train_rollout: float | None = None,
        train_delta: float = 0.03,
    ) -> GateDecision:
        """评估候选分数是否通过门禁。

        Uses select_gate_score to project (hard, soft) to a scalar
        according to self.metric before comparison.

        当 selection 分数持平但 train rollout 显著提升时，仍 accept 以推进 current_skill。
        """
        candidate = select_gate_score(
            candidate_hard, candidate_soft,
            metric=self.metric, mixed_weight=self.mixed_weight,
        )
        min_delta = self.delta if self.strict_improvement else 0.0
        tie_eps = 1e-9 if self.reject_ties else -1.0

        if candidate > best_score + min_delta:
            self._consecutive_rejects = 0
            self._total_accepts += 1
            return GateDecision(
                action="accept_new_best",
                candidate_score=candidate,
                best_score=candidate,
                current_score=current_score,
                reason=f"new_best ({best_score:.3f} → {candidate:.3f}) [{self.metric}]",
            )

        if candidate > current_score + min_delta:
            self._consecutive_rejects = 0
            self._total_accepts += 1
            return GateDecision(
                action="accept",
                candidate_score=candidate,
                best_score=best_score,
                current_score=candidate,
                reason=f"improved ({current_score:.3f} → {candidate:.3f}) [{self.metric}]",
            )

        if (
            not self.strict_improvement
            and train_rollout is not None
            and prev_train_rollout is not None
            and train_rollout >= prev_train_rollout + train_delta
            and candidate >= current_score - 1e-9
        ):
            self._consecutive_rejects = 0
            self._total_accepts += 1
            return GateDecision(
                action="accept",
                candidate_score=candidate,
                best_score=best_score,
                current_score=candidate,
                reason=(
                    f"train_improved ({prev_train_rollout:.3f} → {train_rollout:.3f}) "
                    f"selection_held ({candidate:.3f}) [{self.metric}]"
                ),
            )

        self._consecutive_rejects += 1
        self._total_rejects += 1
        if self.reject_ties and abs(candidate - current_score) <= tie_eps:
            reason = f"tie_rejected ({candidate:.3f} ≈ {current_score:.3f}) [{self.metric}]"
        elif self.strict_improvement and candidate > current_score:
            reason = f"delta_insufficient ({candidate:.3f} vs {current_score:.3f}, need +{min_delta}) [{self.metric}]"
        else:
            reason = f"no_improvement ({candidate:.3f} ≤ {current_score:.3f}) [{self.metric}]"
        return GateDecision(
            action="reject",
            candidate_score=candidate,
            best_score=best_score,
            current_score=current_score,
            reason=reason,
        )

    @property
    def should_early_stop(self) -> bool:
        """连续 reject 超过 patience 时触发早停。"""
        return self._consecutive_rejects >= self.patience

    @property
    def stats(self) -> dict:
        return {
            "consecutive_rejects": self._consecutive_rejects,
            "total_accepts": self._total_accepts,
            "total_rejects": self._total_rejects,
            "patience": self.patience,
        }
