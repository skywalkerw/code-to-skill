"""Tests for rule bank support_score (design 08 §8.3)."""
from code_to_skill.skillopt_loop.rule_bank import compute_rule_support_score, select_active_rules, RuleBankConfig


def test_compute_rule_support_score_with_evidence():
    score = compute_rule_support_score({
        "support_count": 2,
        "accepted_count": 2,
        "regression_count": 0,
        "evidence_refs": ["Foo.java"],
    })
    assert score >= 0.75


def test_low_support_score_excluded_from_active():
    rules = [{
        "rule_id": "low",
        "text": "weak",
        "status": "active",
        "support_count": 1,
        "accepted_count": 0,
        "regression_count": 2,
        "evidence_refs": [],
    }]
    cfg = RuleBankConfig(min_support_score=0.75, max_active_rules=5)
    assert select_active_rules(rules, cfg) == []
