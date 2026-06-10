"""pipeline_config 产物发现与 skip 策略测试。"""
from __future__ import annotations

import json
import os

import pytest

from code_to_skill.cli.config_loader import DocSource, ProjectConfig
from code_to_skill.cli.pipeline_config import (
    build_artifact_contract,
    build_runtime_config_report,
    discover_pipeline_artifacts,
    format_runtime_config_log_lines,
    load_leaf_contexts_from_run,
    log_runtime_config,
    parse_pipeline_settings,
    should_skip_m2,
    should_skip_m3,
    write_artifact_contract,
)


def test_parse_pipeline_settings_defaults():
    settings = parse_pipeline_settings({})
    assert settings.write_artifact_contract is True
    assert settings.run_atoms_when_benchmark_present is False


def test_discover_pipeline_artifacts(tmp_path):
    run_root = tmp_path / "run1"
    code_root = run_root / "sources" / "code" / "myrepo" / "HEAD"
    code_root.mkdir(parents=True)
    (code_root / "graph.db").write_text("")
    (code_root / "entrypoints.json").write_text("[]")
    ctx_dir = code_root / "leaf_contexts"
    ctx_dir.mkdir()
    (ctx_dir / "leaf-1.json").write_text(json.dumps({"leaf_id": "leaf-1", "content": "x"}))

    artifacts = discover_pipeline_artifacts(str(run_root))
    assert artifacts.run_root == str(run_root)
    assert len(artifacts.graphs) == 1
    assert artifacts.graphs[0].graph_db.present is True
    assert artifacts.graphs[0].entrypoints.present is True


def test_load_leaf_contexts_from_run(tmp_path):
    run_root = tmp_path / "run2"
    ctx_dir = run_root / "sources" / "code" / "r" / "HEAD" / "leaf_contexts"
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "a.json").write_text(json.dumps({"leaf_id": "a"}))
    loaded = load_leaf_contexts_from_run(str(run_root))
    assert len(loaded) == 1
    assert loaded[0]["leaf_id"] == "a"


def test_artifact_contract_includes_quality(tmp_path):
    atoms = tmp_path / "atoms"
    atoms.mkdir()
    aq = {
        "passed": True,
        "seeds_total": 5,
        "source_ref_resolve_rate": 0.95,
    }
    (atoms / "artifact_quality.json").write_text(
        __import__("json").dumps(aq), encoding="utf-8",
    )
    artifacts = discover_pipeline_artifacts(str(tmp_path))
    contract = build_artifact_contract(artifacts)
    assert contract.get("artifact_quality", {}).get("passed") is True


def test_artifact_contract_write(tmp_path):
    artifacts = discover_pipeline_artifacts(str(tmp_path))
    contract = build_artifact_contract(artifacts)
    opt_dir = tmp_path / "optimization"
    path = write_artifact_contract(str(opt_dir), contract)
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["schema_version"] == "1.0"


def test_should_skip_m3_with_benchmark(tmp_path, monkeypatch):
    bench = tmp_path / "benchmark"
    (bench / "train").mkdir(parents=True)
    (bench / "train" / "items.json").write_text(
        json.dumps({"items": [{"id": "t1", "question": "q"}]}),
    )
    skill = tmp_path / "skill.md"
    skill.write_text("# skill")

    project = ProjectConfig(
        initial_skill_path=str(skill),
        benchmark_path=str(bench),
    )
    pipeline = parse_pipeline_settings({})
    assert should_skip_m3(project, pipeline) is True
    assert should_skip_m3(project, pipeline, with_atoms=True) is False


def test_runtime_config_log_lines(tmp_path, caplog):
    import logging

    bench = tmp_path / "benchmark"
    (bench / "train").mkdir(parents=True)
    (bench / "train" / "items.json").write_text(
        json.dumps({"items": [{"id": "t1", "question": "q"}]}),
    )
    skill = tmp_path / "skill.md"
    skill.write_text("# skill")

    class _Settings:
        code_graph = {}
        document_normalizer = {}
        atom_extractor = {}
        skillopt = {"num_epochs": 2, "batch_size": 5, "use_llm_rollout": True}
        model_provider = {"backends": {}, "routes": {}}
        pipeline = {}
        self_evolution = {"enabled": False}

    class _Project:
        name = "demo"
        domain = "fineract"
        initial_skill_path = str(skill)
        benchmark_path = str(bench)
        repos = []
        docs = []

    report = build_runtime_config_report(
        _Settings(),
        _Project(),
        config_path="config.yaml",
        output_root=str(tmp_path / "run1"),
        run_flags={"skip_m3": True},
    )
    lines = format_runtime_config_log_lines(report)
    assert any("config_path=" in line for line in lines)
    assert any("skillopt:" in line for line in lines)
    assert any("run_flags:" in line for line in lines)

    with caplog.at_level(logging.INFO):
        log_runtime_config(report)
    assert "Effective runtime configuration" in caplog.text
    assert "num_epochs=2" in caplog.text


def test_should_skip_m2_when_m3_skipped(tmp_path):
    project = ProjectConfig(docs=[DocSource(id="d", path="x.md", type="markdown")])
    pipeline = parse_pipeline_settings({})
    assert should_skip_m2(project, pipeline, skip_m3=True) is True
    assert should_skip_m2(project, pipeline, skip_m3=True, with_docs=True) is False
