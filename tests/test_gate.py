"""Gate 门禁测试。"""
from code_to_skill.skillopt_loop.gate import GateManager


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
        from code_to_skill.skillopt_loop.llm_components import _split_failure_groups

        journal, boundary = _split_failure_groups([
            {"id": "jv_purchase_001", "missed_checks": ["库存", "银行"]},
            {"id": "jv_incomplete_001", "missed_checks": ["待确认", "缺少"]},
            {"id": "jv_constraint_001", "missed_checks": ["不平", "isBalanced"]},
        ])
        assert len(journal) == 1
        assert journal[0]["id"] == "jv_purchase_001"
        assert len(boundary) == 2
        assert {r["id"] for r in boundary} == {"jv_incomplete_001", "jv_constraint_001"}
