"""Tests for diagnosis_rules (design 08 phase E)."""
from pathlib import Path

from code_to_skill.skillopt_loop.diagnosis_rules import (
    diagnoses_to_candidate_rules,
    format_candidate_rules_for_reflect,
    write_diagnosis_step_summary,
)
from code_to_skill.skillopt_loop.skill_quality import QualityGateConfig


def test_diagnoses_to_candidate_rules_filters_leakage():
    diagnoses = [
        {
            "item_id": "x1",
            "failure_type": "missing_business_rule",
            "status": "ready",
            "general_rule": "Add skill rules for missed checks: foo.",
            "code_facts": [{"ref": "a.java", "snippet": "code"}],
        },
        {
            "item_id": "x2",
            "failure_type": "prompt_echo",
            "status": "ready",
            "general_rule": "Rule for jv_purchase_001 only",
            "code_facts": [],
        },
    ]
    qcfg = QualityGateConfig(benchmark_id_patterns=[r"\bjv_[a-z0-9_]+\b"])
    rules = diagnoses_to_candidate_rules(diagnoses, quality_config=qcfg)
    assert len(rules) == 1
    assert rules[0]["source_item"] == "x1"
    assert rules[0]["status"] == "ready"


def test_require_code_facts_excludes_empty():
    diagnoses = [{
        "item_id": "y",
        "status": "ready",
        "general_rule": "Cover missed business terms in output.",
        "code_facts": [],
    }]
    rules = diagnoses_to_candidate_rules(diagnoses, require_code_facts=True)
    assert rules == []


def test_write_diagnosis_step_summary(tmp_path: Path):
    path = write_diagnosis_step_summary(
        str(tmp_path),
        step=2,
        diagnoses=[{"failure_type": "prompt_echo", "status": "ready"}],
        candidate_rules=[{"rule_id": "r1", "text": "no echo"}],
    )
    assert Path(path).is_file()
    text = format_candidate_rules_for_reflect([{"status": "ready", "source_item": "a", "text": "rule"}])
    assert "Diagnosis candidate rules" in text
