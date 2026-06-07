"""自定义框架模式（project YAML）测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from code_to_skill.cli.config_loader import load_config
from code_to_skill.cli.graph_config import resolve_framework_patterns
from code_to_skill.code_graph import run_code_graph_pipeline
from code_to_skill.code_graph.framework import (
    extract_framework_metadata,
    merge_custom_patterns,
    parse_custom_patterns,
)


def test_parse_custom_patterns():
    raw = {
        "fineract": {
            "CommandHandler": "command_handler",
            "AccountingProcessor": "accounting_processor",
        },
    }
    parsed = parse_custom_patterns(raw)
    assert parsed["fineract"]["CommandHandler"] == "command_handler"


def test_merge_custom_patterns_repo_overrides_project():
    merged = merge_custom_patterns(
        {"fineract": {"A": "role_a", "B": "role_b"}},
        {"fineract": {"B": "role_b2", "C": "role_c"}},
    )
    assert merged["fineract"]["A"] == "role_a"
    assert merged["fineract"]["B"] == "role_b2"
    assert merged["fineract"]["C"] == "role_c"


def test_extract_custom_framework_nodes(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "com" / "example"
    pkg.mkdir(parents=True)
    java_path = "com/example/LoanCommandHandler.java"
    (pkg / "LoanCommandHandler.java").write_text(
        """package com.example;
public class LoanCommandHandler implements CommandHandler {
    public void process() {}
}
""",
        encoding="utf-8",
    )
    nodes, _ = extract_framework_metadata(
        [java_path],
        str(repo),
        custom_patterns={"fineract": {"CommandHandler": "command_handler"}},
    )
    custom = [n for n in nodes if n.metadata.get("custom")]
    assert custom
    assert any(n.metadata.get("role") == "command_handler" for n in custom)


def test_config_test_yaml_loads_fineract_patterns():
    cfg_path = Path("config.yaml")
    if not cfg_path.is_file():
        pytest.skip("config.yaml missing")
    cfg = load_config(str(cfg_path))
    patterns = resolve_framework_patterns(cfg.project, cfg.project.repos[0])
    assert "fineract" in patterns
    assert "AccountingProcessor" in patterns["fineract"]


def test_pipeline_applies_custom_patterns(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "FooApiResource.java").write_text(
        "package com.example;\npublic class FooApiResource implements ApiResource {}\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = run_code_graph_pipeline(
        repo_root=str(repo),
        include=["**/*.java"],
        output_root=str(out),
        use_cache=True,
        custom_patterns={"app": {"ApiResource": "api_resource"}},
    )
    custom = [
        n for n in result["graph"].nodes
        if n.metadata.get("custom") and n.metadata.get("role") == "api_resource"
    ]
    assert custom
