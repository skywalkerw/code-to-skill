"""失败样本代码诊断（设计 08 §10）。

项目特定的失败分类与规则建议应通过 benchmark scorer 的 ``diagnostics`` 字段提供；
通用层负责卫生检查、别名启发、以及按 artifact 顺序读取代码证据。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_to_skill.time_utils import local_timestamp

from .output_hygiene import OutputHygieneConfig, detect_output_hygiene
from .scoring import merge_check_aliases

logger = logging.getLogger(__name__)

FAILURE_TYPES = (
    "prompt_echo",
    "output_format_error",
    "scorer_alias_gap",
    "missing_business_rule",
    "unknown",
)


@dataclass
class CodeDiagnosisConfig:
    enabled: bool = True
    max_context_files: int = 2
    max_snippet_chars: int = 800
    max_cases_per_step: int = 8
    write_jsonl: bool = True
    require_code_facts_for_rules: bool = True  # 设计 09 §9：默认强制代码证据

    @classmethod
    def from_skillopt_settings(cls, skillopt_settings: dict[str, Any] | None) -> "CodeDiagnosisConfig":
        raw = (skillopt_settings or {}).get("code_diagnosis") or {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            max_context_files=int(raw.get("max_context_files", 2) or 2),
            max_snippet_chars=int(raw.get("max_snippet_chars", 800) or 800),
            max_cases_per_step=int(raw.get("max_cases_per_step", 8) or 8),
            write_jsonl=bool(raw.get("write_jsonl", True)),
            require_code_facts_for_rules=bool(raw.get("require_code_facts_for_rules", True)),
        )


def _looks_like_alias_gap(
    missed_checks: list,
    predicted: str,
    check_aliases: dict[str, list[str]] | None,
) -> bool:
    if not missed_checks or not check_aliases:
        return False
    pred_norm = (predicted or "").lower()
    for check in missed_checks:
        key = (str(check) or "").strip().lower()
        if not key or key in pred_norm:
            continue
        for alias in check_aliases.get(key, []):
            alias_s = str(alias).strip()
            if alias_s and (alias_s.lower() in pred_norm or alias_s in (predicted or "")):
                return True
    return False


def _classify_failure_type(
    result: dict,
    *,
    hygiene_cfg: OutputHygieneConfig | None = None,
    check_aliases: dict[str, list[str]] | None = None,
) -> str:
    scorer_diag = result.get("scorer_diagnostics") or {}
    if scorer_diag.get("failure_type"):
        return str(scorer_diag["failure_type"])
    if result.get("diagnosis_failure_type"):
        return str(result["diagnosis_failure_type"])

    pred = str(result.get("predicted_answer") or "")
    missed = result.get("missed_checks") or []
    if result.get("output_hygiene_reason") in ("prompt_echo", "tool_leak"):
        return "prompt_echo"
    clean, reason, _ = detect_output_hygiene(pred, hygiene_cfg)
    if not clean and reason in ("prompt_echo", "tool_leak"):
        return "prompt_echo"
    aliases = merge_check_aliases(
        check_aliases,
        result.get("item_check_aliases"),
    )
    if missed and _looks_like_alias_gap(missed, pred, aliases):
        return "scorer_alias_gap"
    if result.get("hard", 1) == 0:
        return "missing_business_rule"
    return "unknown"


def _append_sidecar_facts(
    result: dict,
    facts: list[dict[str, str]],
    *,
    graph_sidecars: Any,
    max_chars: int,
) -> str:
    """role_index / entrypoints sidecars (设计 08 §7.2 step 3)."""
    if graph_sidecars is None:
        return ""
    source = ""
    if getattr(graph_sidecars, "use_role_index", True):
        framework, role = graph_sidecars.resolve_graph_role(result)
        index = getattr(graph_sidecars, "role_index", None)
        if role and index is not None:
            for entry in index.lookup(role, framework=framework, limit=1):
                facts.append({
                    "ref": entry.file_path,
                    "snippet": "",
                    "fact": (
                        f"Role[{entry.framework}/{entry.role}] "
                        f"symbols={', '.join(entry.symbols[:4])}"
                    ),
                    "source": "role_index",
                })
                source = source or "role_index"
    if getattr(graph_sidecars, "use_entrypoints", True):
        ep_index = getattr(graph_sidecars, "entrypoints", None)
        ep_id = str(result.get("entrypoint_id") or "")
        if ep_index is not None and ep_id:
            ep = ep_index.lookup(ep_id)
            if ep:
                facts.append({
                    "ref": ep.path or ep.id,
                    "snippet": "",
                    "fact": f"Entrypoint[{ep.kind}] id={ep.id} handler={ep.handler_node_id}",
                    "source": "entrypoints",
                })
                source = source or "entrypoints"
    return source


def _read_ref_snippet(
    ref: str,
    *,
    code_tools: Any,
    max_chars: int,
) -> tuple[str, str]:
    """context_ref file read or explore_symbol fallback."""
    import json

    from .code_evidence import parse_context_ref

    if code_tools is None:
        return "", ""
    file_path, symbol = parse_context_ref(ref)
    if symbol and hasattr(code_tools, "execute"):
        try:
            raw = code_tools.execute({
                "function": {
                    "name": "explore_symbol",
                    "arguments": json.dumps({"symbol": symbol, "include_source": True}),
                },
            })
            data = json.loads(raw)
            if not data.get("error") and data.get("source"):
                return data["source"][:max_chars], "explore_symbol"
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("code_diagnosis explore %s: %s", ref, exc)
    if hasattr(code_tools, "read_code_file"):
        try:
            snippet = str(code_tools.read_code_file(ref) or "")[:max_chars]
            if snippet:
                return snippet, "context_ref"
        except Exception as exc:
            logger.debug("code_diagnosis read %s: %s", ref, exc)
    if file_path and hasattr(code_tools, "execute"):
        try:
            raw = code_tools.execute({
                "function": {
                    "name": "read_code_file",
                    "arguments": json.dumps({"path": file_path, "end_line": 60}),
                },
            })
            data = json.loads(raw)
            content = str(data.get("content") or "")
            if content:
                return content[:max_chars], "read_code_file"
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("code_diagnosis file %s: %s", ref, exc)
    return "", ""


def _collect_code_facts(
    result: dict,
    *,
    code_tools: Any = None,
    graph_sidecars: Any = None,
    max_files: int = 2,
    max_chars: int = 800,
) -> tuple[list[dict[str, str]], str]:
    """代码事实收集（设计 09 Phase 3 升级版）。

    优先使用确定性检索管线 (CodeQueryPlan → find_relevant_code)；
    回退到旧版 context_refs → evidence_index → role/entrypoint → code read。
    """
    # 尝试新检索管线
    if code_tools is not None and hasattr(code_tools, "execute"):
        try:
            from code_to_skill.tool.code_retrieval import find_relevant_code

            retrieval = find_relevant_code(
                result,
                code_tools,
                graph_sidecars=graph_sidecars,
                max_candidates=8,
                max_snippet_chars=max_chars,
            )
            if retrieval.facts:
                facts: list[dict[str, str]] = []
                for fact in retrieval.facts:
                    facts.append({
                        "ref": fact.evidence_refs[0] if fact.evidence_refs else "",
                        "snippet": fact.evidence_quotes[0] if fact.evidence_quotes else "",
                        "fact": fact.statement,
                        "source": f"code_retrieval:{fact.source}",
                        "confidence": str(fact.confidence),
                        "role": fact.role,
                    })
                return facts, "code_retrieval"
        except Exception:
            logger.debug("code_retrieval fallback to legacy collection", exc_info=True)

    # 回退：旧版收集
    facts: list[dict[str, str]] = []
    diagnosis_source = ""
    refs = [str(r) for r in (result.get("context_refs") or []) if r][:max_files]
    store = (
        getattr(graph_sidecars, "evidence_index", None)
        if graph_sidecars is not None
        else None
    )

    for ref in refs:
        entry: dict[str, str] = {"ref": ref, "snippet": "", "fact": "", "source": ""}
        if store is not None:
            from .graph_sidecars import EvidenceIndexStore

            hits = store.lookup_ref(ref)
            if hits:
                entry["fact"] = EvidenceIndexStore.format_hit(hits[0])
                entry["source"] = "evidence_index"
                diagnosis_source = diagnosis_source or "evidence_index"
        if not entry["fact"]:
            snippet, src = _read_ref_snippet(ref, code_tools=code_tools, max_chars=max_chars)
            if snippet:
                entry["snippet"] = snippet
                entry["source"] = src
                diagnosis_source = diagnosis_source or src
        if entry["fact"] or entry["snippet"]:
            facts.append(entry)

    sidecar_src = _append_sidecar_facts(
        result, facts, graph_sidecars=graph_sidecars, max_chars=max_chars,
    )
    diagnosis_source = diagnosis_source or sidecar_src
    return facts, diagnosis_source


def _diagnosis_status(
    failure_type: str,
    code_facts: list[dict],
    *,
    require_code_facts: bool,
) -> str:
    if failure_type == "prompt_echo":
        return "ready"
    if code_facts:
        return "ready"
    if require_code_facts:
        return "needs_review"
    return "ready"


def _suggest_general_rule(failure_type: str, result: dict) -> str:
    scorer_diag = result.get("scorer_diagnostics") or {}
    if scorer_diag.get("suggested_rule"):
        return str(scorer_diag["suggested_rule"])
    if result.get("diagnosis_suggested_rule"):
        return str(result["diagnosis_suggested_rule"])

    missed = result.get("missed_checks") or []
    missed_s = ", ".join(str(m) for m in missed[:5])
    templates = {
        "prompt_echo": (
            "Final answer must not repeat Task/Skill reference/Code context; "
            "output only the deliverable."
        ),
        "output_format_error": (
            f"Output format does not satisfy scorer checks: {missed_s}."
        ),
        "scorer_alias_gap": (
            f"Output should satisfy missed checks (and configured aliases): {missed_s}."
        ),
        "missing_business_rule": (
            f"Add skill rules so output covers missed checks: {missed_s}."
        ),
    }
    return templates.get(failure_type, "")


def diagnose_failure(
    result: dict,
    *,
    step: int,
    code_tools: Any = None,
    graph_sidecars: Any = None,
    hygiene_cfg: OutputHygieneConfig | None = None,
    config: CodeDiagnosisConfig | None = None,
    check_aliases: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    cfg = config or CodeDiagnosisConfig()
    failure_type = _classify_failure_type(
        result,
        hygiene_cfg=hygiene_cfg,
        check_aliases=check_aliases,
    )
    code_facts, diagnosis_source = _collect_code_facts(
        result,
        code_tools=code_tools,
        graph_sidecars=graph_sidecars,
        max_files=cfg.max_context_files,
        max_chars=cfg.max_snippet_chars,
    )
    status = _diagnosis_status(
        failure_type,
        code_facts,
        require_code_facts=cfg.require_code_facts_for_rules,
    )
    general_rule = _suggest_general_rule(failure_type, result)
    return {
        "schema_version": "1.0",
        "step": step,
        "item_id": result.get("id", ""),
        "question": result.get("question", ""),
        "failure_type": failure_type,
        "missed_checks": list(result.get("missed_checks") or []),
        "predicted_excerpt": str(result.get("predicted_answer") or "")[:400],
        "context_refs": list(result.get("context_refs") or []),
        "code_facts": code_facts,
        "failure_cause": general_rule,
        "general_rule": general_rule,
        "suggested_general_rule": general_rule,
        "diagnosis_source": diagnosis_source or "missed_checks",
        "status": status,
        "diagnosed_at": local_timestamp(),
    }


def diagnose_failures(
    rollout_results: list[dict],
    *,
    step: int,
    code_tools: Any = None,
    graph_sidecars: Any = None,
    hygiene_cfg: OutputHygieneConfig | None = None,
    config: CodeDiagnosisConfig | None = None,
    check_aliases: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    cfg = config or CodeDiagnosisConfig()
    if not cfg.enabled:
        return []
    failures = [r for r in rollout_results if r.get("hard", 1) == 0]
    failures = failures[: cfg.max_cases_per_step]
    out: list[dict[str, Any]] = []
    for row in failures:
        out.append(
            diagnose_failure(
                row,
                step=step,
                code_tools=code_tools,
                graph_sidecars=graph_sidecars,
                hygiene_cfg=hygiene_cfg,
                config=cfg,
                check_aliases=check_aliases,
            )
        )
    return out


def append_diagnoses_jsonl(path: str | Path, diagnoses: list[dict]) -> None:
    if not diagnoses:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for row in diagnoses:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


_FAILURE_PRIORITY = {
    "prompt_echo": 0,
    "output_format_error": 1,
    "scorer_alias_gap": 2,
    "missing_business_rule": 3,
}


def sort_diagnoses_for_reflect(diagnoses: list[dict]) -> list[dict]:
    return sorted(
        diagnoses,
        key=lambda d: (
            _FAILURE_PRIORITY.get(str(d.get("failure_type")), 9),
            str(d.get("item_id", "")),
        ),
    )


def collect_diagnosis_run_metrics(output_dir: str | Path) -> dict[str, Any]:
    """Aggregate diagnosis coverage from code_diagnosis/step_*/ artifacts.

    含 code_retrieval_metrics（设计 09 §12）。
    """
    root = Path(output_dir) / "code_diagnosis"
    steps_root = Path(output_dir) / "steps"
    if not root.is_dir():
        return {
            "diagnosis_steps": 0,
            "diagnosis_count": 0,
            "hard_failure_count": 0,
            "hard_failure_coverage": 0.0,
            "code_facts_rate": 0.0,
            "needs_review_count": 0,
            "code_retrieval_metrics": {},
        }

    total = 0
    with_facts = 0
    needs_review = 0
    steps = 0
    diagnosed_ids: set[str] = set()
    hard_failure_ids: set[str] = set()

    # code_retrieval_metrics aggregation
    retrieval_cases_with_facts = 0
    retrieval_cases_total = 0
    retrieval_fact_sources: dict[str, int] = {}
    retrieval_glue_top1 = 0
    retrieval_scores: list[float] = []
    retrieval_top_roles: list[str] = []

    for summary_path in sorted(root.glob("step_*/summary.json")):
        steps += 1
        try:
            row = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        total += int(row.get("diagnosis_count", 0) or 0)
        needs_review += int(row.get("needs_review_count", 0) or 0)

    for jsonl_path in sorted(root.glob("step_*/code_diagnosis.jsonl")):
        try:
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("item_id"):
                    diagnosed_ids.add(str(row.get("item_id")))
                code_facts = row.get("code_facts") or []
                if code_facts:
                    with_facts += 1
                    retrieval_cases_with_facts += 1
                retrieval_cases_total += 1

                # per-fact metrics
                for fact in code_facts:
                    src = str(fact.get("source") or "")
                    retrieval_fact_sources[src] = retrieval_fact_sources.get(src, 0) + 1
                    role = str(fact.get("role") or "")
                    retrieval_top_roles.append(role)
                    try:
                        conf = float(fact.get("confidence") or 0)
                        retrieval_scores.append(conf)
                    except (ValueError, TypeError):
                        pass
                    if fact is code_facts[0]:
                        if role in ("handler_only", "swagger", "configuration", "resource_api"):
                            retrieval_glue_top1 += 1

        except (OSError, json.JSONDecodeError):
            continue

    if steps_root.is_dir():
        for summary_path in sorted(steps_root.glob("step_*/rollout_summary.json")):
            try:
                row = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for failure in row.get("failures") or []:
                if isinstance(failure, dict) and failure.get("id"):
                    hard_failure_ids.add(str(failure.get("id")))
    coverage = (
        len(diagnosed_ids & hard_failure_ids) / max(len(hard_failure_ids), 1)
        if hard_failure_ids
        else (1.0 if total > 0 else 0.0)
    )

    # compute retrieval metrics
    retrieval_facts_rate = (
        retrieval_cases_with_facts / max(retrieval_cases_total, 1)
        if retrieval_cases_total > 0 else 0.0
    )
    glue_top1_rate = (
        retrieval_glue_top1 / max(retrieval_cases_with_facts, 1)
        if retrieval_cases_with_facts > 0 else 0.0
    )
    avg_score = (
        sum(retrieval_scores) / len(retrieval_scores) if retrieval_scores else 0.0
    )

    return {
        "diagnosis_steps": steps,
        "diagnosis_count": total,
        "hard_failure_count": len(hard_failure_ids),
        "hard_failure_coverage": round(coverage, 3),
        "code_facts_rate": round(with_facts / max(total, 1), 3),
        "needs_review_count": needs_review,
        "code_retrieval_metrics": {
            "query_plan_count": retrieval_cases_total,
            "cases_with_code_facts": retrieval_cases_with_facts,
            "code_facts_rate": round(retrieval_facts_rate, 3),
            "business_rules_with_evidence_rate": (
                retrieval_fact_sources.get("code_retrieval:context_ref", 0)
                + retrieval_fact_sources.get("code_retrieval:evidence_index", 0)
                + retrieval_fact_sources.get("code_retrieval:trace", 0)
                + retrieval_fact_sources.get("code_retrieval:symbol_search", 0)
            ) / max(sum(retrieval_fact_sources.values()), 1) if retrieval_fact_sources else 0.0,
            "glue_code_top1_rate": round(glue_top1_rate, 3),
            "avg_candidates_per_case": 0,  # 候选数需在 retrieval 执行时写入
            "avg_facts_per_case": round(
                sum(retrieval_fact_sources.values()) / max(retrieval_cases_total, 1),
                1,
            ) if retrieval_cases_total > 0 else 0.0,
            "avg_fact_confidence": round(avg_score, 3),
            "top_roles": list(dict.fromkeys(retrieval_top_roles))[:6],
            "fact_sources": retrieval_fact_sources,
        },
    }


def format_diagnoses_for_reflect(diagnoses: list[dict]) -> str:
    if not diagnoses:
        return ""
    lines = ["### Code diagnosis (from failures)"]
    for d in sort_diagnoses_for_reflect(diagnoses)[:8]:
        lines.append(
            f"- {d.get('item_id')}: type={d.get('failure_type')} "
            f"status={d.get('status')} "
            f"missed={d.get('missed_checks')} "
            f"rule={d.get('general_rule')}"
        )
        for fact in (d.get("code_facts") or [])[:1]:
            if fact.get("fact"):
                lines.append(f"  evidence: {fact.get('fact')[:200]}")
            elif (fact.get("snippet") or "").strip():
                lines.append(f"  ref={fact.get('ref')}: {fact.get('snippet', '')[:200]}")
    return "\n".join(lines)
