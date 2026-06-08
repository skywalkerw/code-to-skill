"""``inspect run <run_id>`` 汇总视图。"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _read_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def summarize_run(run_dir: Path) -> list[str]:
    """生成 run 目录的人类可读摘要行。"""
    lines: list[str] = []
    opt = run_dir / "optimization"
    lines.append(f"Run: {run_dir.name}")
    lines.append(f"Path: {run_dir}")

    manifest = _read_json(run_dir / "run_manifest.json")
    if isinstance(manifest, dict):
        lines.append(f"Status: {manifest.get('status', '?')} ({manifest.get('duration_sec', 0):.1f}s)")
        eff = manifest.get("effective_settings") or manifest.get("summary", {}).get("effective_settings")
        if isinstance(eff, dict) and eff.get("wired"):
            m4 = eff["wired"].get("m4") or {}
            if m4.get("reflect_prompts_error"):
                lines.append("Reflect: custom error prompt ✓")
            if m4.get("judge_backend") and m4.get("judge_backend") != "(none)":
                lines.append(f"Judge backend: {m4['judge_backend']}")
        for phase in manifest.get("phases", []):
            if not isinstance(phase, dict):
                continue
            name = phase.get("phase", "?")
            status = phase.get("status", "?")
            dur = phase.get("duration_sec", 0)
            reason = phase.get("skip_reason", "")
            extra = f" — {reason}" if reason else ""
            lines.append(f"  {name}: {status} ({dur:.1f}s){extra}")
        summary = manifest.get("summary") or {}
        if summary.get("best_score") is not None:
            lines.append(f"Best score: {summary['best_score']:.3f}")

    history = _read_json(opt / "history.json")
    if isinstance(history, list) and history:
        last = history[-1]
        lines.append(
            f"Last gate: step={last.get('step')} "
            f"score={last.get('selection_score', 0):.3f} "
            f"action={last.get('gate_action', '?')}"
        )
        recent = history[-5:]
        if len(recent) > 1:
            lines.append("Gate history (last 5):")
            for row in recent:
                lines.append(
                    f"  step {row.get('step', '?')}: "
                    f"score={row.get('selection_score', 0):.3f} "
                    f"action={row.get('gate_action', '?')}"
                )

    test_report = _read_json(opt / "test_report.json")
    if isinstance(test_report, dict):
        lines.append(
            f"Test: hard={test_report.get('test_hard', 0):.3f} "
            f"soft={test_report.get('test_score', 0):.3f} "
            f"n={test_report.get('n_items', 0)}"
        )

    ref_report = _read_json(opt / "context_ref_report.json")
    if isinstance(ref_report, dict):
        s = ref_report.get("summary") or {}
        lines.append(
            f"Context refs: {s.get('resolved', 0)}/{s.get('total_refs', 0)} "
            f"({100 * s.get('resolve_rate', 0):.0f}%)"
        )

    contract = _read_json(opt / "artifact_contract.json")
    if isinstance(contract, dict):
        graphs = contract.get("graphs") or []
        if graphs and isinstance(graphs[0], dict):
            g0 = graphs[0]
            for key in ("graph_db", "entrypoints", "role_index"):
                ref = g0.get(key) or {}
                if isinstance(ref, dict) and ref.get("present"):
                    lines.append(f"  {key}: ✓")

    curve = _read_json(opt / "training_curve.json")
    if isinstance(curve, dict):
        pts = curve.get("points") or []
        summary = curve.get("summary") or {}
        lines.append(f"Training curve: {len(pts)} points")
        if summary.get("best_step"):
            lines.append(f"  best_step={summary['best_step']}")

    for curve_path in (opt / "training_curve.svg", opt / "training_curve.json"):
        if curve_path.is_file():
            lines.append(f"Curve: {curve_path}")

    steps_dir = opt / "steps"
    if steps_dir.is_dir():
        metrics_files = sorted(steps_dir.glob("step_*/metrics.json"))
        if metrics_files:
            with open(metrics_files[-1], encoding="utf-8") as f:
                m = json.load(f)
            ev = m.get("code_evidence") or {}
            refl = m.get("reflect") or {}
            lines.append(
                f"Last reflect: hits={ev.get('evidence_hits', 0)} "
                f"precise={ev.get('precise_hits', 0)} "
                f"fallback_q={ev.get('fallback_queries', 0)} "
                f"custom_prompt={refl.get('custom_reflect_prompt', False)} "
                f"scenario_rules={refl.get('scenario_rules_triggered', 0)}"
            )

    best_skill = opt / "best_skill.md"
    if best_skill.is_file():
        lines.append(f"best_skill.md: {best_skill.stat().st_size} bytes")

    return lines
