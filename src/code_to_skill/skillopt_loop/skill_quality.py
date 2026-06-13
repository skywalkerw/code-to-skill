"""Skill 内容质量扫描、sanitizer 与报告生成。"""
from __future__ import annotations

import re
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

DEFAULT_LEAKAGE_PATTERNS: list[str] = [
    "expected_checks",
    "verified checks",
    "cover verified checks",
    "benchmark case",
    "by benchmark case",
    "scorer",
    "校验程序",
    "评分器",
    "must satisfy verification checks",
]

_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.M)
_TABLE_ROW_RE = re.compile(r"^\s*\|", re.M)


@dataclass
class QualityGateConfig:
    enabled: bool = True
    run_before_selection_eval: bool = False
    run_after_selection_eval: bool = True
    reject_on_leakage: bool = True
    sanitize_then_reevaluate: bool = True
    max_skill_tokens: int = 2000
    max_rules: int = 40
    leakage_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_LEAKAGE_PATTERNS))
    benchmark_id_patterns: list[str] = field(default_factory=list)
    write_selection_eval_report: bool = True
    write_skill_quality_report: bool = True
    write_gate_decision_report: bool = True
    write_run_quality_report: bool = True

    @classmethod
    def from_skillopt_settings(
        cls,
        skillopt_settings: dict[str, Any] | None,
        *,
        se_max_skill_tokens: int = 2000,
        se_max_rules: int = 40,
    ) -> "QualityGateConfig":
        raw = skillopt_settings or {}
        qg = raw.get("quality_gate") or {}
        obs = raw.get("observability") or {}
        leakage = qg.get("leakage_patterns")
        bench_ids = qg.get("benchmark_id_patterns")
        return cls(
            enabled=bool(qg.get("enabled", True)),
            run_before_selection_eval=bool(qg.get("run_before_selection_eval", False)),
            run_after_selection_eval=bool(qg.get("run_after_selection_eval", True)),
            reject_on_leakage=bool(qg.get("reject_on_leakage", True)),
            sanitize_then_reevaluate=bool(qg.get("sanitize_then_reevaluate", True)),
            max_skill_tokens=int(
                qg.get("max_skill_tokens", se_max_skill_tokens) or se_max_skill_tokens
            ),
            max_rules=int(qg.get("max_rules", se_max_rules) or se_max_rules),
            leakage_patterns=list(leakage) if leakage else list(DEFAULT_LEAKAGE_PATTERNS),
            benchmark_id_patterns=[str(p) for p in (bench_ids or []) if str(p).strip()],
            write_selection_eval_report=bool(obs.get("write_selection_eval_report", True)),
            write_skill_quality_report=bool(obs.get("write_skill_quality_report", True)),
            write_gate_decision_report=bool(obs.get("write_gate_decision_report", True)),
            write_run_quality_report=bool(obs.get("write_run_quality_report", True)),
        )


@dataclass
class SkillGateConfig:
    strict_best_monotonic: bool = True
    knowledge_updates_current_only: bool = True
    export_current_on_tie: bool = False

    @classmethod
    def from_skillopt_settings(cls, skillopt_settings: dict[str, Any] | None) -> "SkillGateConfig":
        raw = skillopt_settings or {}
        gate = raw.get("gate") or {}
        finalize = raw.get("finalize") or {}
        return cls(
            strict_best_monotonic=bool(gate.get("strict_best_monotonic", True)),
            knowledge_updates_current_only=bool(gate.get("knowledge_updates_current_only", True)),
            export_current_on_tie=bool(
                finalize.get("export_current_on_tie", gate.get("export_current_on_tie", False))
            ),
        )


def estimate_skill_tokens(skill: str) -> int:
    """Rough token estimate: len/4."""
    return max(1, len(skill) // 4)


def _normalize_rule_line(line: str) -> str:
    text = line.strip().lstrip("-*+").strip()
    text = re.sub(r"\s+", " ", text).lower()
    return text


def count_rules(skill: str) -> int:
    count = len(_BULLET_RE.findall(skill))
    count += sum(1 for ln in skill.splitlines() if _TABLE_ROW_RE.match(ln) and "|" in ln[1:])
    return count


def count_duplicate_rules(skill: str) -> int:
    seen: set[str] = set()
    dupes = 0
    for ln in skill.splitlines():
        stripped = ln.strip()
        if not stripped.startswith(("-", "*", "+")):
            continue
        norm = _normalize_rule_line(stripped)
        if not norm:
            continue
        if norm in seen:
            dupes += 1
        else:
            seen.add(norm)
    return dupes


def _compile_patterns(patterns: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pat in patterns:
        if not pat:
            continue
        try:
            compiled.append((pat, re.compile(pat, re.I)))
        except re.error:
            compiled.append((pat, re.compile(re.escape(pat), re.I)))
    return compiled


def scan_skill_quality(skill: str, config: QualityGateConfig) -> dict[str, Any]:
    """Scan skill for leakage, size, rule count, duplicates."""
    leakage_hits: list[dict[str, Any]] = []
    case_id_hits: list[dict[str, Any]] = []

    for line_no, line in enumerate(skill.splitlines(), start=1):
        for label, pat in _compile_patterns(config.leakage_patterns):
            if pat.search(line):
                leakage_hits.append({"pattern": label, "line": line_no, "text": line.strip()[:120]})
        for label, pat in _compile_patterns(config.benchmark_id_patterns):
            match = pat.search(line)
            if match:
                case_id_hits.append({
                    "pattern": label,
                    "line": line_no,
                    "value": match.group(0),
                    "text": line.strip()[:120],
                })

    est_tokens = estimate_skill_tokens(skill)
    rule_count = count_rules(skill)
    duplicate_rule_count = count_duplicate_rules(skill)

    size_ok = est_tokens <= config.max_skill_tokens
    rules_ok = rule_count <= config.max_rules
    dupes_ok = duplicate_rule_count == 0
    leakage_ok = len(leakage_hits) == 0
    case_id_ok = len(case_id_hits) == 0

    passed = size_ok and rules_ok and dupes_ok and leakage_ok and case_id_ok

    return {
        "estimated_tokens": est_tokens,
        "max_skill_tokens": config.max_skill_tokens,
        "rule_count": rule_count,
        "max_rules": config.max_rules,
        "duplicate_rule_count": duplicate_rule_count,
        "leakage_hits": leakage_hits,
        "leakage_count": len(leakage_hits),
        "case_id_hits": case_id_hits,
        "case_id_count": len(case_id_hits),
        "passed": passed,
        "size_ok": size_ok,
        "rules_ok": rules_ok,
        "dupes_ok": dupes_ok,
        "leakage_ok": leakage_ok,
        "case_id_ok": case_id_ok,
    }


def build_skill_quality_report(
    skill: str,
    config: QualityGateConfig,
    *,
    step: int = 0,
    skill_hash: str = "",
) -> dict[str, Any]:
    scan = scan_skill_quality(skill, config)
    return {
        "schema_version": "1.0",
        "step": step,
        "skill_hash": skill_hash,
        **scan,
    }


def sanitize_skill(skill: str, config: QualityGateConfig) -> tuple[str, list[str]]:
    """Remove lines matching leakage / benchmark id patterns and duplicate bullets."""
    leakage_pats = _compile_patterns(config.leakage_patterns)
    case_pats = _compile_patterns(config.benchmark_id_patterns)
    actions: list[str] = []
    kept_lines: list[str] = []
    seen_rules: set[str] = set()

    for line in skill.splitlines():
        drop = False
        for label, pat in leakage_pats:
            if pat.search(line):
                actions.append(f"removed_leakage:{label}")
                drop = True
                break
        if drop:
            continue
        for label, pat in case_pats:
            if pat.search(line):
                actions.append(f"removed_case_id:{label}")
                drop = True
                break
        if drop:
            continue
        stripped = line.strip()
        if stripped.startswith(("-", "*", "+")):
            norm = _normalize_rule_line(stripped)
            if norm in seen_rules:
                actions.append("removed_duplicate_rule")
                continue
            seen_rules.add(norm)
        kept_lines.append(line)

    sanitized = "\n".join(kept_lines).strip()
    if sanitized and not sanitized.endswith("\n"):
        sanitized += "\n"
    return sanitized, actions


def edit_has_leakage(content: str, config: QualityGateConfig | None = None) -> tuple[bool, str]:
    """Edit-level leakage check for filter_valid_edits."""
    cfg = config or QualityGateConfig()
    text = (content or "").strip()
    if not text:
        return False, ""
    for label, pat in _compile_patterns(cfg.leakage_patterns):
        if pat.search(text):
            return True, f"leakage:{label}"
    for label, pat in _compile_patterns(cfg.benchmark_id_patterns):
        if pat.search(text):
            return True, f"case_id:{label}"
    return False, ""


def build_run_quality_report(
    *,
    run_id: str,
    initial_skill: str,
    best_skill: str,
    best_score: float,
    history: list[dict],
    test_report: dict | None = None,
    optimization_dir: str = "",
    quality_config: QualityGateConfig | None = None,
) -> dict[str, Any]:
    """Aggregate run-level quality metrics for inspect run."""
    cfg = quality_config or QualityGateConfig()
    best_scan = scan_skill_quality(best_skill, cfg)
    best_scores = [float(h.get("best_score", 0)) for h in history if "best_score" in h]
    monotonic = all(
        best_scores[i] >= best_scores[i - 1] - 1e-9
        for i in range(1, len(best_scores))
    ) if len(best_scores) > 1 else True

    hard_failures: list[dict[str, Any]] = []
    test_soft = 0.0
    test_hard = 0.0
    if test_report:
        test_soft = float(test_report.get("test_score", 0) or 0)
        test_hard = float(test_report.get("test_hard", 0) or 0)
        report_path = test_report.get("report_path", "")
        if report_path:
            try:
                import json
                from pathlib import Path
                data = json.loads(Path(report_path).read_text(encoding="utf-8"))
                for row in data.get("per_item") or []:
                    if int(row.get("hard") or 0) == 0:
                        hard_failures.append({
                            "id": row.get("id", ""),
                            "missed_checks": list(row.get("missed_checks") or []),
                            "soft": row.get("soft", 0),
                        })
            except (OSError, json.JSONDecodeError, TypeError):
                pass

    from pathlib import Path

    from .code_diagnosis import collect_diagnosis_run_metrics

    diag_root = optimization_dir
    if not diag_root and test_report and test_report.get("report_path"):
        diag_root = str(Path(test_report["report_path"]).resolve().parent.parent)
    diagnosis_metrics = collect_diagnosis_run_metrics(diag_root) if diag_root else {
        "diagnosis_steps": 0,
        "diagnosis_count": 0,
        "hard_failure_coverage": 0.0,
        "code_facts_rate": 0.0,
        "needs_review_count": 0,
    }

    replay_hard = 0.0
    replay_regressed_ids: list[str] = []
    try:
        from pathlib import Path
        opt_dir = Path(test_report.get("report_path", "")).parent.parent if test_report else None
        if opt_dir and opt_dir.name == "final_eval":
            opt_dir = opt_dir.parent
        if opt_dir and (opt_dir / "steps").is_dir():
            replay_files = sorted((opt_dir / "steps").glob("step_*/replay_eval_report.json"))
            if replay_files:
                import json
                last_replay = json.loads(replay_files[-1].read_text(encoding="utf-8"))
                replay_hard = float(last_replay.get("hard", 0) or 0)
                replay_regressed_ids = list(last_replay.get("regressed_ids") or [])
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass

    recommendations: list[str] = []
    if replay_regressed_ids:
        recommendations.append(f"Replay regression on: {', '.join(replay_regressed_ids[:5])}")
    if not monotonic:
        recommendations.append("Do not overwrite best during knowledge_accept.")
    if best_scan.get("leakage_count", 0) > 0 or best_scan.get("case_id_count", 0) > 0:
        recommendations.append("Sanitize benchmark/scorer-facing rules before gate.")
    if best_scan.get("estimated_tokens", 0) > cfg.max_skill_tokens:
        recommendations.append(f"Reduce skill size below {cfg.max_skill_tokens} tokens.")

    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "initial_skill_chars": len(initial_skill),
        "best_skill_chars": len(best_skill),
        "best_score": round(best_score, 3),
        "test_soft": round(test_soft, 3),
        "test_hard": round(test_hard, 3),
        "best_score_monotonic": monotonic,
        "leakage_count": best_scan.get("leakage_count", 0),
        "case_id_count": best_scan.get("case_id_count", 0),
        "duplicate_rule_count": best_scan.get("duplicate_rule_count", 0),
        "estimated_tokens": best_scan.get("estimated_tokens", 0),
        "hard_failures": hard_failures,
        "replay_hard": round(replay_hard, 3),
        "replay_regressed_ids": replay_regressed_ids,
        "diagnosis_metrics": diagnosis_metrics,
        "code_retrieval_metrics": _collect_code_retrieval_run_metrics(optimization_dir),
        "recommendations": recommendations,
    }


def _collect_code_retrieval_run_metrics(optimization_dir: str) -> dict[str, Any]:
    """从 code_retrieval/step_*/summary.json 聚合 run 级指标（设计 09 §12）。"""
    if not optimization_dir:
        return {}
    from pathlib import Path
    from collections import Counter

    root = Path(optimization_dir) / "code_retrieval"
    if not root.is_dir():
        return {}

    total_plans = 0
    total_candidates = 0
    total_facts = 0
    total_cases = 0
    cases_with_facts = 0
    all_sources: Counter[str] = Counter()
    glue_top1_total = 0
    steps = 0

    for summary_path in sorted(root.glob("step_*/summary.json")):
        steps += 1
        try:
            row = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        total_plans += int(row.get("query_plans", 0) or 0)
        total_candidates += int(row.get("candidates", 0) or 0)
        total_facts += int(row.get("facts", 0) or 0)
        total_cases += int(row.get("cases", 0) or 0)
        cases_with_facts += int(row.get("cases_with_facts", 0) or 0)
        for src, count in (row.get("top_sources") or {}).items():
            all_sources[src] += int(count or 0)
        glue_top1_total += int(row.get("downranked_glue_top1", 0) or 0)

    import operator
    return {
        "steps_with_retrieval": steps,
        "query_plan_count": total_plans,
        "total_candidates": total_candidates,
        "total_facts": total_facts,
        "cases_with_code_facts": cases_with_facts,
        "code_facts_rate": round(cases_with_facts / max(total_cases, 1), 3),
        "avg_candidates_per_case": round(total_candidates / max(total_cases, 1), 1),
        "avg_facts_per_case": round(total_facts / max(total_cases, 1), 1),
        "top_sources": dict(all_sources.most_common(5)),
        "glue_code_top1_rate": round(glue_top1_total / max(steps, 1), 3),
        "business_rules_with_evidence_rate": round(
            cases_with_facts / max(total_cases, 1), 3,
        ),
    }
