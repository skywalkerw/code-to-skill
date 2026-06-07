"""场景规则兜底测试。"""
from __future__ import annotations

from code_to_skill.skillopt_loop.edit_validator import filter_valid_edits
from code_to_skill.skillopt_loop.reflect_helpers import (
    RULE_SECTION_HEADING_PRIMARY,
    SCENARIO_SECTION_HEADING,
)
from code_to_skill.skillopt_loop.scenario_rules import build_scenario_edits


def test_build_scenario_edits_skips_duplicate_task_ids():
    skill = (
        "# Skill\n## Workflow\n\n"
        f"{SCENARIO_SECTION_HEADING}\n\n"
        "- **jv_purchase_001** (buy item): must satisfy verification checks [库存]"
    )
    results = [
        {"id": "jv_purchase_001", "hard": 0, "question": "buy item", "missed_checks": ["库存"]},
        {"id": "jv_loan_disburse_001", "hard": 0, "question": "disburse loan", "missed_checks": ["loan", "disburse"]},
    ]
    edits = build_scenario_edits(results, skill)
    assert len(edits) == 1
    assert "jv_loan_disburse_001" in edits[0].content
    assert "jv_purchase_001" not in edits[0].content


def test_scenario_edits_pass_validator_when_generic_rules_duplicate():
    skill = (
        "# Skill\n## Workflow\n\n"
        f"{RULE_SECTION_HEADING_PRIMARY}\n\n"
        "- Output must satisfy verification check «会计凭证»\n"
        "- Output must satisfy verification check «借»\n"
        "- Output must satisfy verification check «贷»"
    )
    results = [{
        "id": "jv_fee_001",
        "hard": 0,
        "question": "fee charge 200.00",
        "missed_checks": ["会计凭证", "借", "贷", "费用", "Charge"],
        "context_refs": ["CashBasedAccountingProcessorForLoan.java"],
    }]
    edits = build_scenario_edits(results, skill)
    valid, rejected = filter_valid_edits(edits, skill)
    assert valid
    assert not rejected
    assert "jv_fee_001" in valid[0].content
