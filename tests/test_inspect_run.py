"""inspect run 汇总与 run quality 即时计算测试。"""
from __future__ import annotations

from pathlib import Path

from code_to_skill.cli.inspect_run import (
    build_run_quality_from_artifacts,
    resolve_run_quality_report,
    summarize_run,
)


def test_build_run_quality_from_baseline_run():
    run_dir = Path("demo-project/runs/20260612-105003")
    if not (run_dir / "optimization" / "history.json").is_file():
        return
    opt = run_dir / "optimization"
    report = build_run_quality_from_artifacts(opt, run_dir.name)
    assert report is not None
    assert report["schema_version"] == "1.0"
    assert report["best_score_monotonic"] is False
    assert report["leakage_count"] > 0
    assert report["case_id_count"] > 0
    assert report["test_hard"] == 0.625
    assert any("knowledge_accept" in r for r in report.get("recommendations", []))


def test_summarize_run_includes_computed_quality():
    run_dir = Path("demo-project/runs/20260612-105003")
    if not (run_dir / "optimization" / "history.json").is_file():
        return
    lines = summarize_run(run_dir.resolve())
    joined = "\n".join(lines)
    assert "Run quality:" in joined
    assert "monotonic=✗" in joined
    assert "leakage=" in joined


def test_compare_optimization_dirs_baseline():
    run_dir = Path("demo-project/runs/20260612-105003")
    if not (run_dir / "optimization" / "history.json").is_file():
        return
    lines = __import__(
        "code_to_skill.cli.inspect_run", fromlist=["compare_optimization_dirs"]
    ).compare_optimization_dirs(run_dir, candidate="optimization-07")
    assert any("optimization vs" in ln for ln in lines)


def test_resolve_prefers_saved_report(tmp_path):
    opt = tmp_path / "optimization"
    opt.mkdir(parents=True)
    saved = {"schema_version": "1.0", "best_score_monotonic": True, "leakage_count": 0}
    with open(opt / "run_quality_report.json", "w", encoding="utf-8") as f:
        import json

        json.dump(saved, f)
    assert resolve_run_quality_report(opt, "test-run") == saved
