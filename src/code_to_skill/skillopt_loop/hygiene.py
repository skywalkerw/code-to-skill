"""Design 08 — Skill hygiene pass（合并/删除冗余规则）。"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .self_evolution_config import SelfEvolutionConfig
from .skill_rules import RuleAttributionTracker, _RULE_COMMENT_RE
from .skill_ops import apply_edits
from .types import EditOp

logger = logging.getLogger(__name__)


def estimate_skill_tokens(skill: str) -> int:
    """粗略 token 估计（字符数 / 4）。"""
    return max(1, len(skill) // 4)


def should_run_hygiene(
    skill: str,
    config: SelfEvolutionConfig,
    attribution: dict[str, dict[str, Any]] | None = None,
    *,
    force: bool = False,
) -> bool:
    if not config.hygiene_enabled:
        return False
    if force:
        return True
    if estimate_skill_tokens(skill) > config.max_skill_tokens:
        return True
    rules = _rule_lines(skill)
    if len(rules) > config.max_rules:
        return True
    if attribution:
        unused = sum(
            1 for rid, entry in attribution.items()
            if entry.get("rule_used_count", 0) < config.min_rule_use_count
            and entry.get("rule_regression_count", 0) >= 2
        )
        if unused >= 2:
            return True
    return False


def _rule_lines(skill: str) -> list[tuple[str | None, str]]:
    """(rule_id, bullet_text) 列表。"""
    lines = skill.splitlines()
    rules: list[tuple[str | None, str]] = []
    i = 0
    rid: str | None = None
    while i < len(lines):
        line = lines[i]
        m = _RULE_COMMENT_RE.match(line.strip())
        if m:
            rid = m.group(1).strip()
            i += 1
            continue
        if re.match(r"^\s*[-*]\s+", line):
            rules.append((rid, line.strip()))
            rid = None
        i += 1
    return rules


def run_hygiene_pass(
    skill: str,
    attribution: dict[str, dict[str, Any]] | None,
    config: SelfEvolutionConfig,
) -> list[EditOp]:
    """生成 hygiene 建议 edits（delete 长期未使用规则）。"""
    if not config.hygiene_enabled:
        return []

    attr = attribution or {}
    rules = _rule_lines(skill)
    edits: list[EditOp] = []
    seen_content: dict[str, str] = {}

    for rule_id, bullet in rules:
        content = re.sub(r"^\s*[-*]\s+", "", bullet).strip()
        if not content:
            continue
        norm = content.lower()
        if norm in seen_content:
            edits.append(EditOp(op="delete", content=content, target=bullet))
            continue
        seen_content[norm] = bullet

        if rule_id and rule_id in attr:
            used = attr[rule_id].get("rule_used_count", 0)
            regressions = attr[rule_id].get("rule_regression_count", 0)
            if used < config.min_rule_use_count and regressions == 0:
                continue
            if regressions >= 2:
                edits.append(EditOp(op="delete", content=content, target=bullet))

    return edits[:3]


def apply_hygiene_with_gate(
    skill: str,
    output_dir: str,
    *,
    adapter: Any,
    selection_items: list[dict],
    target_backend: Any,
    gate_metric: str = "soft",
    gate_mixed_weight: float = 0.5,
    gate_delta: float = 0.01,
    config: SelfEvolutionConfig | None = None,
    strict: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """对 Skill 执行 hygiene 并通过 selection gate 决定是否应用。"""
    from .gate import GateManager, select_gate_score

    config = config or SelfEvolutionConfig()
    attr_tracker = RuleAttributionTracker(output_dir)
    attr = attr_tracker._data

    if not should_run_hygiene(skill, config, attr, force=force):
        return {
            "applied": False,
            "reason": "hygiene_not_needed",
            "skill": skill,
            "edits": [],
        }

    hygiene_edits = run_hygiene_pass(skill, attr, config)
    if not hygiene_edits:
        return {
            "applied": False,
            "reason": "no_hygiene_edits",
            "skill": skill,
            "edits": [],
        }

    candidate, _ = apply_edits(skill, hygiene_edits)
    result: dict[str, Any] = {
        "applied": False,
        "reason": "no_selection_items",
        "skill": skill,
        "candidate_skill": candidate,
        "edits": [e.model_dump() for e in hygiene_edits],
        "edit_count": len(hygiene_edits),
    }
    if not selection_items:
        return result

    eval_result = adapter.evaluate(candidate, selection_items, target_backend=target_backend)
    base_eval = adapter.evaluate(skill, selection_items, target_backend=target_backend)
    cand_gate = select_gate_score(
        eval_result.get("accuracy", 0.0), eval_result["soft"],
        metric=gate_metric, mixed_weight=gate_mixed_weight,  # type: ignore[arg-type]
    )
    base_gate = select_gate_score(
        base_eval.get("accuracy", 0.0), base_eval["soft"],
        metric=gate_metric, mixed_weight=gate_mixed_weight,  # type: ignore[arg-type]
    )
    gate = GateManager(
        metric=gate_metric,  # type: ignore[arg-type]
        delta=gate_delta,
        strict_improvement=strict or config.strict_improvement,
        reject_ties=config.reject_ties,
    )
    decision = gate.evaluate(
        eval_result.get("accuracy", 0.0), eval_result["soft"],
        best_score=base_gate, current_score=base_gate,
    )
    result.update({
        "before_score": round(base_gate, 4),
        "after_score": round(cand_gate, 4),
        "gate_action": decision.action,
        "gate_reason": decision.reason,
    })
    if decision.action != "reject":
        best_path = os.path.join(output_dir, "best_skill.md")
        os.makedirs(output_dir, exist_ok=True)
        with open(best_path, "w", encoding="utf-8") as f:
            f.write(candidate)
        result["applied"] = True
        result["reason"] = "gate_accepted"
        result["skill"] = candidate
        result["best_skill_path"] = best_path
        logger.info(
            "[hygiene] applied: %d edits, gate %.3f → %.3f",
            len(hygiene_edits), base_gate, cand_gate,
        )
    else:
        result["reason"] = decision.reason
        logger.info("[hygiene] rejected by gate: %s", decision.reason)
    return result


def load_attribution_from_run(output_dir: str) -> dict[str, dict[str, Any]]:
    path = os.path.join(output_dir, "rule_attribution.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        return raw.get("rules") or raw
    return {}
