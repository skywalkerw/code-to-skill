"""Skill quality gate — leakage, size, duplicate, sanitizer tests."""
from __future__ import annotations

from code_to_skill.skillopt_loop.edit_validator import filter_valid_edits, validate_edit
from code_to_skill.skillopt_loop.skill_quality import (
    QualityGateConfig,
    build_skill_quality_report,
    edit_has_leakage,
    sanitize_skill,
    scan_skill_quality,
)
from code_to_skill.skillopt_loop.types import EditOp


def test_scan_detects_scorer_leakage():
    cfg = QualityGateConfig()
    skill = "# Skill\n- cover verified checks for jv_purchase_001\n"
    scan = scan_skill_quality(skill, cfg)
    assert scan["leakage_count"] >= 1
    assert scan["passed"] is False


def test_benchmark_id_patterns_project_only():
    cfg = QualityGateConfig(benchmark_id_patterns=[r"\bjv_[a-z0-9_]+\b"])
    skill = "# Skill\n- rule about jv_fee_001\n"
    scan = scan_skill_quality(skill, cfg)
    assert scan["case_id_count"] == 1
    assert scan["passed"] is False

    cfg_default = QualityGateConfig()
    scan_default = scan_skill_quality(skill, cfg_default)
    assert scan_default["case_id_count"] == 0


def test_sanitize_removes_leakage_lines():
    cfg = QualityGateConfig()
    dirty = (
        "# Skill\n"
        "- good business rule about inventory\n"
        "- cover verified checks [库存]\n"
        "- another good rule\n"
    )
    cleaned, actions = sanitize_skill(dirty, cfg)
    assert "cover verified checks" not in cleaned
    assert "good business rule" in cleaned
    assert actions


def test_edit_validator_rejects_leakage():
    cfg = QualityGateConfig(reject_on_leakage=True)
    edit = EditOp(
        op="append",
        content="- must satisfy verification checks for benchmark case jv_x",
    )
    ok, reason = validate_edit(edit, "# Skill\n", quality_config=cfg)
    assert not ok
    assert reason.startswith("leakage:")


def test_duplicate_rule_detection():
    cfg = QualityGateConfig(max_rules=100)
    skill = "# Skill\n- rule A\n- rule A\n- rule B\n"
    scan = scan_skill_quality(skill, cfg)
    assert scan["duplicate_rule_count"] == 1
    assert scan["passed"] is False


def test_build_report_schema():
    cfg = QualityGateConfig(max_skill_tokens=10)
    long_skill = "- " + ("x" * 200)
    report = build_skill_quality_report(long_skill, cfg, step=3, skill_hash="abc")
    assert report["schema_version"] == "1.0"
    assert report["step"] == 3
    assert report["estimated_tokens"] > cfg.max_skill_tokens
    assert report["passed"] is False


def test_filter_valid_edits_with_quality_config():
    cfg = QualityGateConfig(reject_on_leakage=True)
    edits = [
        EditOp(op="append", content="- 输出必须包含关键词「库存」和「银行」"),
        EditOp(op="append", content="- cover verified checks [库存]"),
    ]
    valid, rejected = filter_valid_edits(edits, "# Skill\n", quality_config=cfg)
    assert len(valid) == 1
    assert len(rejected) == 1


def test_edit_has_leakage_generic_only():
    leaked, reason = edit_has_leakage("refer to expected_checks in output")
    assert leaked
    assert "expected_checks" in reason

    leaked2, _ = edit_has_leakage("jv_purchase_001 rule", QualityGateConfig())
    assert not leaked2
