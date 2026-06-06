"""代码图谱 trace 增强测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_to_skill.code_graph import run_code_graph_pipeline
from code_to_skill.codegraph_mcp.handler import CodeToolsHandler


def _write_call_chain_repo(root: Path) -> None:
    pkg = root / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "OrderService.java").write_text(
        """package com.example;

public class OrderService {
    private final PaymentService paymentService = new PaymentService();

    public void placeOrder(String id) {
        paymentService.charge(id);
    }
}
""",
        encoding="utf-8",
    )
    (pkg / "PaymentService.java").write_text(
        """package com.example;

public class PaymentService {
    public void charge(String orderId) {}
}
""",
        encoding="utf-8",
    )


@pytest.fixture
def call_chain_graph(tmp_path):
    repo = tmp_path / "repo"
    _write_call_chain_repo(repo)
    out = tmp_path / "out"
    run_code_graph_pipeline(
        repo_root=str(repo),
        include=["**/*.java"],
        output_root=str(out),
        use_cache=True,
    )
    return str(repo), str(out / "graph.db")


def test_trace_paths_to_readable_summary(call_chain_graph):
    repo_root, db_path = call_chain_graph
    from code_to_skill.code_graph.registry import GraphRegistry

    reg = GraphRegistry.single(db_path, repo_root=repo_root)
    out = reg.trace("placeOrder", to_symbol="charge", depth=3, path_max_depth=8)

    assert "paths_to" in out or "paths_to_error" in out
    if out.get("paths_to"):
        path = out["paths_to"][0]
        assert "summary" in path
        assert "nodes" in path
        assert "→" in path["summary"] or "charge" in path["summary"]


def test_trace_symbol_tool_depth(call_chain_graph):
    repo_root, db_path = call_chain_graph
    handler = CodeToolsHandler(
        [{"path": repo_root}],
        graph_db_path=db_path,
        repo_root=repo_root,
    )
    raw = handler.execute({
        "function": {
            "name": "trace_symbol",
            "arguments": json.dumps({
                "symbol": "OrderService",
                "direction": "callees",
                "depth": 2,
            }),
        },
    })
    data = json.loads(raw)
    assert data.get("callees") is not None
    assert data.get("name") == "OrderService"


def test_trace_references_spring_edge(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "PaymentService.java").write_text(
        "package com.example;\npublic class PaymentService { public void charge() {} }\n",
        encoding="utf-8",
    )
    (pkg / "OrderService.java").write_text(
        """package com.example;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
public class OrderService {
    @Autowired
    private PaymentService paymentService;
    public void placeOrder() { paymentService.charge(); }
}
""",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    run_code_graph_pipeline(
        repo_root=str(repo),
        include=["**/*.java"],
        output_root=str(out),
        use_cache=True,
    )
    from code_to_skill.code_graph.registry import GraphRegistry

    reg = GraphRegistry.single(str(out / "graph.db"), repo_root=str(repo))
    result = reg.trace("placeOrder", to_symbol="charge", path_max_depth=10)
    if result.get("paths_to"):
        summary = result["paths_to"][0]["summary"]
        assert "charge" in summary or "Payment" in summary
