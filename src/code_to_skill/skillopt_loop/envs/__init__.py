"""SkillOpt 环境适配器（EnvAdapter）抽象层。

对齐 external/SkillOpt skillopt/envs/ 的设计：
- EnvAdapter: 抽象基类，定义 rollout/reflect 接口
- DEFAULTAdapter: 内置默认实现，适配当前仓库的 benchmark 格式
- 后续可按 benchmark 新增子类（如 FineractAdapter）
"""
from .base import EnvAdapter, DEFAULTAdapter

__all__ = ["EnvAdapter", "DEFAULTAdapter"]
