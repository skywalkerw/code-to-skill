"""Tests for eval_hygiene rollout wrapper."""
from code_to_skill.skillopt_loop.eval_hygiene import rollout_with_hygiene
from code_to_skill.skillopt_loop.output_hygiene import OutputHygieneConfig


class _StubAdapter:
    def rollout(self, skill, items, target_backend=None, out_dir=""):
        return [
            {"id": "a", "hard": 1, "soft": 1.0, "predicted_answer": "ok"},
            {"id": "b", "hard": 1, "soft": 1.0, "predicted_answer": "Task: echo"},
        ]


def test_rollout_with_hygiene_hard_fail_echo():
    hygiene = OutputHygieneConfig(enabled=True, hard_fail_on_persistent_echo=True)
    results, hard, soft = rollout_with_hygiene(
        _StubAdapter(),
        "skill",
        [{"id": "a"}, {"id": "b"}],
        hygiene_cfg=hygiene,
    )
    assert results[1]["hard"] == 0
    assert hard == 0.5
