"""OpenAI 兼容后端。

适配百炼 DashScope、vLLM、Ollama 等兼容 OpenAI API 的服务。
"""
from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI

from . import InteractionBackend
from ..types import InteractionRequest, InteractionResponse, ModelResponse, HealthStatus


class OpenAICompatibleBackend(InteractionBackend):
    """OpenAI API 兼容后端。

    通过 base_url 指向任意兼容服务（百炼、vLLM、Ollama 等）。
    """

    backend_type = "llm_api"

    def __init__(self, backend_id: str, base_url: str, api_key: str, model: str,
                 context_window: int = 128000, timeout_seconds: int = 180):
        self.backend_id = backend_id
        self.model = model
        self.context_window = context_window
        self.timeout_seconds = timeout_seconds

        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_seconds,
        )

    # ── InteractionBackend 接口 ──────────────────────────────

    def capabilities(self) -> dict:
        return {
            "chat": True,
            "messages": True,
            "json_schema": self._supports_native_json_schema(),
            "tool_calling": True,
            "vision": False,
            "structured_output_level": 1,  # L1: prompt-based (safer for all backends)
            "context_window": self.context_window,
            "max_output_tokens": 16000,
            "timeout_seconds": self.timeout_seconds,
        }

    def invoke(self, request: InteractionRequest) -> InteractionResponse:
        start = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": self._normalize_messages(request.messages),
                "max_tokens": request.max_output_tokens,
                "temperature": request.temperature,
            }

            # 结构化输出 / 降级处理由 structured_output.py 负责，
            # 本后端只做最基础的 text generation
            if request.response_format and self._supports_native_json_schema():
                kwargs["response_format"] = request.response_format

            completion = self._client.chat.completions.create(**kwargs)
            choice = completion.choices[0]

            latency_ms = int((time.monotonic() - start) * 1000)

            content = choice.message.content or ""

            # 尝试解析 JSON
            parsed = None
            if request.response_format and content.strip():
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
                finish_reason=choice.finish_reason or "stop",
                latency_ms=latency_ms,
                usage={
                    "prompt_tokens": completion.usage.prompt_tokens if completion.usage else 0,
                    "completion_tokens": completion.usage.completion_tokens if completion.usage else 0,
                    "total_tokens": completion.usage.total_tokens if completion.usage else 0,
                },
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return ModelResponse(
                request_id=request.request_id,
                backend_id=self.backend_id,
                model=self.model,
                content="",
                status="error",
                finish_reason="error",
                latency_ms=latency_ms,
                usage={},
            )

    def healthcheck(self) -> HealthStatus:
        start = time.monotonic()
        try:
            # 发一个极短请求验证连通性
            self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return HealthStatus(
                backend_id=self.backend_id,
                healthy=True,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as exc:
            return HealthStatus(
                backend_id=self.backend_id,
                healthy=False,
                latency_ms=int((time.monotonic() - start) * 1000),
                error=str(exc),
            )

    # ── 内部 ──────────────────────────────────────────────

    @staticmethod
    def _normalize_messages(messages: list[dict]) -> list[dict]:
        """确保 messages 格式兼容。"""
        normalized = []
        for msg in messages:
            role = msg.get("role", "user")
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"
            normalized.append({"role": role, "content": str(msg.get("content", ""))})
        return normalized

    @staticmethod
    def _supports_native_json_schema() -> bool:
        """判断后端是否原生支持 JSON Schema structured output。

        百炼 DashScope 的 deepseek-v4-pro 支持 response_format，
        vLLM/Ollama 视版本而定。默认返回 True，子类可覆盖。
        """
        return True
