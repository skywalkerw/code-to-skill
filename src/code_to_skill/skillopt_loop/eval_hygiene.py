"""Selection/test rollout + output hygiene 统一入口。"""
from __future__ import annotations

from typing import Any

from .output_hygiene import OutputHygieneConfig, apply_hygiene_to_rollout_results


def rollout_with_hygiene(
    adapter: Any,
    skill: str,
    items: list[dict],
    *,
    target_backend: Any = None,
    hygiene_cfg: OutputHygieneConfig | None = None,
) -> tuple[list[dict], float, float]:
    """Rollout items and optionally apply output hygiene; return (results, hard, soft)."""
    if not items:
        return [], 0.0, 0.0
    results = adapter.rollout(skill, items, target_backend=target_backend)
    if hygiene_cfg is not None and hygiene_cfg.enabled:
        results = apply_hygiene_to_rollout_results(results, hygiene_cfg)
    n = max(len(results), 1)
    hard = sum(float(r.get("accuracy", r.get("hard", 0))) for r in results) / n
    soft = sum(float(r.get("soft", 0)) for r in results) / n
    return results, hard, soft
