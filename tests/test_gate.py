"""Gate 门禁测试。"""
from code_to_skill.skillopt_loop.gate import GateManager
from code_to_skill.skillopt_loop.reflect_helpers import split_failure_groups


class TestGateTrainAware:
    def test_train_improved_accepts_when_selection_flat(self):
        gate = GateManager(metric="soft", delta=0.01)
        decision = gate.evaluate(
            0.167, 0.708, best_score=0.708, current_score=0.708,
            train_rollout=0.959, prev_train_rollout=0.844,
        )
        assert decision.action == "accept"
        assert "train_improved" in decision.reason

    def test_reject_when_train_flat(self):
        gate = GateManager(metric="soft", delta=0.01)
        decision = gate.evaluate(
            0.167, 0.708, best_score=0.708, current_score=0.708,
            train_rollout=0.850, prev_train_rollout=0.844,
        )
        assert decision.action == "reject"

    def test_new_best_unchanged(self):
        gate = GateManager(metric="soft", delta=0.01)
        decision = gate.evaluate(0.5, 0.80, best_score=0.708, current_score=0.708)
        assert decision.action == "accept_new_best"


class TestReflectSplit:
    def test_split_failure_groups(self):
        primary, boundary = split_failure_groups([
            {"id": "task_a", "response_mode": "answer", "missed_checks": ["token_a", "token_b"]},
            {"id": "task_b", "response_mode": "clarify", "missed_checks": ["clarify", "missing"]},
            {"id": "task_c", "response_mode": "reject", "missed_checks": ["reject", "invalid"]},
        ])
        assert len(primary) == 1
        assert primary[0]["id"] == "task_a"
        assert len(boundary) == 2
        assert {r["id"] for r in boundary} == {"task_b", "task_c"}
