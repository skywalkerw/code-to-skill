"""Pure tool CLI tests."""
from __future__ import annotations

import json

from click.testing import CliRunner

from code_to_skill.cli.main import main


def test_tool_code_file_tools(tmp_path):
    repo = tmp_path / "repo"
    pkg = repo / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "Hello.java").write_text(
        "package com.example;\npublic class Hello { public void run() {} }\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    search = runner.invoke(main, [
        "tool", "code", "search-code", "Hello",
        "--repo-root", str(repo),
        "--format", "json",
    ])
    assert search.exit_code == 0, search.output
    assert json.loads(search.output)["results"]

    read = runner.invoke(main, [
        "tool", "code", "read-code-file", "com/example/Hello.java",
        "--repo-root", str(repo),
        "--end-line", "2",
        "--format", "json",
    ])
    assert read.exit_code == 0, read.output
    assert "public class Hello" in json.loads(read.output)["content"]


def test_tool_code_search_symbol_with_db(tmp_path):
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

    result = CliRunner().invoke(main, [
        "tool", "code", "search-symbol", "Hello",
        "--db", str(out / "graph.db"),
        "--repo-root", str(repo),
        "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert any(row.get("name") == "Hello" for row in data["results"])
