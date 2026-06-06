"""Phase 12: 深度上下文 / 会计链接 / React RENDERS。"""
from __future__ import annotations

import os
import tempfile

from code_to_skill.code_graph import run_code_graph_pipeline
from code_to_skill.code_graph.react_renders import synthesize_react_renders
from code_to_skill.code_graph.registry import GraphRegistry
from code_to_skill.code_graph.types import CodeGraph, GraphNode, NodeKind
from code_to_skill.skillopt_loop.accounting_linker import graph_queries_for_failure


def test_accounting_linker_queries():
    qs = graph_queries_for_failure({
        "id": "jv_loan_disburse_001",
        "question": "发放贷款",
        "missed_checks": ["贷款", "发放"],
    })
    assert any("disburse" in q.lower() or "loan" in q.lower() for q in qs)


def test_react_renders_synthesis():
    graph = CodeGraph(nodes=[
        GraphNode(id="App.tsx", kind=NodeKind.file, name="App.tsx", file_path="App.tsx", language="typescript"),
        GraphNode(id="App.tsx::Dashboard", kind=NodeKind.class_, name="Dashboard", file_path="Dashboard.tsx", language="typescript"),
    ], edges=[])
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "App.tsx"), "w").write("import Dashboard from './Dashboard';\nexport default () => <Dashboard />;\n")
        open(os.path.join(d, "Dashboard.tsx"), "w").write("export default class Dashboard {}\n")
        edges = synthesize_react_renders(graph, d)
        assert isinstance(edges, list)


def test_build_deep_context(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "Svc.java").write_text(
        "package com.example;\npublic class Svc { public void run() {} }\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    run_code_graph_pipeline(
        repo_root=str(repo), include=["**/*.java"], output_root=str(out), use_cache=True,
    )
    reg = GraphRegistry.single(str(out / "graph.db"), repo_root=str(repo))
    ctx = reg.build_context("Svc", max_blocks=3, deep=True)
    assert ctx.get("blocks") or ctx.get("explored")
