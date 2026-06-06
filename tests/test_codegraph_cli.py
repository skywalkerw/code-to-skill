"""CodeGraph CLI 测试。"""
from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from code_to_skill.cli.main import main


@pytest.fixture
def graph_cli_env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    pkg = repo / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "Hello.java").write_text(
        "package com.example;\npublic class Hello { public void run() {} }\n",
        encoding="utf-8",
    )
    from code_to_skill.code_graph import run_code_graph_pipeline

    out = tmp_path / "out"
    run_code_graph_pipeline(
        repo_root=str(repo),
        include=["**/*.java"],
        output_root=str(out),
        use_cache=True,
    )
    db_path = str(out / "graph.db")
    return db_path, str(repo)


def test_codegraph_help():
    runner = CliRunner()
    result = runner.invoke(main, ["codegraph", "-h"])
    assert result.exit_code == 0
    assert "search" in result.output
    assert "context" in result.output
    assert "trace" in result.output
    assert "示例" in result.output or "skill-lab codegraph" in result.output


def test_codegraph_search_with_db(graph_cli_env):
    db_path, repo_root = graph_cli_env
    runner = CliRunner()
    result = runner.invoke(main, [
        "codegraph", "search", "Hello",
        "--db", db_path,
        "--repo-root", repo_root,
        "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert any(h.get("name") == "Hello" for h in data)


def test_codegraph_status_brief(graph_cli_env):
    db_path, repo_root = graph_cli_env
    runner = CliRunner()
    result = runner.invoke(main, [
        "codegraph", "status",
        "--db", db_path,
        "--repo-root", repo_root,
        "--format", "brief",
    ])
    assert result.exit_code == 0
    assert "总节点" in result.output or "nodes" in result.output.lower()


def test_codegraph_explore(graph_cli_env):
    db_path, repo_root = graph_cli_env
    runner = CliRunner()
    result = runner.invoke(main, [
        "codegraph", "explore", "Hello",
        "--db", db_path,
        "--repo-root", repo_root,
        "--format", "pretty",
    ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data.get("name") == "Hello" or data.get("symbol") == "Hello"


def test_codegraph_missing_db():
    runner = CliRunner()
    result = runner.invoke(main, [
        "codegraph", "status",
        "--db", "/nonexistent/graph.db",
    ])
    assert result.exit_code != 0
    assert "不存在" in result.output or "graph.db" in result.output
