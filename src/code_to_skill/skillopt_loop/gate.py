"""评估门禁模块。

对齐 external/SkillOpt skillopt/evaluation/gate.py
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

GateAction = Literal["accept_new_best", "accept", "reject"]


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

    行为：
    - candidate_score > best_score → accept_new_best
    - candidate_score > current_score → accept
    - 连续 reject 超过 patience → 触发早停信号
    """

    def __init__(self, patience: int = 10, delta: float = 0.01):
        self.patience = patience
        self.delta = delta
        self._consecutive_rejects = 0
        self._total_accepts = 0
        self._total_rejects = 0

    def evaluate(
        self,
        candidate_score: float,
        best_score: float,
        current_score: float,
    ) -> GateDecision:
        """评估候选分数是否通过门禁。

        Returns:
            GateDecision 含 action 和 reason。
        """
        if candidate_score > best_score + self.delta:
            self._consecutive_rejects = 0
            self._total_accepts += 1
            return GateDecision(
                action="accept_new_best",
                candidate_score=candidate_score,
                best_score=candidate_score,
                current_score=current_score,
                reason=f"new_best ({best_score:.3f} → {candidate_score:.3f})",
            )

        if candidate_score > current_score:
            self._consecutive_rejects = 0
            self._total_accepts += 1
            return GateDecision(
                action="accept",
                candidate_score=candidate_score,
                best_score=best_score,
                current_score=candidate_score,
                reason=f"improved ({current_score:.3f} → {candidate_score:.3f})",
            )

        self._consecutive_rejects += 1
        self._total_rejects += 1
        return GateDecision(
            action="reject",
            candidate_score=candidate_score,
            best_score=best_score,
            current_score=current_score,
            reason=f"no_improvement ({candidate_score:.3f} ≤ {current_score:.3f})",
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
