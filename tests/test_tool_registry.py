"""CodeGraph 工具注册表 — SkillOpt / MCP parity。"""
from __future__ import annotations

import json

import pytest

from code_to_skill.codegraph_mcp.tool_registry import (
    CODEGRAPH_TOOL_SPECS,
    execute_graph_tool,
    resolve_tool,
    skillopt_tool_definitions,
    skillopt_tool_names,
)
from code_to_skill.codegraph_mcp.registry_holder import invalidate_registry
from code_to_skill.codegraph_mcp.handler import CodeToolsHandler


@pytest.fixture
def graph_env(tmp_path, monkeypatch):
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
    monkeypatch.setenv("CODEGRAPH_DB_PATH", db_path)
    monkeypatch.setenv("CODEGRAPH_REPO_ROOT", str(repo))
    invalidate_registry()
    return db_path, str(repo)


def test_skillopt_definitions_match_registry():
    names = {d["function"]["name"] for d in skillopt_tool_definitions()}
    assert names == set(skillopt_tool_names())
    assert len(CODEGRAPH_TOOL_SPECS) == 11


def test_mcp_and_skillopt_names_unique():
    mcp_names = {s.mcp_name for s in CODEGRAPH_TOOL_SPECS}
    skill_names = {s.skillopt_name for s in CODEGRAPH_TOOL_SPECS}
    assert len(mcp_names) == len(CODEGRAPH_TOOL_SPECS)
    assert len(skill_names) == len(CODEGRAPH_TOOL_SPECS)
    assert "codegraph_node" in mcp_names
    assert "get_symbol_node" in skill_names


def test_resolve_accepts_both_names():
    assert resolve_tool("search_symbol") is not None
    assert resolve_tool("codegraph_search") is not None
    assert resolve_tool("get_symbol_node") is not None
    assert resolve_tool("codegraph_node") is not None


def test_execute_via_skillopt_handler(graph_env):
    db_path, repo_root = graph_env
    handler = CodeToolsHandler(
        [{"path": repo_root}],
        graph_db_path=db_path,
        repo_root=repo_root,
    )
    raw = handler.execute({
        "function": {
            "name": "get_symbol_node",
            "arguments": json.dumps({"symbol": "Hello"}),
        },
    })
    data = json.loads(raw)
    assert data.get("name") == "Hello"
    assert data.get("kind") == "class"


def test_execute_mcp_alias_via_handler(graph_env):
    db_path, repo_root = graph_env
    handler = CodeToolsHandler(
        [{"path": repo_root}],
        graph_db_path=db_path,
        repo_root=repo_root,
    )
    raw = handler.execute({
        "function": {
            "name": "codegraph_search",
            "arguments": json.dumps({"query": "Hello", "limit": 5}),
        },
    })
    data = json.loads(raw)
    assert any(r.get("name") == "Hello" for r in data.get("results", []))
