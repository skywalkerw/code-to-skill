"""Tests for output_hygiene (design 08 phase A)."""
from code_to_skill.skillopt_loop.output_hygiene import (
    OutputHygieneConfig,
    apply_hygiene_to_rollout_results,
    detect_output_hygiene,
)


def test_detect_prompt_echo_task_prefix():
    text = "Task: generate output\nSkill reference: foo\nCode context: bar"
    clean, reason, matched = detect_output_hygiene(text)
    assert not clean
    assert reason == "prompt_echo"
    assert matched


def test_clean_markdown_output():
    text = "## Deliverable\n\n| col_a | col_b |\n| a | b |"
    clean, reason, _ = detect_output_hygiene(text)
    assert clean
    assert reason == ""


def test_apply_hygiene_forces_hard_fail():
    cfg = OutputHygieneConfig(hard_fail_on_persistent_echo=True)
    results = [{
        "id": "x",
        "hard": 1,
        "accuracy": 1.0,
        "soft": 1.0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "passed": 1,
        "passed_checks": ["marker"],
        "expected_checks": ["marker"],
        "predicted_answer": "Task: echo",
        "fail_reason": "",
    }]
    out = apply_hygiene_to_rollout_results(results, cfg)
    assert out[0]["hard"] == 0
    assert out[0]["accuracy"] == 0.0
    assert out[0]["soft"] == 0.0
    assert out[0]["passed_checks"] == []
    assert out[0]["missed_checks"] == ["marker"]
    assert out[0]["output_hygiene_reason"] == "prompt_echo"
