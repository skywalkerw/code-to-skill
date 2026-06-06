"""GraphWatcher 测试。"""
from __future__ import annotations

import pytest

from code_to_skill.code_graph.watcher import _is_source_file, IncrementalGraphWatcher


def test_is_source_file():
    assert _is_source_file("/proj/src/Foo.java")
    assert not _is_source_file("/proj/target/Foo.class")
    assert not _is_source_file("/proj/.git/config")


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("watchdog") is None,
    reason="watchdog not installed",
)
def test_watcher_start_stop(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "out"
    w = IncrementalGraphWatcher(str(repo), str(out), debounce_sec=0.1)
    w.start()
    w.stop()
