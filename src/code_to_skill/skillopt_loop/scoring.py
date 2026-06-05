"""综合评分工具。

对齐 external/SkillOpt skillopt/utils/scoring.py

提供：
- score_rollout_result: 单条 rollout 评分（deterministic keyword check）
- compute_scores: 批量结果聚合
- score_with_llm_judge: LLM Judge rubric 语义评分
- skill_hash: Skill 内容哈希
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── 单条评分 ───────────────────────────────────────────────

def _check_keyword(text: str, check: str) -> bool:
    """检查文本中是否包含预期关键词。"""
    lower = text.lower()
    return check.lower() in lower


def score_rollout_result(predicted: str, expected_checks: list[str]) -> dict:
    """确定性 scorer：keyword/regex 检查 + accuracy/precision/F1。

    对齐 external/SkillOpt 的评分机制：
    - hard: 所有 checks 通过 → 1, 否则 → 0
    - soft: 通过的 checks / 总数
    - accuracy: hard pass rate
    - F1: 2 * precision * recall / (precision + recall)
    """
    passed = 0
    for check in expected_checks:
        if _check_keyword(predicted, check):
            passed += 1

    total = len(expected_checks) if expected_checks else 1
    soft = passed / total
    hard = 1 if soft == 1.0 else 0

    # Precision/Recall/F1（简单版本：expected checks 作为 ground truth）
    precision = passed / max(len(predicted.split()), 1)
    recall = soft  # simplified: pass rate = recall
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "hard": hard,
        "soft": round(soft, 3),
        "passed": passed,
        "total": total,
        "accuracy": float(hard),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


# ── 批量聚合 ───────────────────────────────────────────────

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


# ── LLM Judge Scorer ────────────────────────────────────────

def score_with_llm_judge(
    predicted: str,
    question: str,
    rubric: dict | None = None,
    backend: Any = None,
    hard_threshold: float = 0.8,
) -> dict:
    """用 LLM Judge 对 answer 做 rubric 评分。

    对齐 external/SkillOpt 的 llm_judge scorer。

    Args:
        predicted: Agent 的预测回答
        question: 原始问题
        rubric: 评分标准，格式 {"dimensions": [{"name": ..., "weight": ..., "description": ...}]}
        backend: LLM 后端
        hard_threshold: hard 分的阈值（>= 此分数视为通过）

    Returns:
        {"hard": int, "soft": float, "dimension_scores": [...], "justification": str}
    """
    if not rubric:
        rubric = _DEFAULT_JUDGE_RUBRIC

    if not backend:
        return _fallback_keyword_score(predicted, rubric)

    dimensions = rubric.get("dimensions", [])
    if not dimensions:
        return _fallback_keyword_score(predicted, rubric)

    rubric_text = "\n".join([
        f"- {d['name']} (weight={d.get('weight', 0.5)}): {d.get('description', '')}"
        for d in dimensions
    ])

    from code_to_skill.model_gateway.types import InteractionRequest

    try:
        resp = backend.invoke(InteractionRequest(
            role="judge",
            stage="score",
            messages=[{
                "role": "system",
                "content": _JUDGE_PROMPT.format(
                    question=question[:1000],
                    predicted_answer=predicted[:1500],
                    rubric=rubric_text,
                ),
            }],
            max_output_tokens=512,
            temperature=0.0,  # 可复现
        ))

        from .json_utils import safe_json_parse
        parsed = safe_json_parse(resp.content)

        if parsed and isinstance(parsed, dict) and "scores" in parsed:
            dim_scores = parsed["scores"]
            weighted = sum(
                s.get("score", 0) * (dimensions[i].get("weight", 0.5) if i < len(dimensions) else 0.5)
                for i, s in enumerate(dim_scores)
            )
            soft = round(min(weighted, 1.0), 3)
            return {
                "hard": 1 if soft >= hard_threshold else 0,
                "soft": soft,
                "dimension_scores": dim_scores,
                "justification": parsed.get("justification", ""),
                "score_type": "llm_judge",
            }
    except Exception as e:
        logger.warning("LLM Judge scoring failed: %s, falling back to keyword", e)

    return _fallback_keyword_score(predicted, rubric)


def _fallback_keyword_score(predicted: str, rubric: dict) -> dict:
    """LLM Judge 不可用时的降级 keyword 评分。"""
    dimensions = rubric.get("dimensions", [])
    if not dimensions:
        return {"hard": 0, "soft": 0.0, "dimension_scores": [], "score_type": "fallback_keyword"}

    dim_scores = []
    total_weight = 0.0
    weighted_sum = 0.0
    predicted_lower = predicted.lower()

    for d in dimensions:
        name = d.get("name", "")
        weight = d.get("weight", 0.5)
        desc = d.get("description", "").lower()
        # 简单规则：检查预测文本中是否包含维度关键词
        keywords = name.lower().split("_") + desc.split()[:5]
        match = any(kw in predicted_lower for kw in keywords if len(kw) > 3)
        score = 1.0 if match else 0.0
        dim_scores.append({"dimension": name, "score": score, "justification": "keyword match"})
        total_weight += weight
        weighted_sum += score * weight

    soft = round(weighted_sum / max(total_weight, 0.01), 3)
    return {
        "hard": 1 if soft >= 0.8 else 0,
        "soft": soft,
        "dimension_scores": dim_scores,
        "score_type": "fallback_keyword",
    }


_DEFAULT_JUDGE_RUBRIC = {
    "dimensions": [
        {"name": "completeness", "weight": 0.4, "description": "Answer covers all required aspects"},
        {"name": "accuracy", "weight": 0.4, "description": "Answer is factually correct"},
        {"name": "clarity", "weight": 0.2, "description": "Answer is clear and well-structured"},
    ]
}

_JUDGE_PROMPT = """## Task
Score the following Agent answer against the rubric.

## Question
{question}

## Agent Answer
{predicted_answer}

## Rubric
{rubric}

## Instructions
For each dimension, assign a score 0-1 and a one-sentence justification.
Return JSON: {{"scores": [{{"dimension": "...", "score": 0.X, "justification": "..."}}]}}"""
