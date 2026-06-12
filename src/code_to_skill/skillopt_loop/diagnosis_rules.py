"""将 code diagnosis 转为可注入 reflect 的候选规则（设计 08 §8）。"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .skill_quality import QualityGateConfig, scan_skill_quality


_RULE_TYPE_BY_FAILURE: dict[str, str] = {
    "missing_business_rule": "business_mapping",
    "output_format_error": "output_policy",
    "scorer_alias_gap": "terminology_policy",
    "prompt_echo": "hygiene_policy",
}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", (text or "").strip().lower())[:40]
    return slug.strip("_") or "rule"


def diagnoses_to_candidate_rules(
    diagnoses: list[dict],
    *,
    quality_config: QualityGateConfig | None = None,
    require_code_facts: bool = False,
) -> list[dict[str, Any]]:
    """Map diagnosis rows to candidate rules passing quality scan."""
    cfg = quality_config or QualityGateConfig()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in diagnoses:
        if row.get("status") == "needs_review":
            continue
        text = str(
            row.get("general_rule")
            or row.get("suggested_general_rule")
            or row.get("failure_cause")
            or ""
        ).strip()
        if not text or text in seen:
            continue
        code_facts = row.get("code_facts") or []
        if require_code_facts and not code_facts:
            continue
        scan = scan_skill_quality(f"- {text}\n", cfg)
        if not scan.get("passed"):
            continue
        seen.add(text)
        failure_type = str(row.get("failure_type") or "")
        out.append({
            "rule_id": _slug(text),
            "text": text,
            "source_item": row.get("item_id", ""),
            "failure_type": failure_type,
            "rule_type": _RULE_TYPE_BY_FAILURE.get(failure_type, "business_mapping"),
            "status": "ready" if code_facts else "candidate",
            "evidence_refs": [f.get("ref") for f in code_facts if f.get("ref")][:4],
        })
    return out


def format_candidate_rules_for_reflect(rules: list[dict]) -> str:
    if not rules:
        return ""
    lines = ["### Diagnosis candidate rules"]
    for rule in rules[:6]:
        lines.append(
            f"- [{rule.get('status', 'candidate')}] "
            f"{rule.get('source_item')}: {rule.get('text')}"
        )
        refs = rule.get("evidence_refs") or []
        if refs:
            lines.append(f"  evidence_refs: {', '.join(refs[:3])}")
    return "\n".join(lines)


def write_diagnosis_step_summary(
    output_dir: str,
    step: int,
    diagnoses: list[dict],
    candidate_rules: list[dict] | None = None,
) -> str:
    by_type: dict[str, int] = {}
    needs_review = 0
    for row in diagnoses:
        ft = str(row.get("failure_type") or "unknown")
        by_type[ft] = by_type.get(ft, 0) + 1
        if row.get("status") == "needs_review":
            needs_review += 1
    summary = {
        "schema_version": "1.0",
        "step": step,
        "diagnosis_count": len(diagnoses),
        "needs_review_count": needs_review,
        "by_failure_type": by_type,
        "candidate_rule_count": len(candidate_rules or []),
        "candidate_rules": (candidate_rules or [])[:12],
    }
    step_dir = os.path.join(output_dir, "code_diagnosis", f"step_{step:04d}")
    os.makedirs(step_dir, exist_ok=True)
    path = os.path.join(step_dir, "summary.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return path
