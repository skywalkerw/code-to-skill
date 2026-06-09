"""Rollout 并行执行测试。"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from code_to_skill.skillopt_loop.envs.base import DEFAULTAdapter


def _make_items(n: int) -> list[dict]:
    return [
        {
            "id": f"item_{i}",
            "question": f"task {i}",
            "expected_checks": [f"token_{i}"],
        }
        for i in range(n)
    ]


def test_rollout_parallel_preserves_order():
    adapter = DEFAULTAdapter(use_llm=True, enable_code_tools=False, rollout_workers=4)
    backend = MagicMock()
    backend.invoke.return_value = MagicMock(content="FINAL: token", tool_snippets="")
    items = _make_items(6)

    results = adapter.rollout("skill", items, target_backend=backend)

    assert [r["id"] for r in results] == [item["id"] for item in items]
    assert backend.invoke.call_count == len(items)


@patch("code_to_skill.skillopt_loop.envs.base.DEFAULTAdapter._rollout_single_item")
def test_rollout_serial_when_workers_one(mock_single):
    mock_single.return_value = {"id": "x"}

    adapter = DEFAULTAdapter(rollout_workers=1)
    items = _make_items(3)
    adapter.rollout("skill", items)

    assert mock_single.call_count == 3


def test_rollout_parallel_runs_concurrently():
    lock = threading.Lock()
    active = 0
    peak = 0

    def slow_invoke(*_args, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.08)
        with lock:
            active -= 1
        return MagicMock(content="FINAL: token", tool_snippets="")

    backend = MagicMock()
    backend.invoke.side_effect = slow_invoke

    adapter = DEFAULTAdapter(use_llm=True, enable_code_tools=False, rollout_workers=4)
    items = _make_items(8)

    t0 = time.monotonic()
    adapter.rollout("skill", items, target_backend=backend)
    elapsed = time.monotonic() - t0

    assert peak >= 2
    assert elapsed < 8 * 0.08
