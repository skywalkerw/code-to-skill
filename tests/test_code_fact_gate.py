"""Tests for code fact evidence gate (设计 09 §14 / Phase 3)."""

import pytest

from code_to_skill.skillopt_loop.rule_bank import (
    remove_no_evidence_business_rules,
    compute_rule_support_score,
)


class TestRemoveNoEvidenceBusinessRules:
    def test_removes_business_rule_without_evidence(self):
        rules = [
            {
                "rule_id": "rule_001",
                "text": "Include debit and credit in journal entry",
                "failure_type": "missing_business_rule",
                "evidence_refs": [],
                "code_facts": [],
            },
        ]
        filtered = remove_no_evidence_business_rules(rules)
        assert filtered == []

    def test_keeps_business_rule_with_evidence_refs(self):
        rules = [
            {
                "rule_id": "rule_001",
                "text": "Include debit and credit in journal entry",
                "failure_type": "missing_business_rule",
                "evidence_refs": ["CashProcessor.java#createEntry"],
                "code_facts": [],
            },
        ]
        filtered = remove_no_evidence_business_rules(rules)
        assert len(filtered) == 1

    def test_keeps_business_rule_with_code_facts(self):
        rules = [
            {
                "rule_id": "rule_001",
                "text": "Include debit and credit in journal entry",
                "failure_type": "missing_business_rule",
                "evidence_refs": [],
                "code_facts": [{"fact_id": "f1", "statement": "test"}],
            },
        ]
        filtered = remove_no_evidence_business_rules(rules)
        assert len(filtered) == 1

    def test_keeps_prompt_echo_rule_without_evidence(self):
        rules = [
            {
                "rule_id": "rule_002",
                "text": "Do not repeat skill document",
                "failure_type": "prompt_echo",
                "evidence_refs": [],
                "code_facts": [],
            },
        ]
        filtered = remove_no_evidence_business_rules(rules)
        assert len(filtered) == 1

    def test_keeps_output_format_rule_without_evidence(self):
        rules = [
            {
                "rule_id": "rule_003",
                "text": "Output in table format",
                "failure_type": "output_format_error",
                "evidence_refs": [],
                "code_facts": [],
            },
        ]
        filtered = remove_no_evidence_business_rules(rules)
        assert len(filtered) == 1

    def test_keeps_scorer_alias_gap_without_evidence(self):
        rules = [
            {
                "rule_id": "rule_004",
                "text": "Match scorer check aliases",
                "failure_type": "scorer_alias_gap",
                "evidence_refs": [],
                "code_facts": [],
            },
        ]
        filtered = remove_no_evidence_business_rules(rules)
        assert len(filtered) == 1

    def test_mixed_rules_partial_removal(self):
        rules = [
            {
                "rule_id": "r1", "text": "business no evidence",
                "failure_type": "missing_business_rule",
                "evidence_refs": [], "code_facts": [],
            },
            {
                "rule_id": "r2", "text": "business with evidence",
                "failure_type": "missing_business_rule",
                "evidence_refs": ["X.java#m"], "code_facts": [],
            },
            {
                "rule_id": "r3", "text": "prompt echo",
                "failure_type": "prompt_echo",
                "evidence_refs": [], "code_facts": [],
            },
        ]
        filtered = remove_no_evidence_business_rules(rules)
        assert len(filtered) == 2
        assert any(r["rule_id"] == "r2" for r in filtered)
        assert any(r["rule_id"] == "r3" for r in filtered)
        assert not any(r["rule_id"] == "r1" for r in filtered)

    def test_empty_input(self):
        assert remove_no_evidence_business_rules([]) == []

    def test_rules_without_failure_type(self):
        rules = [
            {
                "rule_id": "r1",
                "text": "Some rule",
                "evidence_refs": [],
                "code_facts": [],
            },
        ]
        filtered = remove_no_evidence_business_rules(rules)
        assert len(filtered) == 1  # kept because no failure_type means not business


class TestRuleSupportScore:
    def test_rule_with_evidence_has_higher_score(self):
        rule_with = {"evidence_refs": ["X.java#m"], "support_count": 1, "accepted_count": 1, "regression_count": 0}
        rule_without = {"evidence_refs": [], "support_count": 1, "accepted_count": 1, "regression_count": 0}
        assert compute_rule_support_score(rule_with) > compute_rule_support_score(rule_without)

    def test_rule_with_regression_lower_score(self):
        rule_clean = {"evidence_refs": ["X.java#m"], "support_count": 2, "accepted_count": 2, "regression_count": 0}
        rule_regressed = {"evidence_refs": ["X.java#m"], "support_count": 2, "accepted_count": 2, "regression_count": 2}
        assert compute_rule_support_score(rule_clean) > compute_rule_support_score(rule_regressed)
