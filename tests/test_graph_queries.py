"""图查询引擎与 ContextBuilder 测试。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from code_to_skill.code_graph import run_code_graph_pipeline
from code_to_skill.code_graph.context_builder import ContextBuilder
from code_to_skill.code_graph.db import GraphDB
from code_to_skill.code_graph.graph_queries import GraphQueryEngine, extract_search_terms
from code_to_skill.code_graph.query_parser import parse_query
from code_to_skill.codegraph_mcp.handler import CodeToolsHandler


def _write_mini_repo(root: Path) -> None:
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
    public void charge(String orderId) {
        validate(orderId);
    }

    private void validate(String orderId) {}
}
""",
        encoding="utf-8",
    )


@pytest.fixture
def mini_graph(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_mini_repo(repo)
    out = tmp_path / "out"
    run_code_graph_pipeline(
        repo_root=str(repo),
        include=["**/*.java"],
        exclude=[],
        output_root=str(out),
        use_cache=True,
    )
    db_path = out / "graph.db"
    assert db_path.is_file()
    return str(repo), str(db_path)


class TestExtractSearchTerms:
    def test_camel_and_snake(self):
        terms = extract_search_terms("How does OrderService call payment_service?")
        assert "OrderService" in terms


class TestQueryParser:
    def test_kind_and_file_filters(self):
        parsed = parse_query("kind:class file:**/*.java OrderService")
        assert "class" in parsed.kinds
        assert parsed.file_patterns == ["**/*.java"]
        assert "OrderService" in parsed.terms


class TestGraphQueryEngine:
    def test_search_finds_class(self, mini_graph):
        _, db_path = mini_graph
        engine = GraphQueryEngine.from_path(db_path)
        hits = engine.search("OrderService", limit=10)
        names = {h["name"] for h in hits}
        assert "OrderService" in names

    def test_search_with_kind_filter(self, mini_graph):
        _, db_path = mini_graph
        engine = GraphQueryEngine.from_path(db_path)
        hits = engine.search("kind:method placeOrder", limit=10)
        assert hits
        assert all(h["kind"] == "method" for h in hits)

    def test_qualified_name_in_db(self, mini_graph):
        _, db_path = mini_graph
        engine = GraphQueryEngine.from_path(db_path)
        hits = engine.search("OrderService", limit=5)
        qnames = [h.get("qualified_name", "") for h in hits if h["name"] == "OrderService"]
        assert any("OrderService" in q for q in qnames)

    def test_callers_callees(self, mini_graph):
        _, db_path = mini_graph
        engine = GraphQueryEngine.from_path(db_path)
        nodes = engine.find_by_name("placeOrder", exact=False)
        assert nodes
        node = nodes[0]
        callees = engine.callees(node.id, depth=1)
        callee_names = {c["name"] for c in callees}
        assert "charge" in callee_names or "PaymentService" in callee_names

    def test_stats(self, mini_graph):
        _, db_path = mini_graph
        stats = GraphQueryEngine.from_path(db_path).stats()
        assert stats["nodes"] > 0
        assert stats["files"] >= 2


class TestContextBuilder:
    def test_build_blocks(self, mini_graph):
        repo_root, db_path = mini_graph
        engine = GraphQueryEngine.from_path(db_path)
        ctx = ContextBuilder(engine, repo_root).build("OrderService", max_blocks=4)
        assert ctx["blocks"]
        assert any("OrderService" in (b.get("symbol") or "") for b in ctx["blocks"])
        md = ContextBuilder(engine, repo_root).format_markdown(ctx)
        assert "OrderService" in md


class TestManifestOutput:
    def test_manifest_written(self, mini_graph):
        _, db_path = mini_graph
        out = Path(db_path).parent
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == "1.0"
        assert manifest["stats"]["nodes"] > 0
        assert (out / "file_inventory.json").is_file()


class TestIncrementalCache:
    def test_cache_reload_skips_parse(self, mini_graph, tmp_path, monkeypatch):
        repo_root, db_path = mini_graph
        calls: list = []

        import code_to_skill.code_graph as cg

        orig = cg.parse_files

        def spy(files, root):
            calls.append(list(files))
            return orig(files, root)

        monkeypatch.setattr(cg, "parse_files", spy)
        out = str(Path(db_path).parent)
        run_code_graph_pipeline(
            repo_root=repo_root,
            include=["**/*.java"],
            output_root=out,
            use_cache=True,
        )
        assert calls == []


class TestCodeToolsGraphIntegration:
    def test_handler_exposes_graph_tools(self, mini_graph):
        repo_root, db_path = mini_graph
        handler = CodeToolsHandler(
            [{"path": repo_root, "include": ["**/*.java"]}],
            graph_db_path=db_path,
            repo_root=repo_root,
        )
        assert handler.graph_enabled
        assert len(handler.definitions) == 14

    def test_search_symbol_tool(self, mini_graph):
        repo_root, db_path = mini_graph
        handler = CodeToolsHandler(
            [{"path": repo_root}],
            graph_db_path=db_path,
            repo_root=repo_root,
        )
        raw = handler.execute({
            "function": {
                "name": "search_symbol",
                "arguments": json.dumps({"query": "PaymentService"}),
            },
        })
        data = json.loads(raw)
        assert data["results"]
        assert any(r["name"] == "PaymentService" for r in data["results"])
