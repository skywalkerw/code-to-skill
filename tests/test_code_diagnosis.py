"""Tests for code_diagnosis (design 08 phase C)."""
import json
from code_to_skill.skillopt_loop.code_diagnosis import (
    CodeDiagnosisConfig,
    CodeRetrievalConfig,
    collect_diagnosis_run_metrics,
    diagnose_failure,
    format_diagnoses_for_reflect,
)


def test_classify_prompt_echo():
    row = {
        "id": "item_1",
        "hard": 0,
        "predicted_answer": "Task: x\nSkill reference: y",
        "missed_checks": ["heading"],
        "output_hygiene_reason": "prompt_echo",
    }
    d = diagnose_failure(row, step=1)
    assert d["failure_type"] == "prompt_echo"
    assert "deliverable" in d["suggested_general_rule"]


def test_uses_scorer_diagnostics_failure_type():
    row = {
        "id": "item_2",
        "hard": 0,
        "predicted_answer": "clarification only",
        "missed_checks": ["section_heading"],
        "scorer_diagnostics": {
            "failure_type": "output_format_error",
            "suggested_rule": "Use ## Deliverable when sufficient info.",
        },
    }
    d = diagnose_failure(row, step=2)
    assert d["failure_type"] == "output_format_error"
    assert d["suggested_general_rule"] == "Use ## Deliverable when sufficient info."


def test_classify_alias_gap_via_check_aliases():
    row = {
        "id": "item_3",
        "hard": 0,
        "predicted_answer": "paid with 银行转账",
        "missed_checks": ["bank"],
        "item_check_aliases": {"bank": ["银行"]},
    }
    d = diagnose_failure(
        row,
        step=3,
        check_aliases={"bank": ["银行", "银行存款"]},
    )
    assert d["failure_type"] == "scorer_alias_gap"


def test_sort_diagnoses_prompt_echo_first():
    from code_to_skill.skillopt_loop.code_diagnosis import sort_diagnoses_for_reflect

    ordered = sort_diagnoses_for_reflect([
        {"item_id": "b", "failure_type": "missing_business_rule"},
        {"item_id": "a", "failure_type": "prompt_echo"},
    ])
    assert ordered[0]["failure_type"] == "prompt_echo"


def test_format_diagnoses_for_reflect():
    text = format_diagnoses_for_reflect([
        {
            "item_id": "a",
            "failure_type": "prompt_echo",
            "missed_checks": ["x"],
            "suggested_general_rule": "no echo",
        },
    ])
    assert "Code diagnosis" in text
    assert "a" in text


def test_only_business_failures_require_code_facts():
    row = {
        "id": "fmt",
        "hard": 0,
        "predicted_answer": "plain text",
        "missed_checks": ["table"],
        "scorer_diagnostics": {"failure_type": "output_format_error"},
    }
    d = diagnose_failure(
        row,
        step=1,
        config=CodeDiagnosisConfig(require_code_facts_for_rules=True),
    )
    assert d["status"] == "ready"


def test_business_code_fact_requirement_can_be_disabled():
    row = {
        "id": "biz",
        "hard": 0,
        "predicted_answer": "missing rule",
        "missed_checks": ["cash"],
        "scorer_diagnostics": {"failure_type": "missing_business_rule"},
    }
    d = diagnose_failure(
        row,
        step=1,
        config=CodeDiagnosisConfig(require_code_facts_for_rules=True),
        retrieval_cfg=CodeRetrievalConfig(require_code_facts_for_business_rules=False),
    )
    assert d["status"] == "ready"


def test_collect_diagnosis_run_metrics_uses_hard_failure_denominator(tmp_path):
    diag_dir = tmp_path / "code_diagnosis" / "step_0001"
    diag_dir.mkdir(parents=True)
    (diag_dir / "summary.json").write_text(
        '{"diagnosis_count": 1, "needs_review_count": 0}',
        encoding="utf-8",
    )
    (diag_dir / "code_diagnosis.jsonl").write_text(
        '{"item_id": "a", "code_facts": [{"ref": "Foo.java"}]}\n',
        encoding="utf-8",
    )
    step_dir = tmp_path / "steps" / "step_0001"
    step_dir.mkdir(parents=True)
    (step_dir / "rollout_summary.json").write_text(
        '{"failures": [{"id": "a"}, {"id": "b"}]}',
        encoding="utf-8",
    )
    metrics = collect_diagnosis_run_metrics(tmp_path)
    assert metrics["hard_failure_count"] == 2
    assert metrics["hard_failure_coverage"] == 0.5
    assert metrics["code_facts_rate"] == 1.0


def test_collect_diagnosis_run_metrics_uses_retrieval_candidate_counts(tmp_path):
    diag_dir = tmp_path / "code_diagnosis" / "step_0001"
    diag_dir.mkdir(parents=True)
    (diag_dir / "summary.json").write_text(
        '{"diagnosis_count": 2, "needs_review_count": 1}',
        encoding="utf-8",
    )
    rows = [
        {
            "item_id": "a",
            "code_facts": [
                {
                    "ref": "Foo.java#m",
                    "source": "code_retrieval:context_ref",
                    "role": "service",
                    "confidence": "0.9",
                }
            ],
            "code_retrieval_metrics": {
                "query_plan": {"case_id": "a"},
                "candidates": [{"role": "service"}, {"role": "domain"}],
                "top_role": "service",
            },
        },
        {
            "item_id": "b",
            "code_facts": [],
            "code_retrieval_metrics": {
                "query_plan": {"case_id": "b"},
                "candidates": [{"role": "handler_only"}],
                "top_role": "handler_only",
            },
        },
    ]
    (diag_dir / "code_diagnosis.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )

    metrics = collect_diagnosis_run_metrics(tmp_path)
    retrieval = metrics["code_retrieval_metrics"]
    assert retrieval["query_plan_count"] == 2
    assert retrieval["cases_with_code_facts"] == 1
    assert retrieval["avg_candidates_per_case"] == 1.5
    assert retrieval["avg_facts_per_case"] == 0.5
    assert retrieval["glue_code_top1_rate"] == 0.5
