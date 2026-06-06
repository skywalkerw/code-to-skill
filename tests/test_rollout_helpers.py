"""Rollout 辅助与 tool 降级测试。"""
from __future__ import annotations

import json

from code_to_skill.skillopt_loop.rollout_helpers import (
    build_rollout_user_message,
    fallback_predicted_from_tools,
)
from code_to_skill.skillopt_loop.scoring import score_rollout_result


def test_user_message_includes_checks():
    msg = build_rollout_user_message("发放贷款 50000", ["会计凭证", "借", "贷"])
    assert "50000" in msg
    assert "会计凭证" in msg


def test_fallback_has_voucher_structure():
    tool_raw = json.dumps({
        "results": [{"path": "Foo.java", "snippet": "JournalEntry"}],
    })
    predicted = fallback_predicted_from_tools(
        tool_raw,
        "向客户发放贷款 50000.00",
        ["会计凭证", "借", "贷", "贷款", "50000", "发放"],
        "# skill\n贷款发放记借贷款贷现金",
    )
    assert "## 会计凭证" in predicted
    assert "借贷校验" in predicted
    assert "50000" in predicted
