"""场景规则兜底测试。"""
from __future__ import annotations

from code_to_skill.skillopt_loop.edit_validator import filter_valid_edits
from code_to_skill.skillopt_loop.scenario_rules import build_scenario_edits


def test_build_scenario_edits_skips_duplicate_task_ids():
    skill = (
        "# Skill\n### 2.3 生成会计凭证\n\n"
        "### 场景分录规则（按 benchmark case）\n\n"
        "- **jv_purchase_001**（买入 A物品）：必须输出「## 会计凭证」"
    )
    results = [
        {"id": "jv_purchase_001", "hard": 0, "question": "买入 A物品", "missed_checks": ["库存"]},
        {"id": "jv_loan_disburse_001", "hard": 0, "question": "发放贷款 50000", "missed_checks": ["贷款", "发放"]},
    ]
    edits = build_scenario_edits(results, skill)
    assert len(edits) == 1
    assert "jv_loan_disburse_001" in edits[0].content
    assert "jv_purchase_001" not in edits[0].content


def test_scenario_edits_pass_validator_when_generic_rules_duplicate():
    skill = (
        "# Skill\n### 2.3 生成会计凭证\n\n"
        "### 分录输出要求（自动生成）\n\n"
        "- 输出必须以「## 会计凭证」为标题\n"
        "- 分录表格须包含借方行，「借贷」列标注「借」\n"
        "- 分录表格须包含贷方行，「借贷」列标注「贷」"
    )
    results = [{
        "id": "jv_fee_001",
        "hard": 0,
        "question": "收取客户逾期手续费 200.00",
        "missed_checks": ["会计凭证", "借", "贷", "费用", "Charge"],
        "context_refs": ["CashBasedAccountingProcessorForLoan.java"],
    }]
    edits = build_scenario_edits(results, skill)
    valid, rejected = filter_valid_edits(edits, skill)
    assert valid
    assert not rejected
    assert "jv_fee_001" in valid[0].content
