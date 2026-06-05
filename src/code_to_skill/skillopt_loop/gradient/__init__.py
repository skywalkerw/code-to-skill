"""Gradient 模块：Reflect → Aggregate 的梯度生成管线。

对齐 external/SkillOpt skillopt/gradient/ 的设计。
"""
from .aggregate import merge_patches

__all__ = ["merge_patches"]
