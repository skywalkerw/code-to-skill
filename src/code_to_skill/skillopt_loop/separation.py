"""Backend 分离与 Accumulation 支持。

对齐 external/SkillOpt skillopt/engine/trainer.py 中的 backend 管理和 accumulation 逻辑。

核心功能：
1. BackendManager: 统一管理 optimizer（Reflect/Select/Merge）和 target（Rollout）后端
2. Accumulator: 多批 rollout 累积后合并一次 update
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _as_config_dict(obj: Any) -> dict:
    """将 dict 或 Pydantic settings 转为普通 dict。"""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return {}


def resolve_skillopt_backend_ids(
    skillopt: dict | Any | None,
    model_provider: dict | Any | None = None,
) -> tuple[str | None, str | None]:
    """解析 rollout(target) 与 optimizer 的 backend ID。

    优先级（各自独立）：
    1. ``settings.skillopt.rollout_backend``
    2. ``settings.skillopt.optimizer_backend``
    3. ``model_provider.routes.target.primary`` / ``optimizer.primary``
    4. 环境变量 ``SKILL_LAB_TARGET_BACKEND`` / ``SKILL_LAB_OPTIMIZER_BACKEND``
    """
    skillopt = _as_config_dict(skillopt)
    mp = _as_config_dict(model_provider)
    routes = mp.get("routes") or {}
    if not routes and model_provider is not None and not isinstance(model_provider, dict):
        routes = getattr(model_provider, "routes", None) or {}

    def _route_primary(role: str) -> str | None:
        route = routes.get(role) or {}
        if hasattr(route, "model_dump"):
            route = route.model_dump()
        elif not isinstance(route, dict):
            primary = getattr(route, "primary", "") or ""
            return str(primary).strip() or None
        primary = (route.get("primary") or "").strip()
        return primary or None

    rollout = (
        skillopt.get("rollout_backend")
        or _route_primary("target")
        or os.environ.get("SKILL_LAB_TARGET_BACKEND")
    )
    optimizer = (
        skillopt.get("optimizer_backend")
        or _route_primary("optimizer")
        or os.environ.get("SKILL_LAB_OPTIMIZER_BACKEND")
    )
    return (
        str(rollout).strip() if rollout else None,
        str(optimizer).strip() if optimizer else None,
    )


def resolve_judge_backend_id(
    skillopt: dict | Any | None,
    model_provider: dict | Any | None = None,
) -> str | None:
    """解析 LLM Judge backend（``routes.judge`` 或 ``skillopt.judge_backend``）。"""
    skillopt = _as_config_dict(skillopt)
    mp = _as_config_dict(model_provider)
    routes = mp.get("routes") or {}
    if not routes and model_provider is not None and not isinstance(model_provider, dict):
        routes = getattr(model_provider, "routes", None) or {}

    route = routes.get("judge") or {}
    if hasattr(route, "model_dump"):
        route = route.model_dump()
    elif not isinstance(route, dict):
        primary = getattr(route, "primary", "") or ""
        route = {"primary": primary}

    judge = (
        skillopt.get("judge_backend")
        or (route.get("primary") or "").strip()
        or os.environ.get("SKILL_LAB_JUDGE_BACKEND")
    )
    return str(judge).strip() if judge else None


def _lookup_backend(
    backend_id: str | None,
    prebuilt: dict[str, Any] | None,
) -> Any | None:
    if not backend_id:
        return None
    if prebuilt and backend_id in prebuilt:
        return prebuilt[backend_id]
    return BackendManager._try_create_backend(backend_id)


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
        *,
        auto_create: bool = True,
    ):
        self._target = target_backend
        self._optimizer = optimizer_backend
        self._auto_create = auto_create

    @property
    def target(self) -> Any:
        """Rollout 用 target 后端；未注入且 auto_create 时才从环境创建。"""
        if self._target is None and self._auto_create:
            self._target = self._try_create_backend()
        return self._target

    @property
    def optimizer(self) -> Any:
        """Reflect/Select 用 optimizer；未注入时回退 target。"""
        if self._optimizer is not None:
            return self._optimizer
        return self.target

    def has_target(self) -> bool:
        return self._target is not None or self._try_create_backend() is not None

    def has_optimizer(self) -> bool:
        return self._optimizer is not None or self.has_target()

    @staticmethod
    def _try_create_backend(backend_id: str | None = None) -> Any | None:
        """尝试从环境变量创建 LLM backend。

        backend_id=None 时从 SKILL_LAB_LLM_BACKEND 默认 "deepseek"。
        """
        try:
            from code_to_skill.model_provider.llm_backend import (
                is_llm_available,
                create_llm_backend,
            )
            bid = backend_id or os.environ.get("SKILL_LAB_LLM_BACKEND", "deepseek")
            if is_llm_available(bid):
                return create_llm_backend(bid)
        except Exception:
            pass
        return None

    @classmethod
    def from_env(cls, use_llm: bool = True) -> "BackendManager":
        """从环境变量创建（target 和 optimizer 相同）。"""
        backend = None
        if use_llm:
            backend = cls._try_create_backend()
        return cls(target_backend=backend, optimizer_backend=backend, auto_create=use_llm)

    @classmethod
    def from_separate(
        cls,
        target_backend_id: str | None = None,
        optimizer_backend_id: str | None = None,
        use_llm: bool = True,
    ) -> "BackendManager":
        """分别创建 target 和 optimizer 后端。

        对齐 SkillOpt 论文的关键设计：
        optimizer 用更强模型（如 deepseek-v4-pro），target 用执行模型（如 deepseek-v4-flash）。

        backend_id 优先级：
        1. 显式传入 target_backend_id / optimizer_backend_id
        2. 环境变量 SKILL_LAB_TARGET_BACKEND / SKILL_LAB_OPTIMIZER_BACKEND
        3. 环境变量 SKILL_LAB_LLM_BACKEND（默认 "deepseek"）
        """
        target_bid = target_backend_id or os.environ.get("SKILL_LAB_TARGET_BACKEND")
        optimizer_bid = optimizer_backend_id or os.environ.get("SKILL_LAB_OPTIMIZER_BACKEND")

        target = cls._try_create_backend(target_bid) if use_llm else None
        optimizer = cls._try_create_backend(optimizer_bid) if use_llm else None

        if target and optimizer and target_bid == optimizer_bid:
            logger.info("[BackendManager] Optimizer and target share the same backend: %s", target_bid)
        elif target and optimizer:
            logger.info("[BackendManager] Separate backends — optimizer=%s, target=%s",
                        optimizer_bid or "default", target_bid or "default")

        return cls(
            target_backend=target,
            optimizer_backend=optimizer,
            auto_create=use_llm,
        )

    @classmethod
    def from_skillopt(
        cls,
        *,
        use_llm_rollout: bool = True,
        use_llm_optimizer: bool = True,
        rollout_backend_id: str | None = None,
        optimizer_backend_id: str | None = None,
        model_provider: dict | None = None,
    ) -> "BackendManager":
        """从 skillopt / model_provider 配置创建分离的 target 与 optimizer 后端。"""
        prebuilt: dict[str, Any] = {}
        if model_provider:
            try:
                from code_to_skill.model_provider.config import build_router_from_dict

                mp_dump = (
                    model_provider.model_dump()
                    if hasattr(model_provider, "model_dump")
                    else model_provider
                )
                _, prebuilt = build_router_from_dict(mp_dump)
            except Exception as exc:
                logger.warning("[BackendManager] pre-build backends failed: %s", exc)

        target = (
            _lookup_backend(rollout_backend_id, prebuilt)
            if use_llm_rollout
            else None
        )
        optimizer = (
            _lookup_backend(optimizer_backend_id, prebuilt)
            if use_llm_optimizer
            else None
        )

        if target and optimizer:
            if rollout_backend_id == optimizer_backend_id:
                logger.info(
                    "[BackendManager] Rollout and optimizer share backend: %s",
                    rollout_backend_id or "default",
                )
            else:
                logger.info(
                    "[BackendManager] Separate backends — rollout=%s, optimizer=%s",
                    rollout_backend_id or "default",
                    optimizer_backend_id or "default",
                )
        elif target:
            logger.info(
                "[BackendManager] Rollout backend: %s",
                rollout_backend_id or "default",
            )
        elif optimizer:
            logger.info(
                "[BackendManager] Optimizer backend: %s",
                optimizer_backend_id or "default",
            )

        return cls(
            target_backend=target,
            optimizer_backend=optimizer,
            auto_create=False,
        )


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
