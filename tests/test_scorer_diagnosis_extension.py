"""Target-project scorer emits failure_type diagnostics for code_diagnosis."""
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "demo-project/benchmarks/score_expected_checks.py"


def _run_scorer(predicted: str, item: dict, global_aliases: dict | None = None) -> dict:
    payload = json.dumps({
        "predicted": predicted,
        "item": item,
        "global_check_aliases": global_aliases or {},
    })
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout.strip())


def test_scorer_emits_output_format_error_for_clarification():
    result = _run_scorer(
        "请补充支付方式",
        {"expected_checks": ["会计凭证", "借"]},
    )
    assert result["hard"] == 0
    diag = result.get("diagnostics") or {}
    assert diag.get("failure_type") == "output_format_error"
    assert diag.get("suggested_rule")


def test_scorer_emits_missing_business_rule():
    result = _run_scorer(
        "generic partial answer",
        {"expected_checks": ["RequiredBusinessTerm"]},
    )
    assert result["hard"] == 0
    diag = result.get("diagnostics") or {}
    assert diag.get("failure_type") == "missing_business_rule"
    assert "RequiredBusinessTerm" in diag.get("suggested_rule", "")
