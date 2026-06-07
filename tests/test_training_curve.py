"""训练曲线记录测试。"""
from __future__ import annotations

import json
import os

from code_to_skill.skillopt_loop.training_curve import (
    CURVE_CSV,
    CURVE_JSON,
    CURVE_JSONL,
    CURVE_SVG,
    TrainingCurveRecorder,
    plot_training_curve,
)


def test_recorder_writes_jsonl_json_and_csv(tmp_path):
    out = str(tmp_path / "opt")
    rec = TrainingCurveRecorder(out, gate_metric="hard")
    rec.record_init(selection_hard=0.9, selection_soft=0.85, selection_gate=0.9)
    rec.record_gate(
        step=1,
        epoch=1,
        rollout_results=[
            {"hard": 1, "soft": 1.0},
            {"hard": 0, "soft": 0.5},
        ],
        selection_hard=1.0,
        selection_soft=0.95,
        selection_gate=1.0,
        best_score=1.0,
        current_score=1.0,
        gate_action="accept_new_best",
        gate_reason="new_best",
        edit_count=2,
        patch_count=1,
    )
    rec.record_skip(
        step=2,
        epoch=2,
        reason="no_valid_edits",
        rollout_results=[{"hard": 1, "soft": 1.0}],
        patch_count=0,
    )
    payload = rec.finalize(best_step=1)

    assert os.path.isfile(os.path.join(out, CURVE_JSONL))
    assert os.path.isfile(os.path.join(out, CURVE_JSON))
    assert os.path.isfile(os.path.join(out, CURVE_CSV))
    assert payload["summary"]["gate_accepts"] == 1
    assert payload["summary"]["skip_steps"] == 1
    assert len(payload["series"]["train_rollout_hard"]) >= 1
    assert len(payload["series"]["selection_gate"]) >= 2

    with open(os.path.join(out, CURVE_JSON), encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["gate_metric"] == "hard"
    assert saved["points"][0]["event"] == "init"


def test_recorder_resume_appends(tmp_path):
    out = str(tmp_path / "opt")
    rec1 = TrainingCurveRecorder(out, gate_metric="soft")
    rec1.record_init(selection_hard=0.5, selection_soft=0.5, selection_gate=0.5)
    rec1.finalize(best_step=0)

    rec2 = TrainingCurveRecorder(out, gate_metric="soft", resume=True)
    rec2.record_test(test_score=0.9, test_hard=0.88, n_items=3, best_score=0.9)
    payload = rec2.finalize(best_step=0)

    assert payload["summary"]["total_points"] == 2
    assert payload["points"][-1]["event"] == "test"


def test_backfill_from_run_artifacts(tmp_path):
    run_root = tmp_path / "run"
    opt = run_root / "optimization"
    logs = run_root / "logs"
    opt.mkdir(parents=True)
    logs.mkdir(parents=True)

    (opt / "config.json").write_text(
        json.dumps({"gate_metric": "hard"}), encoding="utf-8",
    )
    (opt / "runtime_state.json").write_text(
        json.dumps({"best_step": 2}), encoding="utf-8",
    )
    (opt / "history.json").write_text(
        json.dumps([{
            "step": 2, "epoch": 2, "selection_score": 1.0,
            "gate_action": "accept_new_best", "edit_count": 1,
        }]),
        encoding="utf-8",
    )
    step_dir = opt / "steps" / "step_0002"
    step_dir.mkdir(parents=True)
    (step_dir / "rollout_summary.json").write_text(
        json.dumps({"step": 2, "total": 2, "passed": 1, "failed": 1}),
        encoding="utf-8",
    )
    (step_dir / "reflect_patches.json").write_text("[]", encoding="utf-8")
    (step_dir / "eval_results.json").write_text(
        json.dumps({"hard": 1.0, "soft": 1.0, "action": "accept_new_best"}),
        encoding="utf-8",
    )

    log = "\n".join([
        "2026-06-07 10:00:00  INFO  [M4] 初始评分: hard=0.500 soft=0.600 acc=0.500 f1=0.1 (gate=0.500 [hard])",
        "2026-06-07 10:00:01  INFO  [M4] === Epoch 2/5 ===",
        "2026-06-07 10:00:02  INFO  [M4] step=2 rollout: avg=0.80 passed=1 failed=1 (from 1 accumulated batches)",
        "2026-06-07 10:00:03  INFO  [M4] evaluate: hard=1.000 soft=1.000 acc=1.000 f1=0.2 (gate=1.000, best=0.500 [hard])",
        "2026-06-07 10:00:04  INFO  [M4] gate: ⭐ reason=new_best (0.500 → 1.000) [hard]",
        "2026-06-07 10:00:05  INFO  [M4] slow update gate: ✗ (no_improvement (1.000 ≤ 1.000) [hard])",
        "2026-06-07 10:00:06  INFO  [SlowUpdate] Comparison: improved=1 regressed=0 persistent_fail=0 stable_success=1",
        "2026-06-07 10:00:07  INFO  [M4] === Meta Skill epoch 2 ===",
    ])
    (logs / "run.log").write_text(log, encoding="utf-8")

    from code_to_skill.skillopt_loop.training_curve import backfill_training_curve_from_run

    payload = backfill_training_curve_from_run(str(opt))
    events = [p["event"] for p in payload["points"]]
    assert events == ["init", "gate", "epoch_end"]
    assert payload["points"][0]["selection_gate"] == 0.5
    assert payload["points"][1]["gate_action"] == "accept_new_best"
    assert payload["points"][2]["slow_update_gate"] == "reject"
    assert payload["backfilled"] is True
    assert os.path.isfile(opt / CURVE_JSON)
    assert os.path.isfile(opt / CURVE_CSV)
    assert os.path.isfile(opt / CURVE_JSONL)


def test_plot_training_curve_writes_svg(tmp_path):
    out = str(tmp_path / "opt")
    rec = TrainingCurveRecorder(out, gate_metric="hard")
    rec.record_init(selection_hard=0.5, selection_soft=0.6, selection_gate=0.5)
    rec.record_gate(
        step=1,
        epoch=1,
        rollout_results=[{"hard": 1, "soft": 1.0}],
        selection_hard=1.0,
        selection_soft=1.0,
        selection_gate=1.0,
        best_score=1.0,
        current_score=1.0,
        gate_action="accept_new_best",
        gate_reason="new_best",
        edit_count=1,
        patch_count=1,
    )
    rec.finalize(best_step=1)

    svg_path = plot_training_curve(out, title="Test Curve")
    assert svg_path.endswith(CURVE_SVG)
    content = open(svg_path, encoding="utf-8").read()
    assert "<svg" in content
    assert "selection (hard)" in content
    assert "train rollout (hard)" in content
