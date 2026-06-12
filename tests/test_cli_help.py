"""CLI --help / -h 文案测试。"""
from __future__ import annotations

from click.testing import CliRunner

from code_to_skill.cli.main import main


def test_main_help_lists_top_level_commands():
    result = CliRunner().invoke(main, ["-h"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "tool" in result.output
    assert "codegraph" in result.output
    assert "skill-lab run all -h" in result.output


def test_run_help_lists_subcommands():
    result = CliRunner().invoke(main, ["run", "-h"])
    assert result.exit_code == 0
    assert "all" in result.output
    assert "optimize-skill" in result.output
    assert "code-graph" in result.output
    assert "skill-lab run <子命令> -h" in result.output


def test_run_all_help_shows_pipeline_options():
    result = CliRunner().invoke(main, ["run", "all", "-h"])
    assert result.exit_code == 0
    assert "--config-path" in result.output
    assert "--resume-run-id" in result.output
    assert "M1" in result.output or "code-graph" in result.output


def test_codegraph_help_lists_tools():
    result = CliRunner().invoke(main, ["codegraph", "-h"])
    assert result.exit_code == 0
    assert "search" in result.output
    assert "trace" in result.output


def test_tool_code_help_lists_tools():
    result = CliRunner().invoke(main, ["tool", "code", "-h"])
    assert result.exit_code == 0
    assert "search-code" in result.output
    assert "read-code-file" in result.output
