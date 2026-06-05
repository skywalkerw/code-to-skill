"""综合评分工具。

对齐 external/SkillOpt skillopt/utils/scoring.py
"""
from __future__ import annotations

import hashlib


def compute_scores(results: list[dict]) -> dict[str, float]:
    """从 rollout 结果列表计算综合指标。

    返回: {hard, soft, accuracy, precision, recall, f1}
    """
    if not results:
        return {"hard": 0.0, "soft": 0.0, "accuracy": 0.0,
                "precision": 0.0, "recall": 0.0, "f1": 0.0}

    n = len(results)

    def _hard(r):
        return float(r.get("hard", 0))
    def _soft(r):
        return float(r.get("soft", 0.0))
    def _acc(r):
        return float(r.get("accuracy", 0.0))
    def _f1(r):
        return float(r.get("f1", 0.0))

    return {
        "hard": round(sum(_hard(r) for r in results) / n, 3),
        "soft": round(sum(_soft(r) for r in results) / n, 3),
        "accuracy": round(sum(_acc(r) for r in results) / n, 3),
        "f1": round(sum(_f1(r) for r in results) / n, 3),
    }


def skill_hash(content: str) -> str:
    """返回 Skill 内容的短确定性 hash（用于缓存）。"""
    return hashlib.sha256(content.encode()).hexdigest()[:16]
