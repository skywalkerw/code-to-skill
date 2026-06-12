"""Tests for replay_gate (design 08 phase D)."""
from code_to_skill.skillopt_loop.replay_gate import (
    ReplayGateConfig,
    apply_replay_results_to_pool,
    filter_replay_pool,
    merge_rule_exemplars_into_pool,
    run_replay_gate,
    update_replay_pool,
)


class _StubAdapter:
    def rollout(self, skill, items, target_backend=None, out_dir=""):
        results = []
        for item in items:
            ok = "DELIVERABLE_MARKER" in skill and item.get("id") != "bad_echo"
            pred = "## DELIVERABLE_MARKER" if ok else "Task: echo"
            results.append({
                "id": item.get("id"),
                "hard": 1 if ok else 0,
                "soft": 1.0 if ok else 0.0,
                "predicted_answer": pred,
            })
        return results


class _CaptureAdapter:
    def __init__(self):
        self.items = []

    def rollout(self, skill, items, target_backend=None, out_dir=""):
        self.items = list(items)
        return [
            {
                "id": item.get("id"),
                "hard": 1,
                "soft": 1.0,
                "predicted_answer": "ok",
            }
            for item in items
        ]


def test_update_replay_pool_dedupes_by_id():
    pool = [{"id": "a", "last_step": 1, "baseline_hard": 0}]
    updated = update_replay_pool(
        pool,
        [{"id": "a", "hard": 0, "step": 2, "question": "q", "expected_checks": ["x"]}],
        max_items=5,
    )
    assert len(updated) == 1
    assert updated[0]["last_step"] == 2


def test_update_replay_pool_preserves_scorer_metadata():
    updated = update_replay_pool(
        [],
        [{
            "id": "a",
            "hard": 0,
            "step": 2,
            "question": "q",
            "expected_checks": ["x"],
            "scorer": "python_script",
            "scorer_config": {"script": "../score_expected_checks.py"},
            "_benchmark_dir": "/tmp/bench",
            "item_check_aliases": {"x": ["alias"]},
        }],
        max_items=5,
    )
    assert updated[0]["scorer"] == "python_script"
    assert updated[0]["scorer_config"]["script"] == "../score_expected_checks.py"
    assert updated[0]["_benchmark_dir"] == "/tmp/bench"
    assert updated[0]["check_aliases"] == {"x": ["alias"]}


def test_replay_gate_passes_full_item_metadata_to_adapter():
    adapter = _CaptureAdapter()
    pool = [{
        "id": "a",
        "input": "q",
        "expected_checks": ["x"],
        "scorer": "python_script",
        "scorer_config": {"script": "../score_expected_checks.py"},
        "_benchmark_dir": "/tmp/bench",
    }]
    report = run_replay_gate(
        "skill",
        pool,
        adapter=adapter,
        config=ReplayGateConfig(enabled=True),
    )
    assert report["passed"]
    assert adapter.items[0]["scorer"] == "python_script"
    assert adapter.items[0]["scorer_config"]["script"] == "../score_expected_checks.py"
    assert adapter.items[0]["_benchmark_dir"] == "/tmp/bench"


def test_rule_exemplars_skip_unresolved_ids():
    merged = merge_rule_exemplars_into_pool(
        [],
        [{"rule_id": "r1", "source_items": ["missing"]}],
        max_items=5,
    )
    assert merged == []


def test_rule_exemplars_use_item_registry_when_available():
    merged = merge_rule_exemplars_into_pool(
        [],
        [{"rule_id": "r1", "source_items": ["a"]}],
        max_items=5,
        item_registry={
            "a": {
                "id": "a",
                "question": "q",
                "expected_checks": ["x"],
                "scorer": "python_script",
                "scorer_config": {"script": "../score_expected_checks.py"},
            }
        },
    )
    assert len(merged) == 1
    assert merged[0]["question"] == "q"
    assert merged[0]["scorer"] == "python_script"
    assert merged[0]["from_rule_id"] == "r1"


def test_replay_gate_rejects_prompt_echo():
    from code_to_skill.skillopt_loop.output_hygiene import OutputHygieneConfig

    cfg = ReplayGateConfig(enabled=True, min_hard_pass_rate=1.0, reject_on_prompt_echo=True)
    hygiene = OutputHygieneConfig(enabled=True, hard_fail_on_persistent_echo=True)
    pool = [{"id": "bad_echo", "input": "q", "expected_checks": ["marker"], "baseline_hard": 0}]
    report = run_replay_gate(
        "thin skill",
        pool,
        adapter=_StubAdapter(),
        hygiene_cfg=hygiene,
        config=cfg,
        step=3,
        candidate_hash="abc",
    )
    assert not report["passed"]
    assert report["prompt_echo_count"] >= 1
    assert report["step"] == 3
    assert "bad_echo" in report["prompt_echo_ids"]


def test_replay_gate_detects_regression():
    cfg = ReplayGateConfig(
        enabled=True,
        min_hard_pass_rate=0.0,
        reject_on_regression=True,
        reject_on_prompt_echo=False,
    )
    pool = [{"id": "was_ok", "question": "q", "expected_checks": [], "baseline_hard": 1}]
    report = run_replay_gate(
        "thin skill",
        pool,
        adapter=_StubAdapter(),
        config=cfg,
        step=2,
    )
    assert not report["passed"]
    assert report["reason"] == "replay_regression"
    assert "was_ok" in report["regressed_ids"]


def test_filter_replay_pool_excludes_prompt_echo_when_disabled():
    pool = [
        {"id": "echo", "prompt_echo": True},
        {"id": "normal", "prompt_echo": False},
    ]
    cfg = ReplayGateConfig(include_prompt_echo_cases=False, pool_max_items=5)
    out = filter_replay_pool(cfg, pool)
    assert len(out) == 1
    assert out[0]["id"] == "normal"


def test_replay_on_regression_accept_current_reason():
    cfg = ReplayGateConfig(
        enabled=True,
        min_hard_pass_rate=0.0,
        reject_on_regression=True,
        on_regression="accept_current",
        reject_on_prompt_echo=False,
    )
    pool = [{"id": "was_ok", "question": "q", "baseline_hard": 1}]
    report = run_replay_gate("thin", pool, adapter=_StubAdapter(), config=cfg)
    assert report["reason"] == "replay_regression_accept_current"


def test_apply_replay_results_marks_fixed():
    pool = [{"id": "x", "baseline_hard": 0}]
    results = [{"id": "x", "hard": 1, "soft": 1.0}]
    updated, regressed, fixed = apply_replay_results_to_pool(pool, results, step=1)
    assert fixed == ["x"]
    assert not regressed
    assert updated[0]["baseline_hard"] == 1
