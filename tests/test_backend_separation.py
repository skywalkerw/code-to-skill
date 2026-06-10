"""Backend 分离与 skillopt backend 解析测试。"""
from __future__ import annotations

from code_to_skill.skillopt_loop.separation import resolve_skillopt_backend_ids


def test_resolve_from_skillopt_explicit():
    rollout, optimizer = resolve_skillopt_backend_ids(
        {"rollout_backend": "qwen-local", "optimizer_backend": "deepseek"},
        None,
    )
    assert rollout == "qwen-local"
    assert optimizer == "deepseek"


def test_resolve_from_model_provider_routes():
    rollout, optimizer = resolve_skillopt_backend_ids(
        {},
        {
            "routes": {
                "target": {"primary": "qwen-local"},
                "optimizer": {"primary": "deepseek"},
            },
        },
    )
    assert rollout == "qwen-local"
    assert optimizer == "deepseek"


def test_resolve_from_pydantic_like_model_provider():
    class _Route:
        def __init__(self, primary: str):
            self.primary = primary

    class _ModelProvider:
        routes = {
            "target": _Route("deepseek-flash"),
            "optimizer": _Route("deepseek"),
        }

    rollout, optimizer = resolve_skillopt_backend_ids({}, _ModelProvider())
    assert rollout == "deepseek-flash"
    assert optimizer == "deepseek"


def test_skillopt_overrides_routes():
    rollout, optimizer = resolve_skillopt_backend_ids(
        {"rollout_backend": "mock-backend"},
        {"routes": {"target": {"primary": "qwen-local"}, "optimizer": {"primary": "deepseek"}}},
    )
    assert rollout == "mock-backend"
    assert optimizer == "deepseek"
