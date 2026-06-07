"""Reflect helpers 通用化测试。"""
from __future__ import annotations

from code_to_skill.skillopt_loop.reflect_helpers import (
    BOUNDARY_FOCUS,
    PRIMARY_FOCUS,
    is_graph_searchable_check,
    is_numeric_check,
    resolve_reflect_focus,
    skill_compact_for_reflect,
    skill_section_index,
    split_failure_groups,
)


def test_is_graph_searchable_check_filters_short_and_numeric_tokens():
    assert not is_graph_searchable_check("借")
    assert not is_graph_searchable_check("贷")
    assert not is_graph_searchable_check("会计")
    assert not is_graph_searchable_check("金额")
    assert not is_graph_searchable_check("50000")
    assert is_graph_searchable_check("Charge")
    assert not is_graph_searchable_check("会计凭证")
    assert is_graph_searchable_check("无人认领负债")
    assert is_numeric_check("1,234.56")


def test_resolve_reflect_focus_from_response_mode():
    assert resolve_reflect_focus({"response_mode": "answer"}) == PRIMARY_FOCUS
    assert resolve_reflect_focus({"response_mode": "clarify"}) == BOUNDARY_FOCUS
    assert resolve_reflect_focus({"response_mode": "reject"}) == BOUNDARY_FOCUS
    assert resolve_reflect_focus({"reflect_focus": "boundary", "response_mode": "answer"}) == BOUNDARY_FOCUS


def test_split_failure_groups():
    failed = [
        {"id": "a", "response_mode": "answer"},
        {"id": "b", "response_mode": "clarify"},
    ]
    primary, boundary = split_failure_groups(failed)
    assert [r["id"] for r in primary] == ["a"]
    assert [r["id"] for r in boundary] == ["b"]


def test_skill_section_index_from_headers():
    skill = "# Title\n## Workflow\n### Output format\n## Constraints"
    index = skill_section_index(skill)
    assert "## Workflow" in index
    assert "### Output format" in index


def test_skill_compact_prefers_constraint_sections():
    skill = (
        "# Title\n## Intro\nlong intro text\n"
        "## Constraints\nmust clarify when incomplete\n"
        "## Workflow\nstep one\n"
    )
    compact = skill_compact_for_reflect(skill)
    assert "Constraints" in compact
    assert "Intro" not in compact or "long intro" not in compact
