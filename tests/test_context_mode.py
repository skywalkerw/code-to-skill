"""context_mode inline / agent_read / none rollout 行为。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from code_to_skill.skillopt_loop.envs.base import EnvAdapter, DEFAULTAdapter
from code_to_skill.skillopt_loop.rollout_helpers import build_rollout_user_message


def test_build_context_inline_lists_refs():
    item = {
        "question": "What is the risk?",
        "context_refs": ["code://a.py::foo"],
        "context_mode": "inline",
    }
    text = EnvAdapter._build_context_from_item(item)
    assert "Context references:" in text
    assert "code://a.py::foo" in text
    assert "What is the risk?" in text


def test_build_context_agent_read_hints_tools():
    item = {
        "question": "Review this code",
        "context_refs": ["code://b.py::bar"],
        "context_mode": "agent_read",
    }
    text = EnvAdapter._build_context_from_item(item)
    assert "Use code tools to read" in text
    assert "code://b.py::bar" in text
    assert "Review this code" in text


def test_build_context_none_is_question_only():
    item = {
        "question": "Explain idempotency",
        "context_refs": ["code://ignored.py::x"],
        "context_mode": "none",
    }
    text = EnvAdapter._build_context_from_item(item)
    assert text == "Explain idempotency"


def test_rollout_user_message_none_omits_tool_hint():
    msg = build_rollout_user_message(
        "Q", ["token"],
        item={"context_mode": "none"},
    )
    assert "code tools" not in msg.lower()


def test_rollout_user_message_agent_read_mentions_tools():
    msg = build_rollout_user_message(
        "Q", ["token"],
        item={"context_mode": "agent_read"},
    )
    assert "fetch the referenced context" in msg


@patch("code_to_skill.model_provider.tool_loop.invoke_with_tool_loop")
@patch("code_to_skill.skillopt_loop.code_evidence.build_rollout_item_context")
def test_rollout_inline_injects_code_ctx(mock_build_ctx, mock_tool_loop):
    mock_build_ctx.return_value = "\n## Code\nsnippet"
    mock_tool_loop.return_value = MagicMock(content="FINAL: answer token", tool_snippets="")

    adapter = DEFAULTAdapter(use_llm=True, enable_code_tools=True)
    adapter._backend = MagicMock()
    adapter.code_tools = MagicMock(enabled=True)

    item = {
        "id": "i1",
        "question": "Q?",
        "expected_checks": ["token"],
        "context_refs": ["code://a.py::fn"],
        "context_mode": "inline",
    }
    adapter.rollout("skill", [item], target_backend=adapter._backend)
    mock_build_ctx.assert_called_once()


@patch("code_to_skill.model_provider.tool_loop.invoke_with_tool_loop")
@patch("code_to_skill.skillopt_loop.code_evidence.build_rollout_item_context")
def test_rollout_agent_read_skips_code_ctx(mock_build_ctx, mock_tool_loop):
    mock_tool_loop.return_value = MagicMock(content="FINAL: answer token", tool_snippets="")

    adapter = DEFAULTAdapter(use_llm=True, enable_code_tools=True)
    adapter._backend = MagicMock()
    adapter.code_tools = MagicMock(enabled=True)
    adapter.rollout_max_tool_rounds = 2

    item = {
        "id": "i2",
        "question": "Q?",
        "expected_checks": ["token"],
        "context_refs": ["code://a.py::fn"],
        "context_mode": "agent_read",
    }
    adapter.rollout("skill", [item], target_backend=adapter._backend)
    mock_build_ctx.assert_not_called()
    mock_tool_loop.assert_called_once()
    assert mock_tool_loop.call_args.kwargs["max_rounds"] == 2


@patch("code_to_skill.model_provider.tool_loop.invoke_with_tool_loop")
@patch("code_to_skill.skillopt_loop.code_evidence.build_rollout_item_context")
def test_rollout_none_disables_tools(mock_build_ctx, mock_invoke):
    adapter = DEFAULTAdapter(use_llm=True, enable_code_tools=True)
    adapter._backend = MagicMock()
    adapter._backend.invoke.return_value = MagicMock(content="FINAL: answer token", tool_snippets="")
    adapter.code_tools = MagicMock(enabled=True)

    item = {
        "id": "i3",
        "question": "Q?",
        "expected_checks": ["token"],
        "context_mode": "none",
    }
    adapter.rollout("skill", [item], target_backend=adapter._backend)
    mock_build_ctx.assert_not_called()
    mock_invoke.assert_not_called()
    adapter._backend.invoke.assert_called_once()
