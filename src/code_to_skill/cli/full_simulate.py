"""L3 full-simulate：L2 静态分析 + MockReplayBackend 全流程演练。"""
from __future__ import annotations

import copy
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from code_to_skill.time_utils import local_timestamp, local_timestamp_compact

from .pipeline_config import ModuleRunSettings
from .static_analysis import StaticAnalysisReport, run_static_analysis

_SIMULATE_ENV = "SKILL_LAB_SIMULATE"

_DEFAULT_TRAIN_ITEMS = [
    {
        "id": "sim_001",
        "question": "What validation is required before retry?",
        "expected_checks": ["idempotency", "audit"],
        "context_mode": "none",
    },
    {
        "id": "sim_002",
        "question": "How should failures be recorded?",
        "expected_checks": ["audit", "retry"],
        "context_mode": "none",
    },
]


def simulate_fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "full_simulate" / "mock-backend"


@contextmanager
def simulate_llm_env():
    """启用 MockReplay 模式（``create_llm_backend`` / ``is_llm_available``）。"""
    prev = os.environ.get(_SIMULATE_ENV)
    os.environ[_SIMULATE_ENV] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(_SIMULATE_ENV, None)
        else:
            os.environ[_SIMULATE_ENV] = prev


def build_simulate_model_provider(model_provider: dict | Any | None) -> dict:
    """将所有路由指向 mock-backend 并绑定内置 fixture。"""
    if hasattr(model_provider, "model_dump"):
        base = model_provider.model_dump()
    elif isinstance(model_provider, dict):
        base = copy.deepcopy(model_provider)
    else:
        base = {}

    backends = dict(base.get("backends") or {})
    backends["mock-backend"] = {
        "type": "mock",
        "provider": "mock",
        "model": "mock-simulate",
        "fixture_dir": str(simulate_fixture_dir()),
    }
    routes = dict(base.get("routes") or {})
    for role in (
        "extractor", "clusterer", "optimizer", "target",
        "judge", "agent_worker", "default",
    ):
        routes[role] = {"primary": "mock-backend", "fallback": []}

    out = copy.deepcopy(base)
    out["backends"] = backends
    out["routes"] = routes
    out["trace_enabled"] = False
    return out


def build_simulate_skillopt(skillopt: dict | Any | None) -> dict:
    """缩小训练规模，保证 simulate 快速结束。"""
    if hasattr(skillopt, "model_dump"):
        base = skillopt.model_dump()
    elif isinstance(skillopt, dict):
        base = copy.deepcopy(skillopt)
    else:
        base = {}
    out = copy.deepcopy(base)
    out.update({
        "num_epochs": 1,
        "batch_size": 2,
        "accumulation": 1,
        "edit_budget": 2,
        "patience": 1,
        "use_llm_rollout": True,
        "rollout_backend": "mock-backend",
        "optimizer_backend": "mock-backend",
        "enable_code_tools": False,
        "enable_slow_update": False,
        "enable_meta_skill": False,
    })
    return out


@dataclass
class FullSimulateReport:
    static_analysis: StaticAnalysisReport
    output_root: str = ""
    phases: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def format_lines(self) -> list[str]:
        lines = ["", "── L3 full-simulate ──"]
        if self.output_root:
            lines.append(f"  Simulate run: {self.output_root}")
        for phase in self.phases:
            name = phase.get("phase", "?")
            status = phase.get("status", "?")
            extra = ""
            if phase.get("metrics"):
                m = phase["metrics"]
                extra = " " + ", ".join(f"{k}={v}" for k, v in m.items())
            if phase.get("error"):
                extra += f" error={phase['error'][:80]}"
            lines.append(f"  {name}: {status}{extra}")
        for w in self.warnings:
            lines.append(f"  ⚠️  {w}")
        lines.append("")
        lines.append("✅ L3 full-simulate 完成（全部 LLM 调用使用 MockReplayBackend）")
        return lines


def run_full_simulate(
    cfg: Any,
    *,
    echo: Callable[[str], None] | None = None,
) -> FullSimulateReport:
    """执行 L2 + M1–M4 模拟流水线（无真实 LLM）。"""
    _echo = echo or (lambda _msg: None)
    sa = run_static_analysis(cfg)
    for line in sa.format_lines():
        _echo(line)

    p = cfg.project
    s = cfg.settings
    run_id = f"simulate-{local_timestamp_compact()}"
    output_root = os.path.join(s.output_root, run_id)
    os.makedirs(output_root, exist_ok=True)

    report = FullSimulateReport(static_analysis=sa, output_root=output_root)
    module_settings = ModuleRunSettings.from_settings(s)
    cg_kwargs = module_settings.code_graph_pipeline_kwargs()
    cg_settings = dict(cg_kwargs.get("code_graph_settings") or {})
    cg_settings["llm_clustering_enabled"] = False
    cg_kwargs["code_graph_settings"] = cg_settings
    cg_kwargs["use_cache"] = False

    sim_mp = build_simulate_model_provider(s.model_provider)
    sim_skillopt = build_simulate_skillopt(s.skillopt)

    all_leaf_ctxs: list = []
    doc_chunks: list = []
    m3: dict | None = None

    with simulate_llm_env():
        if p.repos:
            try:
                from code_to_skill.code_graph import run_code_graph_pipeline
                from .graph_config import resolve_framework_patterns

                total_nodes = 0
                for repo in p.repos:
                    if not os.path.isdir(repo.path):
                        report.warnings.append(f"M1 skip missing repo: {repo.id}")
                        continue
                    m1 = run_code_graph_pipeline(
                        repo_root=repo.path,
                        include=repo.include,
                        exclude=repo.exclude,
                        output_root=os.path.join(
                            output_root, "sources", "code", repo.id, repo.ref,
                        ),
                        repo_id=repo.id,
                        snapshot_ref=repo.ref,
                        custom_patterns=resolve_framework_patterns(p, repo),
                        **cg_kwargs,
                    )
                    total_nodes += len(m1["graph"].nodes)
                    all_leaf_ctxs.extend(
                        [ctx.model_dump() for ctx in m1.get("leaf_contexts", [])],
                    )
                report.phases.append({
                    "phase": "m1_code_graph",
                    "status": "completed",
                    "metrics": {"nodes": total_nodes, "leaf_contexts": len(all_leaf_ctxs)},
                })
            except Exception as e:
                report.phases.append({
                    "phase": "m1_code_graph", "status": "failed", "error": str(e),
                })
                report.warnings.append(f"M1 failed: {e}")
        else:
            report.phases.append({
                "phase": "m1_code_graph", "status": "skipped", "reason": "no repos",
            })

        if p.docs:
            try:
                from code_to_skill.document_normalizer import normalize_document

                for doc in p.docs:
                    if doc.provider != "local_file":
                        report.warnings.append(f"M2 skip {doc.id}: provider {doc.provider}")
                        continue
                    if not os.path.exists(doc.path):
                        report.warnings.append(f"M2 skip {doc.id}: path missing")
                        continue
                    result = normalize_document(
                        source_uri=doc.path,
                        source_id=doc.id,
                        source_provider=doc.provider,
                        output_root=os.path.join(
                            output_root, "sources", "docs", doc.id, doc.version,
                        ),
                        **module_settings.normalize_document_kwargs(),
                    )
                    doc_chunks.extend([c.model_dump() for c in result["chunks"]])
                report.phases.append({
                    "phase": "m2_docs",
                    "status": "completed",
                    "metrics": {"chunks": len(doc_chunks)},
                })
            except Exception as e:
                report.phases.append({
                    "phase": "m2_docs", "status": "failed", "error": str(e),
                })
                report.warnings.append(f"M2 failed: {e}")
        else:
            report.phases.append({
                "phase": "m2_docs", "status": "skipped", "reason": "no docs",
            })

        if all_leaf_ctxs or doc_chunks:
            try:
                from code_to_skill.atom_extractor import run_atom_extraction

                graph_db_path = ""
                repo_root = ""
                if p.repos:
                    repo = p.repos[0]
                    graph_db_path = os.path.join(
                        output_root, "sources", "code", repo.id, repo.ref, "graph.db",
                    )
                    repo_root = repo.path
                m3 = run_atom_extraction(
                    leaf_contexts=all_leaf_ctxs,
                    document_chunks=doc_chunks,
                    output_root=os.path.join(output_root, "atoms"),
                    graph_db_path=graph_db_path,
                    repo_root=repo_root,
                    atom_extractor_settings=s.atom_extractor,
                )
                report.phases.append({
                    "phase": "m3_atoms",
                    "status": "completed",
                    "metrics": {
                        "merged": len(m3["merged_atoms"]),
                        "seeds": len(m3["benchmark_seeds"]),
                    },
                })
            except Exception as e:
                report.phases.append({
                    "phase": "m3_atoms", "status": "failed", "error": str(e),
                })
                report.warnings.append(f"M3 failed: {e}")
        else:
            report.phases.append({
                "phase": "m3_atoms", "status": "skipped", "reason": "no inputs",
            })

        try:
            from code_to_skill.skillopt_loop import run_skillopt_loop

            initial_skill = "# Simulate Skill\n- Default rule for dry-run.\n"
            if p.initial_skill_path and os.path.isfile(p.initial_skill_path):
                with open(p.initial_skill_path, encoding="utf-8") as f:
                    initial_skill = f.read()

            train_items = list(_DEFAULT_TRAIN_ITEMS)
            if m3 and m3.get("benchmark_seeds"):
                train_items = m3["benchmark_seeds"][:2] or train_items

            graph_db_path, repo_root, graph_sources = "", "", None
            if p.repos:
                repo = p.repos[0]
                graph_db_path = os.path.join(
                    output_root, "sources", "code", repo.id, repo.ref, "graph.db",
                )
                repo_root = repo.path
                graph_sources = [{
                    "repo_id": repo.id,
                    "db_path": graph_db_path,
                    "repo_root": repo_root,
                }] if os.path.isfile(graph_db_path) else None

            from code_to_skill.skillopt_loop.separation import resolve_skillopt_backend_ids

            rollout_id, optimizer_id = resolve_skillopt_backend_ids(sim_skillopt, sim_mp)
            m4 = run_skillopt_loop(
                initial_skill=initial_skill,
                benchmark_items=train_items,
                selection_items=[],
                test_items=[],
                output_dir=os.path.join(output_root, "optimization"),
                run_root=output_root,
                code_repos=[
                    {"path": r.path, "include": r.include, "exclude": r.exclude}
                    for r in p.repos
                ],
                graph_db_path=graph_db_path,
                repo_root=repo_root,
                graph_sources=graph_sources,
                num_epochs=sim_skillopt.get("num_epochs", 1),
                batch_size=sim_skillopt.get("batch_size", 2),
                accumulation=sim_skillopt.get("accumulation", 1),
                edit_budget=sim_skillopt.get("edit_budget", 2),
                patience=sim_skillopt.get("patience", 1),
                use_llm_rollout=sim_skillopt.get("use_llm_rollout", True),
                rollout_backend_id=rollout_id,
                optimizer_backend_id=optimizer_id,
                model_provider=sim_mp,
                enable_code_tools=False,
                enable_slow_update=False,
                enable_meta_skill=False,
                token_budgets=sim_skillopt.get("token_budgets"),
            )
            report.phases.append({
                "phase": "m4_skillopt",
                "status": "completed",
                "metrics": {
                    "best_score": m4.get("best_score"),
                    "history_steps": len(m4.get("history") or []),
                },
            })
        except Exception as e:
            report.phases.append({
                "phase": "m4_skillopt", "status": "failed", "error": str(e),
            })
            raise

    summary_path = os.path.join(output_root, "simulate_report.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "simulated_at": local_timestamp(),
            "output_root": output_root,
            "phases": report.phases,
            "warnings": report.warnings,
        }, f, indent=2, ensure_ascii=False)
    _echo(f"  Report: {summary_path}")

    return report
