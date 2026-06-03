"""M5 类型与后端验证测试。"""
import pytest
from src.model_gateway.types import InteractionRequest, InteractionResponse, ModelResponse
from src.model_gateway.backends.mock import MockReplayBackend
from src.model_gateway.router import Router


class TestTypes:
    def test_interaction_request_defaults(self):
        req = InteractionRequest(role="extractor", stage="test")
        assert req.schema_version == "1.0"
        assert req.request_id.startswith("req-")

    def test_model_response(self):
        resp = ModelResponse(
            request_id="req-001",
            backend_id="mock",
            model="gpt-4",
            content='{"key": 1}',
        )
        assert resp.schema_version == "1.0"
        assert resp.parsed is None  # 自动解析 TODO


class TestMockBackend:
    def test_healthcheck(self):
        backend = MockReplayBackend("mock-1", fixture_dir="/nonexistent")
        status = backend.healthcheck()
        assert status.healthy is True

    def test_invoke_no_fixtures(self):
        backend = MockReplayBackend("mock-1", fixture_dir="/nonexistent")
        req = InteractionRequest(role="optimizer", stage="reflect_test")
        resp = backend.invoke(req)
        assert resp.status == "ok"
        assert '"mock": true' in resp.content


class TestRouter:
    def test_resolve(self):
        router = Router(
            route_config={"optimizer": {"primary": "dashscope", "fallback": ["azure"]}},
            backends={},
        )
        candidates = router.resolve("optimizer")
        assert candidates == ["dashscope", "azure"]
