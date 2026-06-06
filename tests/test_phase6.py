"""Phase 6：MyBatis XML、JS 回调、图谱工具扩展。"""
from __future__ import annotations

from pathlib import Path

import pytest

from code_to_skill.code_graph import run_code_graph_pipeline
from code_to_skill.code_graph.js_callbacks import synthesize_js_callbacks
from code_to_skill.code_graph.mybatis_xml import extract_mybatis_xml
from code_to_skill.code_graph.types import CodeGraph, EdgeKind, GraphNode, NodeKind
from code_to_skill.codegraph_mcp.handler import CodeToolsHandler


@pytest.fixture
def mybatis_repo(tmp_path):
    repo = tmp_path / "repo"
    java = repo / "com" / "example"
    java.mkdir(parents=True)
    (java / "OrderMapper.java").write_text(
        """package com.example;
import org.apache.ibatis.annotations.Mapper;
@Mapper
public interface OrderMapper {
    Order findById(Long id);
}
""",
        encoding="utf-8",
    )
    xml_dir = repo / "mappers"
    xml_dir.mkdir()
    (xml_dir / "OrderMapper.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mapper namespace="com.example.OrderMapper">
  <select id="findById" resultType="Order">
    SELECT * FROM orders WHERE id = #{id}
  </select>
  <insert id="insertOrder">
    INSERT INTO orders VALUES (#{id})
  </insert>
</mapper>
""",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = run_code_graph_pipeline(
        repo_root=str(repo),
        include=["**/*"],
        output_root=str(out),
        use_cache=True,
    )
    return str(repo), str(out / "graph.db"), result["graph"]


def test_mybatis_xml_extraction(mybatis_repo):
    _, _, graph = mybatis_repo
    stmts = [n for n in graph.nodes if n.metadata.get("framework") == "mybatis" and n.language == "xml"]
    names = {n.name for n in stmts}
    assert "findById" in names
    assert "insertOrder" in names
    refs = [e for e in graph.edges if e.kind == EdgeKind.references and "findById" in e.target]
    assert refs


def test_js_callback_synthesis():
    graph = CodeGraph(
        nodes=[
            GraphNode(id="app.js", kind=NodeKind.file, name="app.js", file_path="app.js", language="javascript"),
            GraphNode(id="app.js::handleClick", kind=NodeKind.function, name="handleClick", file_path="app.js", language="javascript"),
            GraphNode(id="app.js::init", kind=NodeKind.function, name="init", file_path="app.js", language="javascript"),
        ],
        edges=[],
    )
    repo = Path("/tmp/js_cb_test")
    repo.mkdir(exist_ok=True)
    (repo / "app.js").write_text(
        "function init() { btn.addEventListener('click', handleClick); }\n"
        "function handleClick() {}\n",
        encoding="utf-8",
    )
    syn = synthesize_js_callbacks(graph, str(repo))
    assert syn
    assert any(e.target == "app.js::handleClick" for e in syn)


def test_code_tools_graph_extensions(mybatis_repo):
    repo_root, db_path, _ = mybatis_repo
    handler = CodeToolsHandler(
        [{"path": repo_root}],
        graph_db_path=db_path,
        repo_root=repo_root,
    )
    assert len(handler.definitions) == 14
    import json
    status = json.loads(handler.execute({
        "function": {"name": "graph_status", "arguments": "{}"},
    }))
    assert status["total_nodes"] > 0
