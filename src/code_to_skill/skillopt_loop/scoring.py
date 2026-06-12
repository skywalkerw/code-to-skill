"""综合评分工具（M4 rollout → gate 的评分层）。

对齐 external/SkillOpt ``skillopt/utils/scoring.py``。

调用链
------
``envs/base._rollout_single_item`` 得到 ``predicted`` 后调用
``score_benchmark_item``；每条 item 的 ``scorer`` 字段选择评分器；结果中的
``hard`` / ``soft`` / ``missed_checks`` 供 selection gate（``gate.py``）与
reflect 使用。split 级均值由 ``compute_scores`` 聚合。

评分器路由（``score_benchmark_item``）
------------------------------------
由 benchmark item 的 ``scorer`` 字段决定（默认 ``keyword`` / ``deterministic``）：

1. **keyword / deterministic**（内置，无子进程）
   - ``score_rollout_result``：对 ``expected_checks`` 逐条做子串匹配（金额类
     check 忽略千分位逗号）。
   - 别名：``settings.skillopt.check_aliases``（全局）与 item 级
     ``check_aliases`` 合并后，可按 check 键追加同义词。
   - **hard**：全部 check 命中 → ``1``，否则 ``0``。
   - **soft**：``passed_checks / len(expected_checks)``。

2. **python_script / script / py / python**（benchmark 扩展脚本）
   - ``score_with_python_script`` 以子进程执行 item 指定的脚本。
   - 脚本路径（优先级）：``score_script`` → ``scorer_script`` →
     ``scorer_config.script`` / ``path``；相对路径基于
     ``item._benchmark_dir``（``BenchmarkSplits.from_dir`` 注入）或
     ``scorer_config.base_dir``。
   - **stdin JSON**::
         {"predicted": str, "item": dict, "global_check_aliases": dict}
   - **stdout JSON**（单行对象）：``hard``, ``soft``, ``passed_checks``,
     ``missed_checks``, ``precision``, ``recall``, ``f1``, ``justification``。
   - 脚本未返回 ``hard`` 时，由 ``soft`` 与 ``hard_threshold``（默认 0.8）
     推导；缺 ``missed_checks`` 时从 ``expected_checks`` 反推供 reflect。
   - 超时：``score_timeout_seconds`` 或 ``scorer_config.timeout_seconds``
     （默认 10s）；失败时 ``hard=0`` 且 ``error`` 字段记录原因。
   - 示例：``demo-project/benchmarks/score_expected_checks.py``（keyword +
     借贷平衡验算，Fineract 全量 benchmark 共用）。

3. **llm_judge / judge / llm**（语义 rubric）
   - ``score_with_llm_judge``：调用 ``routes.judge`` backend，``temperature=0``。
   - 适用于开放问答；item 可提供 ``rubric``。

单条结果字段
------------
各评分器统一产出（供 gate / history / reflect）：

- ``hard`` / ``soft`` / ``accuracy``：0–1 标量；gate 经 ``select_gate_score`` 投影。
- ``passed_checks`` / ``missed_checks``：可追溯的失败项，驱动 reflect。
- ``passed`` / ``total``、``precision`` / ``recall`` / ``f1``：辅助指标。
- ``score_type``：``python_script`` 时标记来源。

批量聚合
--------
``compute_scores(results)``：对 rollout 结果列表求 ``hard`` / ``soft`` /
``accuracy`` / ``f1`` 均值，写入 train/selection/test 汇总。

其它
----
``skill_hash``：Skill 正文短 hash，用于 selection cache 去重。

公开 API
--------
- ``score_benchmark_item`` — 评分入口（按 item.scorer 路由）
- ``score_rollout_result`` — 内置 keyword 评分
- ``score_with_python_script`` — 扩展脚本评分
- ``score_with_llm_judge`` — LLM Judge 评分
- ``merge_check_aliases`` — 全局 + item 别名合并
- ``compute_scores`` — split 级聚合
- ``skill_hash`` — Skill 内容哈希
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from typing import Any

from .token_budgets import get_token_budgets

logger = logging.getLogger(__name__)


# ── 单条评分 ───────────────────────────────────────────────

def _normalize_text_for_check(text: str) -> str:
    """归一化文本便于 keyword/金额匹配（去千分位逗号）。"""
    return text.replace(",", "").lower()


def _check_keyword(text: str, check: str) -> bool:
    """检查文本中是否包含预期关键词（金额类 check 忽略千分位）。"""
    norm_text = _normalize_text_for_check(text)
    norm_check = _normalize_text_for_check(check)
    return norm_check in norm_text


def merge_check_aliases(
    global_aliases: dict[str, list[str]] | None,
    item_aliases: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    """合并 settings.skillopt.check_aliases 与 benchmark item 级别名。"""
    merged: dict[str, list[str]] = {}
    for source in (global_aliases, item_aliases):
        for key, values in (source or {}).items():
            norm_key = (key or "").strip().lower()
            if not norm_key:
                continue
            merged.setdefault(norm_key, [])
            merged[norm_key].extend(str(v) for v in (values or []) if str(v).strip())
    return merged


def _check_aliases(check: str, extra_aliases: dict[str, list[str]] | None = None) -> list[str]:
    aliases = list((extra_aliases or {}).get((check or "").strip().lower(), []))
    # Preserve order while removing duplicates.
    seen: set[str] = set()
    out: list[str] = []
    for alias in aliases:
        norm = _normalize_text_for_check(alias)
        if norm and norm not in seen:
            out.append(alias)
            seen.add(norm)
    return out


def _check_expected(text: str, check: str, aliases: dict[str, list[str]] | None = None) -> bool:
    if _check_keyword(text, check):
        return True
    return any(_check_keyword(text, alias) for alias in _check_aliases(check, aliases))


def score_benchmark_item(
    predicted: str,
    item: dict,
    *,
    judge_backend: Any = None,
    hard_threshold: float = 0.8,
    global_check_aliases: dict[str, list[str]] | None = None,
) -> dict:
    """按 benchmark item 的 ``scorer`` 字段路由评分器。

    调用链：rollout 产出 predicted → 本函数 → gate 用 hard/soft 做 accept/reject。
    """
    scorer = str(item.get("scorer") or "keyword").strip().lower()
    # llm_judge：语义 rubric 打分，走 routes.judge。
    if scorer in ("llm_judge", "judge", "llm"):
        result = score_with_llm_judge(
            predicted,
            item.get("question", ""),
            rubric=item.get("rubric"),
            backend=judge_backend,
            hard_threshold=hard_threshold,
        )
        result.setdefault("passed_checks", [])
        result.setdefault("missed_checks", list(item.get("expected_checks") or []))
        if result.get("hard") == 1:
            result["missed_checks"] = []
        return result
    # python_script：benchmark 外挂脚本（如 score_expected_checks.py），可扩展领域校验。
    if scorer in ("python", "py", "python_script", "script"):
        return score_with_python_script(
            predicted,
            item,
            hard_threshold=hard_threshold,
            global_check_aliases=global_check_aliases,
        )
    # keyword / deterministic（默认）：内置 keyword 匹配，无子进程开销。
    checks = list(item.get("expected_checks") or [])
    aliases = merge_check_aliases(global_check_aliases, item.get("check_aliases"))
    return score_rollout_result(predicted, checks, check_aliases=aliases or None)


def score_rollout_result(
    predicted: str,
    expected_checks: list[str],
    *,
    check_aliases: dict[str, list[str]] | None = None,
) -> dict:
    """确定性 scorer：keyword/regex 检查 + accuracy/precision/F1。

    对齐 external/SkillOpt 的评分机制：
    - hard: 所有 checks 通过 → 1, 否则 → 0
    - soft: 通过的 checks / 总数
    - accuracy: hard pass rate
    - F1: 2 * precision * recall / (precision + recall)
    """
    passed_checks: list[str] = []
    missed_checks: list[str] = []
    for check in expected_checks:
        if _check_expected(predicted, check, check_aliases):
            passed_checks.append(check)
        else:
            missed_checks.append(check)

    passed = len(passed_checks)
    total = len(expected_checks) if expected_checks else 1
    soft = passed / total
    # hard=1 当且仅当全部 expected_checks 命中；gate metric=hard 时直接比较此字段。
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
        "passed_checks": passed_checks,
        "missed_checks": missed_checks,
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

    # split 级均值：train/selection/test 汇总后写入 history.json 与 gate 输入。
    return {
        "hard": round(sum(_hard(r) for r in results) / n, 3),
        "soft": round(sum(_soft(r) for r in results) / n, 3),
        "accuracy": round(sum(_acc(r) for r in results) / n, 3),
        "f1": round(sum(_f1(r) for r in results) / n, 3),
    }


def skill_hash(content: str) -> str:
    """返回 Skill 内容的短确定性 hash（用于缓存）。"""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ── Python Script Scorer ────────────────────────────────────

def _script_path_from_item(item: dict) -> str:
    scorer_config = item.get("scorer_config") or {}
    path = (
        item.get("score_script")
        or item.get("scorer_script")
        or scorer_config.get("script")
        or scorer_config.get("path")
    )
    return str(path or "").strip()


def _resolve_script_path(script_path: str, item: dict) -> str:
    script_path = os.path.expanduser(script_path)
    if os.path.isabs(script_path):
        return script_path
    scorer_config = item.get("scorer_config") or {}
    base_dir = (
        item.get("score_script_base_dir")
        or scorer_config.get("base_dir")
        or item.get("_benchmark_dir")
        or item.get("_item_dir")
    )
    if base_dir:
        return os.path.abspath(
            os.path.join(os.path.expanduser(str(base_dir)), script_path)
        )
    return os.path.abspath(script_path)


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v).strip()]
    return []


def _coerce_count(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return fallback


def _normalize_script_score(
    raw: dict,
    item: dict,
    *,
    hard_threshold: float,
) -> dict:
    checks = list(item.get("expected_checks") or [])
    passed_checks = _coerce_str_list(raw.get("passed_checks"))
    missed_checks = _coerce_str_list(raw.get("missed_checks") or raw.get("missed"))

    if "soft" in raw:
        soft = max(0.0, min(1.0, float(raw.get("soft") or 0.0)))
    elif checks:
        passed = len(passed_checks)
        soft = passed / max(len(checks), 1)
    else:
        soft = 1.0 if int(raw.get("hard", 0) or 0) == 1 else 0.0

    if "hard" in raw:
        hard = 1 if int(raw.get("hard") or 0) == 1 else 0
    else:
        # 脚本未返回 hard 时，按 soft 与 hard_threshold（默认 0.8）推导。
        hard = 1 if soft >= hard_threshold else 0

    # 脚本只返回 soft 时，从 expected_checks 反推 missed_checks 供 reflect 使用。
    if not missed_checks and checks and hard == 0:
        seen = {str(c) for c in passed_checks}
        missed_checks = [c for c in checks if str(c) not in seen]

    precision = float(raw.get("precision", 0.0) or 0.0)
    recall = float(raw.get("recall", soft) or soft)
    f1 = float(raw.get("f1", 0.0) or 0.0)
    if not f1 and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)

    return {
        "hard": hard,
        "soft": round(soft, 3),
        "passed": _coerce_count(
            raw.get("passed_count", raw.get("passed")),
            len(passed_checks),
        ),
        "total": int(raw.get("total", len(checks) if checks else 1) or 1),
        "passed_checks": passed_checks,
        "missed_checks": missed_checks,
        "accuracy": float(raw.get("accuracy", hard) or hard),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "score_type": "python_script",
        **({"justification": str(raw.get("justification"))} if raw.get("justification") else {}),
    }


def score_with_python_script(
    predicted: str,
    item: dict,
    *,
    hard_threshold: float = 0.8,
    global_check_aliases: dict[str, list[str]] | None = None,
) -> dict:
    """Run a benchmark-provided Python scorer script.

    The script receives JSON on stdin:
    ``{"predicted": str, "item": dict, "global_check_aliases": dict | optional}``

    It must print a JSON object. Supported fields include:
    ``hard``, ``soft``, ``passed_checks``, ``missed_checks``, ``precision``,
    ``recall``, ``f1`` and ``justification``.
    """
    script_path = _script_path_from_item(item)
    checks = list(item.get("expected_checks") or [])
    if not script_path:
        return {
            "hard": 0,
            "soft": 0.0,
            "passed": 0,
            "total": len(checks) if checks else 1,
            "passed_checks": [],
            "missed_checks": checks,
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "score_type": "python_script",
            "error": "missing_score_script",
        }

    # 相对路径基于 item._benchmark_dir（BenchmarkSplits.from_dir 注入）或 scorer_config.base_dir。
    script_path = _resolve_script_path(script_path, item)
    scorer_config = item.get("scorer_config") or {}
    timeout = float(
        item.get("score_timeout_seconds")
        or scorer_config.get("timeout_seconds")
        or 10
    )
    payload = {
        "predicted": predicted,
        "item": item,
        "global_check_aliases": global_check_aliases or {},
    }
    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "").strip()[:500])
        parsed = json.loads(proc.stdout or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("script output must be a JSON object")
        return _normalize_script_score(
            parsed,
            item,
            hard_threshold=hard_threshold,
        )
    except Exception as exc:
        logger.warning("Python scorer failed for %s: %s", script_path, exc)
        return {
            "hard": 0,
            "soft": 0.0,
            "passed": 0,
            "total": len(checks) if checks else 1,
            "passed_checks": [],
            "missed_checks": checks,
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "score_type": "python_script",
            "error": str(exc)[:500],
        }


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

    from code_to_skill.model_provider.types import InteractionRequest

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
            max_output_tokens=get_token_budgets().judge,
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
