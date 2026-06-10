"""Reflect step buffer 与 rejected buffer 注入测试。"""
from code_to_skill.skillopt_loop.reflect_helpers import summarize_step_buffer_for_reflect
from code_to_skill.skillopt_loop.types import EditOp


def test_summarize_rejected_buffer_records():
    summary = summarize_step_buffer_for_reflect(
        [
            {
                "type": "rejected_buffer",
                "record": {
                    "reason": "no_improvement",
                    "content": "When handling journal entries, verify balance.",
                    "before_score": 0.72,
                    "after_score": 0.70,
                },
            },
        ],
    )
    assert "Gate-rejected" in summary
    assert "no_improvement" in summary
    assert "verify balance" in summary


def test_summarize_rejected_edit_in_step_buffer():
    summary = summarize_step_buffer_for_reflect(
        [{"type": "rejected_edit", "edit": EditOp(op="append", content="bad rule")}],
    )
    assert "step_reject" in summary
    assert "bad rule" in summary
