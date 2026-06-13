"""``inspect run <run_id>`` 汇总视图。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _quality_config_for_inspect() -> Any:
    """Load quality_gate from project config."""
    from code_to_skill.skillopt_loop.skill_quality import QualityGateConfig

    cfg_path = os.environ.get("SKILL_LAB_CONFIG_PATH", "config.yaml")
    try:
        from code_to_skill.cli.config_loader import load_config

        app = load_config(cfg_path)
        skillopt = app.settings.skillopt or {}
        se = skillopt.get("self_evolution") or {}
        edits = se.get("edits") or {}
        hygiene = se.get("hygiene") or {}
        return QualityGateConfig.from_skillopt_settings(
            skillopt,
            se_max_skill_tokens=int(edits.get("max_skill_tokens", 2000) or 2000),
            se_max_rules=int(hygiene.get("max_rules", 40) or 40),
        )
    except (OSError, ValueError, TypeError, ImportError):
        return QualityGateConfig()


def build_run_quality_from_artifacts(
    opt_dir: Path,
    run_id: str,
    *,
    quality_config: Any | None = None,
) -> dict | None:
    """从已有 optimization 产物即时计算 run quality（旧 run 无 report 时）。"""
    history = _read_json(opt_dir / "history.json")
    if not isinstance(history, list) or not history:
        return None
    best_path = opt_dir / "best_skill.md"
    if not best_path.is_file():
        return None

    from code_to_skill.skillopt_loop.skill_quality import build_run_quality_report

    best_skill = best_path.read_text(encoding="utf-8")
    runtime_cfg = _read_json(opt_dir / "config.json")
    initial_chars = 0
    if isinstance(runtime_cfg, dict):
        initial_chars = int(runtime_cfg.get("initial_skill_chars", 0) or 0)
    initial_skill = " " * initial_chars if initial_chars > 0 else ""

    test_report = _read_json(opt_dir / "test_report.json")
    if not isinstance(test_report, dict):
        test_report = {}
    report_path = str(test_report.get("report_path") or "")
    resolved_report = Path(report_path) if report_path else None
    if resolved_report and not resolved_report.is_file():
        for candidate in (
            Path.cwd() / report_path if report_path else None,
            opt_dir / "final_eval" / "test_eval_report.json",
        ):
            if candidate and candidate.is_file():
                test_report = {**test_report, "report_path": str(candidate)}
                break
    elif resolved_report and not resolved_report.is_absolute() and resolved_report.is_file():
        test_report = {**test_report, "report_path": str(resolved_report.resolve())}

    best_score = float(history[-1].get("best_score", 0) or 0)
    qcfg = quality_config or _quality_config_for_inspect()
    return build_run_quality_report(
        run_id=run_id,
        initial_skill=initial_skill,
        best_skill=best_skill,
        best_score=best_score,
        history=history,
        test_report=test_report or None,
        quality_config=qcfg,
    )


def resolve_run_quality_report(opt_dir: Path, run_id: str) -> dict | None:
    """读取或即时计算 run_quality_report.json。"""
    saved = _read_json(opt_dir / "run_quality_report.json")
    if isinstance(saved, dict):
        return saved
    return build_run_quality_from_artifacts(opt_dir, run_id)


def _selected_best_score(
    run_quality: dict | None,
    runtime_state: dict | None,
    history: list | None,
    manifest_summary: dict | None = None,
) -> float | None:
    """Best score for the selected optimization directory.

    ``run_manifest.json`` belongs to the original run directory, so alternate
    optimization dirs such as ``optimization-07`` must prefer their local
    artifacts.
    """
    for source in (run_quality, runtime_state):
        if isinstance(source, dict) and source.get("best_score") is not None:
            return float(source.get("best_score") or 0.0)
    if isinstance(history, list) and history:
        last = history[-1]
        if isinstance(last, dict) and last.get("best_score") is not None:
            return float(last.get("best_score") or 0.0)
    if isinstance(manifest_summary, dict) and manifest_summary.get("best_score") is not None:
        return float(manifest_summary.get("best_score") or 0.0)
    return None


def summarize_run(
    run_dir: Path,
    *,
    optimization_dir: str = "optimization",
    trace_pool: bool = False,
    rule_attribution: bool = False,
    frontier: bool = False,
    validate_self_evolution: bool = False,
    show_diagnosis: bool = False,
) -> list[str]:
    """生成 run 目录的人类可读摘要行。"""
    lines: list[str] = []
    opt = run_dir / optimization_dir
    if not opt.is_dir():
        opt = run_dir / "optimization"
    lines.append(f"Run: {run_dir.name}")
    lines.append(f"Path: {run_dir}")
    lines.append(f"Optimization: {opt.name}")
    history = _read_json(opt / "history.json")
    runtime_state = _read_json(opt / "runtime_state.json")
    run_quality = resolve_run_quality_report(opt, run_dir.name)

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
        best_score = _selected_best_score(
            run_quality if isinstance(run_quality, dict) else None,
            runtime_state if isinstance(runtime_state, dict) else None,
            history if isinstance(history, list) else None,
            summary if isinstance(summary, dict) else None,
        )
        if best_score is not None:
            lines.append(f"Best score: {best_score:.3f}")

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

    if show_diagnosis:
        diag_root = opt / "code_diagnosis"
        if diag_root.is_dir():
            diag_files = sorted(diag_root.glob("step_*/code_diagnosis.jsonl"))
            summary_files = sorted(diag_root.glob("step_*/summary.json"))
            lines.append(f"Code diagnosis: {len(diag_files)} steps")
            if summary_files:
                last_sum = _read_json(summary_files[-1])
                if isinstance(last_sum, dict):
                    lines.append(
                        f"  last summary: n={last_sum.get('diagnosis_count', 0)} "
                        f"candidate_rules={last_sum.get('candidate_rule_count', 0)} "
                        f"needs_review={last_sum.get('needs_review_count', 0)}"
                    )
            if diag_files:
                last = diag_files[-1]
                rows = []
                with open(last, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                rows.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                for row in rows[:5]:
                    if isinstance(row, dict):
                        cause = str(row.get("failure_cause") or row.get("general_rule") or "")[:80]
                        lines.append(
                            f"  {row.get('item_id', '?')}: "
                            f"type={row.get('failure_type')} "
                            f"status={row.get('status', '?')}"
                        )
                        if cause:
                            lines.append(f"    cause: {cause}")
        replay_pool = _read_json(opt / "replay_pool.json")
        if isinstance(replay_pool, dict):
            items = replay_pool.get("items") or []
            lines.append(f"Replay pool: {len(items)} items")
        steps_dir = opt / "steps"
        hygiene_files = (
            sorted(steps_dir.glob("step_*/output_hygiene_report.json"))
            if steps_dir.is_dir()
            else []
        )
        if hygiene_files:
            last_h = _read_json(hygiene_files[-1])
            if isinstance(last_h, dict):
                lines.append(
                    f"Output hygiene (last step): echo={last_h.get('prompt_echo_count', 0)} "
                    f"tool={last_h.get('tool_residue_count', 0)}"
                )
        replay_files = (
            sorted(steps_dir.glob("step_*/replay_eval_report.json"))
            if steps_dir.is_dir()
            else []
        )
        if replay_files:
            last_r = _read_json(replay_files[-1])
            if isinstance(last_r, dict):
                flag = "✓" if last_r.get("passed") else "✗"
                lines.append(
                    f"Replay gate (last step): {flag} "
                    f"hard={last_r.get('hard', last_r.get('hard_pass_rate', 0)):.2f} "
                    f"fixed={len(last_r.get('fixed_ids') or [])} "
                    f"regressed={len(last_r.get('regressed_ids') or [])} "
                    f"reason={last_r.get('reason', '?')}"
                )
        # 设计 09 代码检索指标
        retrieval_root = opt / "code_retrieval"
        if retrieval_root.is_dir():
            retrieval_summaries = sorted(retrieval_root.glob("step_*/summary.json"))
            if retrieval_summaries:
                last_rs = _read_json(retrieval_summaries[-1])
                if isinstance(last_rs, dict):
                    lines.append(
                        f"Code retrieval (last step): "
                        f"cases={last_rs.get('cases', 0)} "
                        f"facts={last_rs.get('facts', 0)} "
                        f"rate={last_rs.get('code_facts_rate', 0):.2f} "
                        f"glue_top1={last_rs.get('downranked_glue_top1', 0)} "
                        f"sources={last_rs.get('top_sources', {})}"
                    )
        rb_path = None
        try:
            from code_to_skill.cli.config_loader import load_config

            cfg_path = os.environ.get("SKILL_LAB_CONFIG_PATH", "config.yaml")
            app = load_config(cfg_path)
            rb = (app.settings.skillopt or {}).get("rule_bank") or {}
            rb_path = rb.get("path")
        except (OSError, ValueError, TypeError, ImportError):
            rb_path = None
        if rb_path:
            from code_to_skill.skillopt_loop.rule_bank import (
                RuleBankConfig,
                load_rules,
                select_active_rules,
            )

            rules = load_rules(rb_path)
            if rules:
                rb_cfg = RuleBankConfig(enabled=True, path=str(rb_path))
                active = select_active_rules(rules, rb_cfg)
                lines.append(f"Rule bank: {len(active)} active / {len(rules)} total")

    if isinstance(run_quality, dict):
        dm = run_quality.get("diagnosis_metrics") or {}
        if dm:
            lines.append(
                f"Diagnosis metrics: steps={dm.get('diagnosis_steps', 0)} "
                f"code_facts_rate={dm.get('code_facts_rate', 0):.2f} "
                f"needs_review={dm.get('needs_review_count', 0)}"
            )
        # 设计 09 代码检索指标
        crm = run_quality.get("code_retrieval_metrics") or {}
        if crm:
            lines.append(
                f"Code retrieval: facts_rate={crm.get('code_facts_rate', 0):.2f} "
                f"glue_top1_rate={crm.get('glue_code_top1_rate', 0):.2f} "
                f"avg_cands={crm.get('avg_candidates_per_case', 0)} "
                f"avg_facts={crm.get('avg_facts_per_case', 0)}"
            )
        if run_quality.get("replay_hard"):
            lines.append(
                f"Replay hard: {run_quality.get('replay_hard', 0):.3f} "
                f"regressed={run_quality.get('replay_regressed_ids', [])}"
            )
        mono = run_quality.get("best_score_monotonic")
        mono_flag = "✓" if mono else "✗"
        lines.append(
            f"Run quality: monotonic={mono_flag} "
            f"leakage={run_quality.get('leakage_count', 0)} "
            f"case_ids={run_quality.get('case_id_count', 0)}"
        )
        recs = run_quality.get("recommendations") or []
        for rec in recs[:3]:
            lines.append(f"  → {rec}")
        failures = run_quality.get("hard_failures") or []
        if failures:
            lines.append(f"  hard failures: {len(failures)}")
            for row in failures[:5]:
                if isinstance(row, dict):
                    lines.append(
                        f"    {row.get('id', '?')}: missed={row.get('missed_checks', [])}"
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

    atoms_dir = run_dir / "atoms"
    aq = _read_json(atoms_dir / "artifact_quality.json")
    if isinstance(aq, dict):
        flag = "✓" if aq.get("passed") else "✗"
        lines.append(
            f"M3 artifact_quality: {flag} seeds={aq.get('seeds_total', 0)} "
            f"resolve_rate={aq.get('source_ref_resolve_rate', 0):.2f}"
        )
        if aq.get("failures"):
            lines.append(f"  failures: {', '.join(aq['failures'])}")

    if trace_pool:
        tp = opt / "trace_pool"
        traces = tp / "traces.jsonl"
        clusters = _read_json(tp / "clusters.json")
        if traces.is_file():
            n = sum(1 for _ in open(traces, encoding="utf-8"))
            lines.append(f"Trace pool: {n} traces")
        if isinstance(clusters, dict):
            summary = clusters.get("summary") or {}
            lines.append(
                f"  clusters={summary.get('clusters', 0)} "
                f"failures={summary.get('failure_traces', 0)}"
            )
        prop_q = _read_json(tp.parent / "proposals" / "proposal_quality.json")
        if isinstance(prop_q, dict):
            lines.append(
                f"  proposals: ready={prop_q.get('ready_count', 0)} "
                f"avg_support={prop_q.get('avg_support_count', 0):.1f}"
            )

    if rule_attribution:
        attr = _read_json(opt / "rule_attribution.json")
        if isinstance(attr, dict):
            rules = attr.get("rules") or {}
            lines.append(f"Rule attribution: {len(rules)} rules")
            for rid, entry in list(rules.items())[:5]:
                if isinstance(entry, dict):
                    lines.append(
                        f"  {rid}: used={entry.get('rule_used_count', 0)} "
                        f"regressions={entry.get('rule_regression_count', 0)}"
                    )

    rej_buf = opt / "rejected_edit_buffer.jsonl"
    if rej_buf.is_file():
        n_rej = sum(1 for _ in open(rej_buf, encoding="utf-8"))
        lines.append(f"Rejected edit buffer: {n_rej} entries")

    if frontier:
        fdata = _read_json(opt / "frontier" / "frontier.json")
        if isinstance(fdata, dict):
            entries = fdata.get("entries") or []
            lines.append(f"Frontier pool: {len(entries)}/{fdata.get('max_size', '?')}")
            for e in entries[:5]:
                if isinstance(e, dict):
                    lines.append(
                        f"  step {e.get('step', '?')}: score={e.get('score', 0):.3f} "
                        f"hash={(e.get('skill_hash') or '')[:8]}"
                    )

    if validate_self_evolution:
        from code_to_skill.skillopt_loop.self_evolution_validate import (
            validate_self_evolution_run,
        )
        report = validate_self_evolution_run(opt)
        flag = "PASS" if report.get("passed") else "FAIL"
        lines.append(f"Self-evolution validation: {flag}")
        for chk in report.get("checks") or []:
            if not isinstance(chk, dict):
                continue
            mark = "✓" if chk.get("ok") else "✗"
            detail = chk.get("detail", "")
            lines.append(f"  {mark} {chk.get('name', '?')}: {detail}")

    return lines


def promote_rules_to_bank_from_run(
    run_dir: Path,
    *,
    optimization_dir: str = "optimization",
    rule_bank_path: str = "",
) -> list[str]:
    """从 run 的 best_skill.md 提升规则到 rule bank。"""
    opt = run_dir / optimization_dir
    best = opt / "best_skill.md"
    lines: list[str] = []
    if not best.is_file():
        lines.append(f"❌ 未找到 {best}")
        return lines
    if not rule_bank_path.strip():
        lines.append("❌ rule_bank.path 未配置")
        return lines
    from code_to_skill.skillopt_loop.rule_bank import promote_rules_from_skill

    skill = best.read_text(encoding="utf-8")
    source_run = f"{run_dir.name}/{optimization_dir}"
    promoted = promote_rules_from_skill(
        skill,
        source_run=source_run,
        path=rule_bank_path,
    )
    lines.append(f"Promoted {len(promoted)} rules → {rule_bank_path}")
    for row in promoted:
        lines.append(f"  + {row.get('rule_id')}: {str(row.get('text', ''))[:60]}")
    return lines


def compare_optimization_dirs(
    run_dir: Path,
    *,
    baseline: str = "optimization",
    candidate: str = "optimization-07",
) -> list[str]:
    """对比同一 run 下两次 optimization 产物质量。"""
    lines: list[str] = [f"Compare {baseline} vs {candidate} ({run_dir.name})"]
    for label, sub in ((baseline, baseline), (candidate, candidate)):
        opt = run_dir / sub
        if not opt.is_dir():
            lines.append(f"  {label}: (missing)")
            continue
        rq = resolve_run_quality_report(opt, run_dir.name)
        if not isinstance(rq, dict):
            lines.append(f"  {label}: (no quality data)")
            continue
        mono = "✓" if rq.get("best_score_monotonic") else "✗"
        lines.append(
            f"  {label}: best={rq.get('best_score', 0):.3f} "
            f"test_hard={rq.get('test_hard', 0):.3f} "
            f"test_soft={rq.get('test_soft', 0):.3f} "
            f"monotonic={mono} "
            f"leakage={rq.get('leakage_count', 0)} "
            f"case_ids={rq.get('case_id_count', 0)} "
            f"chars={rq.get('best_skill_chars', 0)}"
        )
    return lines
