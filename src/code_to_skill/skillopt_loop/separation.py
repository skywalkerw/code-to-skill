"""Backend 分离与 Accumulation 支持。

对齐 external/SkillOpt skillopt/engine/trainer.py 中的 backend 管理和 accumulation 逻辑。

核心功能：
1. BackendManager: 统一管理 optimizer（Reflect/Select/Merge）和 target（Rollout）后端
2. Accumulator: 多批 rollout 累积后合并一次 update
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Backend Manager ──────────────────────────────────────────

class BackendManager:
    """分离 optimizer 和 target 后端。

    论文关键设计：optimizer model（做 Reflect/Select/Merge）可以比
    target model（做 Rollout）更强，且 optimizer 只在训练时调用，不增加部署成本。

    Usage:
        bm = BackendManager(
            target_backend=target_backend_instance,
            optimizer_backend=optimizer_backend_instance,
        )
        # or auto-create from env:
        bm = BackendManager.from_env()
    """

    def __init__(
        self,
        target_backend: Any = None,
        optimizer_backend: Any = None,
    ):
        self._target = target_backend
        self._optimizer = optimizer_backend

    @property
    def target(self) -> Any:
        """返回 target 后端（用于 Rollout）。"""
        if self._target is None:
            self._target = self._try_create_backend()
        return self._target

    @property
    def optimizer(self) -> Any:
        """返回 optimizer 后端（用于 Reflect/Select/Aggregate）。

        如果未单独设置，降级为 target backend。
        """
        if self._optimizer is not None:
            return self._optimizer
        return self.target  # fallback: same as target

    def has_target(self) -> bool:
        return self._target is not None or self._try_create_backend() is not None

    def has_optimizer(self) -> bool:
        return self._optimizer is not None or self.has_target()

    @staticmethod
    def _try_create_backend() -> Any | None:
        """尝试从环境变量创建 LLM backend。"""
        try:
            from code_to_skill.model_gateway.llm_backend import (
                is_llm_available,
                create_llm_backend,
            )
            if is_llm_available():
                return create_llm_backend()
        except Exception:
            pass
        return None

    @classmethod
    def from_env(cls, use_llm: bool = True) -> "BackendManager":
        """从环境变量创建（target 和 optimizer 相同）。"""
        backend = None
        if use_llm:
            backend = cls._try_create_backend()
        return cls(target_backend=backend, optimizer_backend=backend)

    @classmethod
    def from_separate(
        cls,
        target_model: str | None = None,
        optimizer_model: str | None = None,
    ) -> "BackendManager":
        """分别创建 target 和 optimizer 后端（预留接口）。

        当前简化：target 和 optimizer 共用相同的创建逻辑，
        后续可扩展到不同模型/不同 API key。
        """
        target = cls._try_create_backend()
        optimizer = cls._try_create_backend()  # same for now
        return cls(target_backend=target, optimizer_backend=optimizer)


# ── Accumulator ──────────────────────────────────────────────

class Accumulator:
    """多批 Rollout 累积后合并一次 Update。

    论文的 accumulation 机制：accumulation > 1 时，
    执行多次 rollout 但只做一次 reflect → update。
    这解耦了 rollout 吞吐量和 update 频率。

    Example:
        acc = Accumulator(accumulate=2)
        for batch in batches:
            acc.add_batch(rollout_results)
            if acc.ready:
                merged_results = acc.consume()  # 累积的所有结果
                # 用 merged_results 做一次 reflect → update
    """

    def __init__(self, accumulate: int = 1):
        if accumulate < 1:
            raise ValueError(f"accumulate must be >= 1, got {accumulate}")
        self._accumulate = accumulate
        self._buffer: list[dict] = []
        self._batch_count = 0

    def add_batch(self, results: list[dict]) -> None:
        """添加一批 rollout 结果到缓冲区。"""
        self._buffer.extend(results)
        self._batch_count += 1

    @property
    def ready(self) -> bool:
        """是否积累够了，可以触发一次 update。"""
        return self._batch_count >= self._accumulate

    def consume(self) -> list[dict]:
        """取出所有累积的结果并重置。"""
        results = list(self._buffer)
        self._buffer.clear()
        self._batch_count = 0
        return results

    @property
    def pending_count(self) -> int:
        """当前缓冲区中的结果数。"""
        return len(self._buffer)

    def flush_remaining(self) -> list[dict]:
        """强制取出剩余结果（用于 epoch 结束清理）。"""
        if self._buffer:
            return self.consume()
        return []
