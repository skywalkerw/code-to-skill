"""Rollout 辅助与 tool 降级测试（通用，不含目标项目领域知识）。"""
from __future__ import annotations

import json

from code_to_skill.skillopt_loop.rollout_helpers import (
    build_rollout_synthesis_hint,
    build_rollout_system_prompt,
    build_rollout_user_message,
    extract_rollout_answer,
    fallback_predicted_from_tools,
    fallback_skill_voucher,
    sanitize_skill_for_rollout,
)


def test_sanitize_skill_strips_placeholder_example():
    skill = (
        "### Rules\n```markdown\n| col | (placeholder) | TBD |\n```\n"
        "- Map purchase to inventory / bank\n"
    )
    cleaned = sanitize_skill_for_rollout(skill)
    assert "(placeholder)" not in cleaned
    assert "inventory" in cleaned


def test_system_prompt_is_domain_agnostic():
    prompt = build_rollout_system_prompt(
        "## Skill\n- Use concrete values, not placeholders.",
        code_tools_enabled=False,
    )
    assert "skill document" in prompt.lower()
    assert "(placeholder)" not in prompt


def test_user_message_includes_checks():
    msg = build_rollout_user_message(
        "deploy service X",
        ["output", "health", "check"],
    )
    assert "deploy service X" in msg
    assert "health" in msg
    assert "verification checks" in msg


def test_user_message_clarify_mode():
    msg = build_rollout_user_message(
        "buy supplies",
        ["clarify", "amount", "missing"],
        item={"response_mode": "clarify"},
    )
    assert "clarification" in msg.lower()
    assert "amount" in msg


def test_user_message_reject_mode():
    msg = build_rollout_user_message(
        "invalid payload",
        ["reject", "constraint"],
        item={"response_mode": "reject"},
    )
    assert "refuse" in msg.lower()
    assert "constraint" in msg


def test_user_message_supports_rollout_hint_from_item():
    msg = build_rollout_user_message(
        "task A",
        ["token_a"],
        item={"rollout_hint": "Include TOKEN_A in the summary line."},
    )
    assert "TOKEN_A" in msg


def test_synthesis_hint_lists_expected_checks():
    hint = build_rollout_synthesis_hint(["alpha", "beta"])
    assert "alpha" in hint
    assert "beta" in hint


def test_extract_rollout_answer_strips_skill_echo():
    raw = (
        "## Result\n\n| key | value |\n| --- | --- |\n| a | 1 |\n\n"
        "# Project Skill Title\nrepeated body"
    )
    cleaned = extract_rollout_answer(raw)
    assert "key" in cleaned
    assert "Project Skill Title" not in cleaned


def test_fallback_skill_voucher_uses_checks_and_skill():
    predicted = fallback_skill_voucher(
        "run job J-42",
        ["job", "status"],
        "# skill\nJob status must be reported.",
    )
    assert "J-42" in predicted
    assert "job" in predicted.lower()


def test_fallback_predicted_from_tools_has_structure():
    tool_raw = json.dumps({
        "results": [{"path": "Foo.java", "snippet": "Handler"}],
    })
    predicted = fallback_predicted_from_tools(
        tool_raw,
        "process request R-1",
        ["output", "request", "R-1"],
        "# skill\nEmit structured output for each request.",
    )
    assert "R-1" in predicted
    assert "Checks:" in predicted
    assert "Handler" in predicted or "Foo.java" in predicted
