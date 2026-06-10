"""Slow update gate hygiene tests."""
from __future__ import annotations

from code_to_skill.skillopt_loop.slow_update import (
    has_actionable_slow_update_signal,
    is_non_actionable_slow_update,
    run_slow_update,
)


class _StableSuccessAdapter:
    def rollout(self, skill, items, target_backend=None):
        return [{"id": item["id"], "hard": 1, "soft": 1.0} for item in items]


def test_stable_success_only_skips_slow_update():
    result = run_slow_update(
        "previous",
        "current",
        [{"id": "item-1"}, {"id": "item-2"}],
        adapter=_StableSuccessAdapter(),
    )
    assert result["slow_update_content"] == ""
    assert result["action"] == "skip_stable_success_only"
    assert result["comparison_pairs"] == {
        "improved": 0,
        "regressed": 0,
        "persistent_fail": 0,
        "stable_success": 2,
    }


def test_actionable_signal_requires_changes_or_failures():
    assert not has_actionable_slow_update_signal({"stable_success": 15})
    assert has_actionable_slow_update_signal({"persistent_fail": 1, "stable_success": 14})
    assert has_actionable_slow_update_signal({"regressed": 1})


def test_non_actionable_slow_update_detection():
    assert is_non_actionable_slow_update("The skill definition remained unchanged; no changes needed.")
    assert not is_non_actionable_slow_update("Add a rule: when transfer is requested, include transfer evidence.")
