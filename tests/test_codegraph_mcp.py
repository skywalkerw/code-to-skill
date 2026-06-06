"""CodeGraph MCP 模块测试（不启动 stdio 服务）。"""
from __future__ import annotations

import os

import pytest

from code_to_skill.codegraph_mcp.registry_holder import get_registry, invalidate_registry


@pytest.fixture
def graph_env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
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
    monkeypatch.setenv("CODEGRAPH_DB_PATH", db_path)
    monkeypatch.setenv("CODEGRAPH_REPO_ROOT", str(repo))
    invalidate_registry()
    return db_path, str(repo)


def test_get_registry(graph_env):
    reg = get_registry()
    hits = reg.search("Hello", limit=5)
    assert any(h["name"] == "Hello" for h in hits)


def test_mcp_status_payload(graph_env):
    reg = get_registry()
    stats = reg.stats()
    assert stats["total_nodes"] > 0
    assert os.path.isfile(os.environ["CODEGRAPH_DB_PATH"])


def test_registry_list_files(graph_env):
    reg = get_registry()
    files = reg.list_files(pattern="**/*.java", limit=10)
    assert any("Hello.java" in f["path"] for f in files)


def test_registry_callers_callees(graph_env):
    reg = get_registry()
    callees = reg.callees_of("Hello", depth=1)
    assert "node_id" in callees or "error" in callees


def test_registry_holder_invalidate(graph_env):
    r1 = get_registry()
    invalidate_registry()
    r2 = get_registry()
    assert r1 is not r2
