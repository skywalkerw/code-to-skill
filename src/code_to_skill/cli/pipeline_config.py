"""流水线产物发现与 settings.pipeline 契约。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from code_to_skill.time_utils import local_timestamp


class PipelineSettings(BaseModel):
    """settings.pipeline — 编排与 M4 产物契约开关。"""

    write_artifact_contract: bool = True
    validate_context_refs: bool = True
    run_atoms_when_benchmark_present: bool = False
    run_docs_when_atoms_skipped: bool = False
    merge_atom_seeds_into_benchmark: bool = False
    append_atom_rules_to_skill: bool = False
    bootstrap_min_confidence: float = 0.8
    use_evidence_index: bool = True
    use_entrypoints: bool = True
    use_role_index: bool = True
    auto_plot_training_curve: bool = True


def parse_pipeline_settings(raw: dict | None) -> PipelineSettings:
    if not raw:
        return PipelineSettings()
    return PipelineSettings(**{k: v for k, v in raw.items() if k in PipelineSettings.model_fields})


@dataclass
class ModuleRunSettings:
    """从 ``settings`` 解析 M1/M2/M3 模块参数（CLI 单点传入）。"""

    code_graph: dict[str, Any]
    document_normalizer: dict[str, Any]
    atom_extractor: dict[str, Any]

    @classmethod
    def from_settings(cls, settings: Any) -> "ModuleRunSettings":
        if hasattr(settings, "model_dump"):
            data = settings.model_dump()
        elif isinstance(settings, dict):
            data = settings
        else:
            data = {}
        return cls(
            code_graph=dict(data.get("code_graph") or {}),
            document_normalizer=dict(data.get("document_normalizer") or {}),
            atom_extractor=dict(data.get("atom_extractor") or {}),
        )

    def code_graph_pipeline_kwargs(self) -> dict[str, Any]:
        cg = self.code_graph
        return {
            "max_leaf_tokens": int(cg.get("max_leaf_tokens", 8000)),
            "max_module_depth": int(cg.get("max_module_depth", 3)),
            "use_cache": bool(cg.get("use_cache", True)),
            "code_graph_settings": cg,
        }

    def normalize_document_kwargs(self) -> dict[str, Any]:
        dn = self.document_normalizer
        return {
            "max_chunk_tokens": int(dn.get("max_chunk_tokens", 2000)),
            "normalizer_settings": dn,
        }


def _self_evolution_wired(settings: Any) -> dict[str, Any]:
    se = (
        settings.self_evolution if hasattr(settings, "self_evolution")
        else (settings.get("self_evolution") if isinstance(settings, dict) else {})
    ) or {}
    gate = se.get("gate") or {}
    return {
        "enabled": bool(se.get("enabled", False)),
        "trace_pool": bool((se.get("trace_pool") or {}).get("enabled", True)),
        "strict_gate": bool(gate.get("strict_improvement", True)),
        "frontier": bool(gate.get("frontier_enabled", False)),
        "attribution": bool((se.get("attribution") or {}).get("enabled", True)),
    }


def build_effective_settings_report(
    settings: Any,
    project: Any | None = None,
) -> dict[str, Any]:
    """汇总已接线配置项（``config validate`` / run_manifest 用）。"""
    ms = ModuleRunSettings.from_settings(settings)
    pipeline_raw = (
        settings.pipeline
        if hasattr(settings, "pipeline")
        else (settings.get("pipeline") if isinstance(settings, dict) else {})
    )
    pipe = parse_pipeline_settings(pipeline_raw)
    cg = ms.code_graph_pipeline_kwargs()
    dn = ms.normalize_document_kwargs()
    cg_raw = cg.get("code_graph_settings") or {}

    from code_to_skill.skillopt_loop.separation import resolve_judge_backend_id

    skillopt = (
        settings.skillopt if hasattr(settings, "skillopt")
        else (settings.get("skillopt") if isinstance(settings, dict) else {})
    )
    mp = (
        settings.model_provider if hasattr(settings, "model_provider")
        else (settings.get("model_provider") if isinstance(settings, dict) else {})
    )
    mp_dump = mp.model_dump() if hasattr(mp, "model_dump") else (mp or {})
    judge_id = resolve_judge_backend_id(skillopt, mp_dump)

    reflect_error = ""
    reflect_success = ""
    if project is not None:
        prompts = getattr(project, "reflect_prompts", None) or {}
        reflect_error = bool((prompts.get("error") or "").strip())
        reflect_success = bool((prompts.get("success") or "").strip())

    wired = {
        "m1": {
            "split_strategy": cg_raw.get("split_strategy", "top_dir"),
            "max_leaf_tokens": cg_raw.get("max_leaf_tokens", 8000),
            "max_module_depth": cg_raw.get("max_module_depth", 3),
            "llm_clustering_enabled": cg_raw.get("llm_clustering_enabled", False),
            "use_cache": cg.get("use_cache", True),
        },
        "m2": {
            "max_chunk_tokens": dn["max_chunk_tokens"],
            "ocr_engine": (dn.get("normalizer_settings") or {}).get("ocr_engine", ""),
        },
        "m3": {
            "confidence_tier_1_max": ms.atom_extractor.get("confidence_tier_1_max", 0.95),
            "llm_adjustment": ms.atom_extractor.get("llm_adjustment", 0.05),
        },
        "m4": {
            "judge_backend": judge_id or "(none)",
            "reflect_prompts_error": reflect_error,
            "reflect_prompts_success": reflect_success,
            "graph_role_hints": bool(getattr(project, "graph_role_hints", None)) if project else False,
        },
        "self_evolution": _self_evolution_wired(settings),
        "pipeline": pipe.model_dump(),
    }
    reserved = {
        "code_graph.llm_clustering_enabled": "logged only; rule clustering used",
        "model_provider.routes.agent_worker": "reserved; not in default pipeline",
    }
    return {"wired": wired, "reserved": reserved}


def format_effective_settings_lines(report: dict[str, Any]) -> list[str]:
    """可打印的生效配置行。"""
    lines = ["Effective settings (wired to CLI/modules):"]
    wired = report.get("wired") or {}
    for module, fields in wired.items():
        if not isinstance(fields, dict):
            continue
        parts = ", ".join(f"{k}={v}" for k, v in fields.items())
        lines.append(f"  {module}: {parts}")
    reserved = report.get("reserved") or {}
    if reserved:
        lines.append("Reserved (YAML present, limited/no wiring):")
        for key, note in reserved.items():
            lines.append(f"  {key}: {note}")
    return lines


@dataclass
class ArtifactRef:
    """单个产物文件引用。"""

    name: str
    path: str
    present: bool


@dataclass
class GraphArtifacts:
    repo_id: str = ""
    repo_ref: str = ""
    repo_root: str = ""
    graph_db: ArtifactRef = field(default_factory=lambda: ArtifactRef("graph_db", "", False))
    entrypoints: ArtifactRef = field(default_factory=lambda: ArtifactRef("entrypoints", "", False))
    graph_json: ArtifactRef = field(default_factory=lambda: ArtifactRef("graph_json", "", False))
    role_index: ArtifactRef = field(default_factory=lambda: ArtifactRef("role_index", "", False))
    module_tree: ArtifactRef = field(default_factory=lambda: ArtifactRef("module_tree", "", False))
    leaf_contexts_dir: ArtifactRef = field(
        default_factory=lambda: ArtifactRef("leaf_contexts", "", False),
    )


@dataclass
class PipelineArtifacts:
    run_root: str
    optimization_dir: str
    atoms_dir: str
    graphs: list[GraphArtifacts] = field(default_factory=list)
    evidence_index: ArtifactRef = field(
        default_factory=lambda: ArtifactRef("evidence_index", "", False),
    )
    merged_atoms: ArtifactRef = field(
        default_factory=lambda: ArtifactRef("merged_atoms", "", False),
    )
    artifact_quality: ArtifactRef = field(
        default_factory=lambda: ArtifactRef("artifact_quality", "", False),
    )


def _artifact_ref(name: str, path: str) -> ArtifactRef:
    return ArtifactRef(name=name, path=path, present=bool(path and os.path.isfile(path)))


def _artifact_dir(name: str, path: str) -> ArtifactRef:
    return ArtifactRef(name=name, path=path, present=bool(path and os.path.isdir(path)))


def discover_graph_artifacts(
    code_output_root: str,
    *,
    repo_id: str = "",
    repo_ref: str = "HEAD",
    repo_root: str = "",
) -> GraphArtifacts:
    """发现单个 M1 代码图谱产物目录中的 sidecar 文件。"""
    root = code_output_root
    return GraphArtifacts(
        repo_id=repo_id,
        repo_ref=repo_ref,
        repo_root=repo_root,
        graph_db=_artifact_ref("graph_db", os.path.join(root, "graph.db")),
        entrypoints=_artifact_ref("entrypoints", os.path.join(root, "entrypoints.json")),
        graph_json=_artifact_ref("graph_json", os.path.join(root, "graph.json")),
        role_index=_artifact_ref("role_index", os.path.join(root, "role_index.json")),
        module_tree=_artifact_ref("module_tree", os.path.join(root, "module_tree.json")),
        leaf_contexts_dir=_artifact_dir("leaf_contexts", os.path.join(root, "leaf_contexts")),
    )


def discover_pipeline_artifacts(
    run_root: str,
    *,
    repos: list[dict] | None = None,
) -> PipelineArtifacts:
    """从 run 目录发现 M1/M3/M4 产物路径。

    ``repos`` 每项含 ``id``, ``ref``, ``path``（与 ``RepoSource`` 对齐）。
    """
    run_root = os.path.abspath(run_root)
    atoms_dir = os.path.join(run_root, "atoms")
    graphs: list[GraphArtifacts] = []

    for repo in repos or []:
        code_root = os.path.join(
            run_root, "sources", "code", repo["id"], repo.get("ref", "HEAD"),
        )
        if os.path.isdir(code_root) or os.path.isfile(os.path.join(code_root, "graph.db")):
            graphs.append(discover_graph_artifacts(
                code_root,
                repo_id=repo["id"],
                repo_ref=repo.get("ref", "HEAD"),
                repo_root=repo.get("path", ""),
            ))

    if not graphs:
        code_sources = os.path.join(run_root, "sources", "code")
        if os.path.isdir(code_sources):
            for repo_id in sorted(os.listdir(code_sources)):
                repo_path = os.path.join(code_sources, repo_id)
                if not os.path.isdir(repo_path):
                    continue
                for ref in sorted(os.listdir(repo_path)):
                    code_root = os.path.join(repo_path, ref)
                    if os.path.isdir(code_root):
                        graphs.append(discover_graph_artifacts(code_root, repo_id=repo_id, repo_ref=ref))

    return PipelineArtifacts(
        run_root=run_root,
        optimization_dir=os.path.join(run_root, "optimization"),
        atoms_dir=atoms_dir,
        graphs=graphs,
        evidence_index=_artifact_ref(
            "evidence_index", os.path.join(atoms_dir, "evidence_index.json"),
        ),
        merged_atoms=_artifact_ref(
            "merged_atoms", os.path.join(atoms_dir, "merged_atoms.jsonl"),
        ),
        artifact_quality=_artifact_ref(
            "artifact_quality", os.path.join(atoms_dir, "artifact_quality.json"),
        ),
    )


def build_artifact_contract(
    artifacts: PipelineArtifacts,
    *,
    pipeline_settings: PipelineSettings | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成 ``optimization/artifact_contract.json`` 载荷。"""
    settings = pipeline_settings or PipelineSettings()
    contract: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": local_timestamp(),
        "run_root": artifacts.run_root,
        "optimization_dir": artifacts.optimization_dir,
        "atoms_dir": artifacts.atoms_dir,
        "pipeline_settings": settings.model_dump(),
        "graphs": [asdict(g) for g in artifacts.graphs],
        "atoms": {
            "evidence_index": asdict(artifacts.evidence_index),
            "merged_atoms": asdict(artifacts.merged_atoms),
            "artifact_quality": asdict(artifacts.artifact_quality),
        },
    }
    if extra:
        contract.update(extra)
    aq_path = artifacts.artifact_quality.path
    if aq_path and os.path.isfile(aq_path):
        try:
            with open(aq_path, encoding="utf-8") as f:
                contract["artifact_quality"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            contract["artifact_quality"] = {"present": True, "parsed": False}
    return contract


def write_artifact_contract(
    output_dir: str,
    contract: dict[str, Any],
) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "artifact_contract.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(contract, f, indent=2, ensure_ascii=False)
    return path


def load_leaf_contexts_from_run(run_root: str) -> list[dict]:
    """从 run 目录加载 M1 leaf_contexts JSON 文件。"""
    contexts: list[dict] = []
    code_root = os.path.join(run_root, "sources", "code")
    if not os.path.isdir(code_root):
        return contexts
    for repo_id in sorted(os.listdir(code_root)):
        repo_path = os.path.join(code_root, repo_id)
        if not os.path.isdir(repo_path):
            continue
        for ref in sorted(os.listdir(repo_path)):
            ctx_dir = os.path.join(repo_path, ref, "leaf_contexts")
            if not os.path.isdir(ctx_dir):
                continue
            for fname in sorted(os.listdir(ctx_dir)):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(ctx_dir, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        contexts.append(json.load(f))
                except (OSError, json.JSONDecodeError):
                    continue
    return contexts


def load_document_chunks_from_run(run_root: str) -> list[dict]:
    """从 run 目录加载 M2 document chunk 产物。"""
    chunks: list[dict] = []
    docs_root = os.path.join(run_root, "sources", "docs")
    if not os.path.isdir(docs_root):
        return chunks
    for doc_id in sorted(os.listdir(docs_root)):
        doc_path = os.path.join(docs_root, doc_id)
        if not os.path.isdir(doc_path):
            continue
        for version in sorted(os.listdir(doc_path)):
            chunks_dir = os.path.join(doc_path, version, "chunks")
            if not os.path.isdir(chunks_dir):
                continue
            for fname in sorted(os.listdir(chunks_dir)):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(chunks_dir, fname)
                try:
                    with open(fpath, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        chunks.append(data)
                except (OSError, json.JSONDecodeError):
                    continue
    return chunks


def project_has_initial_skill(project) -> bool:
    path = getattr(project, "initial_skill_path", "") or ""
    return bool(path and os.path.isfile(path))


def project_has_benchmark_train(project, benchmark_dir: str | None = None) -> bool:
    from code_to_skill.skillopt_loop.benchmark_splits import BenchmarkSplits

    path = benchmark_dir or getattr(project, "benchmark_path", "") or ""
    if not path:
        return False
    splits = BenchmarkSplits.from_dir(path)
    return bool(splits.train)


def should_skip_m3(
    project,
    pipeline: PipelineSettings,
    *,
    with_atoms: bool = False,
    benchmark_dir: str | None = None,
) -> bool:
    """有 initial_skill + benchmark train 时默认跳过 M3。"""
    if with_atoms or pipeline.run_atoms_when_benchmark_present:
        return False
    if not project_has_initial_skill(project):
        return False
    if not project_has_benchmark_train(project, benchmark_dir):
        return False
    return True


def should_skip_m2(
    project,
    pipeline: PipelineSettings,
    *,
    skip_m3: bool,
    with_docs: bool = False,
    with_atoms: bool = False,
) -> bool:
    if not skip_m3:
        return False
    if with_docs or with_atoms:
        return False
    if pipeline.run_docs_when_atoms_skipped:
        return False
    if not getattr(project, "docs", None):
        return True
    return True
