"""各 LLM 阶段的输出 token 预算。

默认按 DeepSeek（1M 上下文 / 384K 输出）能力设置；可通过 config skillopt.token_budgets 覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenBudgets:
    rollout: int = 8192
    reflect_failure: int = 16384
    reflect_success: int = 4096
    select_edits: int = 4096
    judge: int = 4096
    aggregate: int = 4096
    slow_update: int = 4096
    meta_skill: int = 2048
    atom_extract: int = 8192
    reflect_retry: list[int] = field(default_factory=lambda: [32768, 65536])


_active = TokenBudgets()


def configure_token_budgets(overrides: dict | None = None) -> TokenBudgets:
    """用配置覆盖默认 token 预算（流水线启动时调用）。"""
    global _active
    if not overrides:
        return _active
    base = TokenBudgets()
    for key, val in overrides.items():
        if key == "reflect_retry":
            base.reflect_retry = [int(v) for v in val]
        elif hasattr(base, key):
            setattr(base, key, int(val))
    _active = base
    return _active


def get_token_budgets() -> TokenBudgets:
    return _active
