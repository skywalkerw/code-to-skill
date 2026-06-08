"""L2 static-analysis 校验。"""
from __future__ import annotations

from pathlib import Path

from code_to_skill.cli.config_loader import (
    AppConfig,
    DocSource,
    ProjectConfig,
    RepoSource,
    SettingsConfig,
)
from code_to_skill.cli.static_analysis import run_static_analysis


def test_static_analysis_repo_and_doc(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Hello.java").write_text(
        "package example;\npublic class Hello { void run() {} }\n",
        encoding="utf-8",
    )
    doc = tmp_path / "readme.md"
    doc.write_text("# Title\n\nSome content.\n", encoding="utf-8")

    cfg = AppConfig(
        settings=SettingsConfig(),
        project=ProjectConfig(
            name="t",
            repos=[
                RepoSource(
                    id="r1",
                    path=str(repo),
                    include=["*.java"],
                ),
            ],
            docs=[
                DocSource(
                    id="d1",
                    path=str(doc),
                    type="markdown",
                ),
            ],
        ),
    )
    report = run_static_analysis(cfg)
    assert len(report.repo_reports) == 1
    assert report.repo_reports[0]["nodes"] >= 1
    assert len(report.doc_reports) == 1
    assert report.doc_reports[0]["ok"] is True
    lines = report.format_lines()
    assert any("L2 static-analysis" in ln for ln in lines)
