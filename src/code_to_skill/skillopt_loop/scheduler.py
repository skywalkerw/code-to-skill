"""编辑预算调度器。

对齐 external/SkillOpt skillopt/scheduler/，控制每个 step 的 edit_budget。
"""
from __future__ import annotations

import math
import logging

logger = logging.getLogger(__name__)


class EditBudgetScheduler:
    """编辑预算调度器：支持多种退火策略。

    策略：
    - "constant": 固定预算
    - "cosine": 余弦退火（前期大预算 → 后期精细）
    - "linear": 线性递减
    - "exponential": 指数衰减
    """

    def __init__(
        self,
        initial_budget: int = 5,
        min_budget: int = 1,
        total_steps: int = 100,
        strategy: str = "constant",
    ):
        self.initial_budget = initial_budget
        self.min_budget = min_budget
        self.total_steps = total_steps
        self.strategy = strategy
        self._step = 0

    def step(self) -> int:
        """获取当前步的预算，并推进状态。"""
        budget = self.get_budget(self._step)
        self._step += 1
        return budget

    def get_budget(self, step: int) -> int:
        """计算第 step 步的编辑预算。"""
        if self.strategy == "constant":
            return self.initial_budget

        if self.strategy == "cosine":
            progress = min(step / max(self.total_steps, 1), 1.0)
            decay = 0.5 * (1 + math.cos(math.pi * progress))
            budget = self.min_budget + (self.initial_budget - self.min_budget) * decay
            return max(1, round(budget))

        if self.strategy == "linear":
            progress = min(step / max(self.total_steps, 1), 1.0)
            budget = self.initial_budget - (self.initial_budget - self.min_budget) * progress
            return max(1, round(budget))

        if self.strategy == "exponential":
            decay_rate = 0.95
            budget = self.initial_budget * (decay_rate ** step)
            return max(self.min_budget, round(budget))

        return self.initial_budget

    def reset(self):
        """重置步数计数。"""
        self._step = 0

    @property
    def current_step(self) -> int:
        return self._step
