"""Mock 回放后端。

从预置 fixture 返回固定响应，用于测试和离线开发。
"""
from __future__ import annotations

import json
import time
import os
from typing import Any

from . import InteractionBackend
from ..types import InteractionRequest, InteractionResponse, ModelResponse, HealthStatus


class MockReplayBackend(InteractionBackend):
    """从 JSON fixture 文件回放响应。

    目录结构：
        fixtures/mock/<backend_id>/
            <request_id>.json  或者  按 index 编号的 response 列表
    """

    backend_type = "mock"

    def __init__(self, backend_id: str, fixture_dir: str, model: str = "mock-model"):
        self.backend_id = backend_id
        self.fixture_dir = fixture_dir
        self.model = model
        self._call_count = 0
        self._responses: list[dict] = self._load_responses()

    def capabilities(self) -> dict:
        return {
            "chat": True,
            "messages": True,
            "json_schema": True,
            "tool_calling": True,
            "vision": False,
            "structured_output_level": 3,
            "context_window": 1000000,
            "max_output_tokens": 16000,
            "timeout_seconds": 600,
        }

    def invoke(self, request: InteractionRequest) -> InteractionResponse:
        start = time.monotonic()

        if self._call_count < len(self._responses):
            data = self._responses[self._call_count]
        elif self._responses:
            data = self._responses[-1]  # 循环使用最后一个
        else:
            data = {"content": '{"mock": true}', "finish_reason": "stop"}

        self._call_count += 1

        content = str(data.get("content", ""))
        parsed = None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            pass

        return ModelResponse(
            request_id=request.request_id,
            backend_id=self.backend_id,
            model=self.model,
            content=content,
            parsed=parsed,
            finish_reason=data.get("finish_reason", "stop"),
            latency_ms=int((time.monotonic() - start) * 1000),
            usage=data.get("usage", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}),
        )

    def healthcheck(self) -> HealthStatus:
        return HealthStatus(backend_id=self.backend_id, healthy=True)

    def _load_responses(self) -> list[dict]:
        """加载 fixture 目录下的所有响应。"""
        responses: list[dict] = []
        if not os.path.isdir(self.fixture_dir):
            return responses

        for fname in sorted(os.listdir(self.fixture_dir)):
            fpath = os.path.join(self.fixture_dir, fname)
            if fname.endswith(".json"):
                with open(fpath) as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        responses.extend(data)
                    else:
                        responses.append(data)

        return responses
