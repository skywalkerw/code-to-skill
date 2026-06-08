"""L3 full-simulate 测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from code_to_skill.cli.config_loader import (
    AppConfig,
    DocSource,
    ProjectConfig,
    RepoSource,
    SettingsConfig,
)
from code_to_skill.cli.full_simulate import (
    build_simulate_model_provider,
    run_full_simulate,
    simulate_fixture_dir,
    simulate_llm_env,
)
from code_to_skill.model_provider.llm_backend import create_llm_backend, is_llm_available


def test_simulate_fixture_dir_exists():
    assert (simulate_fixture_dir() / "responses.json").is_file()


def test_build_simulate_model_provider_forces_mock():
    mp = build_simulate_model_provider({"routes": {"target": {"primary": "deepseek"}}})
    assert mp["routes"]["target"]["primary"] == "mock-backend"
    assert mp["backends"]["mock-backend"]["fixture_dir"]


def test_simulate_llm_backend():
    with simulate_llm_env():
        assert is_llm_available()
        backend = create_llm_backend()
        assert backend.backend_id == "mock-backend"
        assert backend._responses


def test_run_full_simulate_minimal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Handler.java").write_text(
        "package example;\npublic class Handler { void retry() {} }\n",
        encoding="utf-8",
    )
    doc = tmp_path / "guide.md"
    doc.write_text("# Guide\n\nAlways check idempotency.\n", encoding="utf-8")

    cfg = AppConfig(
        settings=SettingsConfig(output_root=str(tmp_path / "runs")),
        project=ProjectConfig(
            name="sim-test",
            repos=[
                RepoSource(id="r1", path=str(repo), include=["*.java"]),
            ],
            docs=[DocSource(id="d1", path=str(doc), type="markdown")],
        ),
    )
    report = run_full_simulate(cfg)
    assert report.output_root
    assert any(p["phase"] == "m4_skillopt" and p["status"] == "completed" for p in report.phases)
    assert (Path(report.output_root) / "optimization" / "best_skill.md").is_file()
    assert (Path(report.output_root) / "simulate_report.json").is_file()
