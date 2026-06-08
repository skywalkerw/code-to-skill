"""CodeToolsHandler 工厂构造。"""
from __future__ import annotations

from code_to_skill.codegraph_mcp.handler import (
    CodeToolsHandler,
    build_code_tools_handler,
)


def test_build_code_tools_handler_disabled():
    handler = build_code_tools_handler(
        [{"path": "/tmp/repo"}],
        enable_code_tools=False,
        graph_db_path="/tmp/graph.db",
    )
    assert isinstance(handler, CodeToolsHandler)
    assert handler.repos == []
    assert handler.graph_db_path == "/tmp/graph.db"


def test_build_code_tools_handler_with_graph():
    handler = build_code_tools_handler(
        [{"path": "/tmp/repo", "include": ["**/*.py"]}],
        graph_db_path="/data/graph.db",
        repo_root="/tmp/repo",
    )
    assert len(handler.repos) == 1
    assert handler.graph_db_path == "/data/graph.db"
    assert handler.repo_root == "/tmp/repo"
    assert handler.graph_sources[0]["db_path"] == "/data/graph.db"
