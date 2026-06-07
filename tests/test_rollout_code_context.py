"""rollout 代码上下文预取测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_to_skill.skillopt_loop.code_evidence import build_rollout_item_context
from code_to_skill.codegraph_mcp.handler import CodeToolsHandler, CodeRepoConfig


def test_rollout_context_empty_without_graph():
    assert build_rollout_item_context({"context_refs": ["a.java"]}, None) == ""


def test_rollout_context_from_context_ref(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "com" / "example"
    pkg.mkdir(parents=True)
    rel = "com/example/LoanProc.java"
    (repo / rel).write_text(
        "package com.example;\npublic class LoanProc {\n"
        "  public void disburse() { /* debit loan credit cash */ }\n}\n",
        encoding="utf-8",
    )
    from code_to_skill.code_graph import run_code_graph_pipeline

    out = tmp_path / "g"
    run_code_graph_pipeline(
        repo_root=str(repo),
        include=["**/*.java"],
        output_root=str(out),
        use_cache=True,
    )

    handler = CodeToolsHandler(
        repos=[CodeRepoConfig(path=str(repo), include=["**/*.java"])],
        graph_db_path=str(out / "graph.db"),
        repo_root=str(repo),
    )
    item = {
        "id": "jv_loan_001",
        "question": "发放贷款",
        "context_refs": [rel + "#disburse"],
    }
    ctx = build_rollout_item_context(item, handler)
    assert "Project code reference" in ctx
    assert "disburse" in ctx.lower() or "LoanProc" in ctx
