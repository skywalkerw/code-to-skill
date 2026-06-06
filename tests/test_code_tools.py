"""代码读取工具测试。"""
import json
from pathlib import Path

import pytest

from code_to_skill.codegraph_mcp.handler import CodeToolsHandler, CodeRepoConfig


REPO = "test-data/sources/repos/fineract"
ACCOUNTING_INCLUDE = ["fineract-provider/src/main/java/org/apache/fineract/accounting/**"]


@pytest.fixture
def handler():
    if not Path(REPO).is_dir():
        pytest.skip("fineract test repo not present")
    return CodeToolsHandler([CodeRepoConfig(path=REPO, include=ACCOUNTING_INCLUDE)])


def test_handler_enabled(handler):
    assert handler.enabled
    assert len(handler.definitions) == 3  # file tools only (no graph.db)


def test_search_code_finds_journalentry(handler):
    raw = handler.execute({
        "function": {"name": "search_code", "arguments": json.dumps({"query": "journalentry"})},
    })
    data = json.loads(raw)
    assert data["results"]
    assert any("journalentry" in r["path"].lower() for r in data["results"])


def test_read_code_file(handler):
    listed = json.loads(handler.execute({
        "function": {"name": "list_code_files", "arguments": json.dumps({
            "pattern": "**/journalentry/**/*.java",
            "max_results": 1,
        })},
    }))
    assert listed["files"]
    path = listed["files"][0]
    raw = handler.execute({
        "function": {"name": "read_code_file", "arguments": json.dumps({"path": path, "end_line": 30})},
    })
    data = json.loads(raw)
    assert "content" in data
    assert data["end_line"] <= 30


def test_path_traversal_blocked(handler):
    raw = handler.execute({
        "function": {"name": "read_code_file", "arguments": json.dumps({"path": "../../../etc/passwd"})},
    })
    data = json.loads(raw)
    assert "error" in data
