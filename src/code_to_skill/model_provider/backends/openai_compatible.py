"""OpenAI 兼容后端。

适配 DeepSeek、OpenAI、vLLM、Ollama 等兼容 OpenAI API 的服务。
"""
from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI

from . import InteractionBackend
from ..types import InteractionRequest, InteractionResponse, ModelResponse, HealthStatus
from ..tracker import log_llm_input, log_llm_output
from ..tracer import record_interaction


def _extract_tool_calls(message: Any) -> list[dict]:
    """将 OpenAI message.tool_calls 转为统一 dict 列表。"""
    raw = getattr(message, "tool_calls", None) or []
    out: list[dict] = []
    for tc in raw:
        fn = getattr(tc, "function", None)
        out.append({
            "id": getattr(tc, "id", ""),
            "type": getattr(tc, "type", "function"),
            "function": {
                "name": getattr(fn, "name", "") if fn else "",
                "arguments": getattr(fn, "arguments", "") if fn else "",
            },
        })
    return out


class OpenAICompatibleBackend(InteractionBackend):
    """OpenAI API 兼容后端。

    通过 base_url 指向任意兼容服务（百炼、vLLM、Ollama 等）。
    Client 采用懒初始化，即使 API key 缺失也不会在构造时崩溃。
    """

    backend_type = "llm_api"
    provider = "openai_compatible"

    def __init__(self, backend_id: str, base_url: str, api_key: str, model: str,
                 context_window: int = 128000, max_output_tokens: int = 16384,
                 timeout_seconds: int = 180):
        self.backend_id = backend_id
        self.model = model
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds

        self._base_url = base_url
        self._api_key = api_key
        self._client: OpenAI | None = None

    def _get_client(self) -> OpenAI:
        """懒初始化 OpenAI client。"""
        if self._client is None:
            self._client = OpenAI(
                base_url=self._base_url,
                api_key=self._api_key,
                timeout=self.timeout_seconds,
            )
        return self._client

    # ── InteractionBackend 接口 ──────────────────────────────

    def capabilities(self) -> dict:
        return {
            "chat": True,
            "messages": True,
            "json_schema": self._supports_native_json_schema(),
            "tool_calling": True,
            "vision": False,
            "workspace_execution": False,
            "file_write": False,
            "shell_command": False,
            "returns_trajectory": False,
            "structured_output_level": 1,  # L1: prompt-based (safer for all backends)
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
        }

    def invoke(self, request: InteractionRequest) -> InteractionResponse:
        log_llm_input("openai", request.role, request.stage, self.model,
                      request.messages, request.max_output_tokens)
        start = time.monotonic()
        try:
            effective_max_tokens = min(request.max_output_tokens, self.max_output_tokens)
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": self._normalize_messages(request.messages),
                "max_tokens": effective_max_tokens,
                "temperature": request.temperature,
            }

            # 结构化输出 / 降级处理由 structured_output.py 负责，
            # 本后端只做最基础的 text generation
            if request.response_format and self._supports_native_json_schema():
                kwargs["response_format"] = request.response_format

            if request.tools:
                kwargs["tools"] = request.tools
                kwargs["tool_choice"] = "auto"

            client = self._get_client()
            completion = client.chat.completions.create(**kwargs)
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

            tool_calls = _extract_tool_calls(choice.message)

            response = ModelResponse(
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
                tool_calls=tool_calls,
            )
            log_llm_output("openai", self.model, content, response.usage, latency_ms)
            record_interaction(request, response, backend_id=self.backend_id)
            return response
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            log_llm_output("openai", self.model, f"[ERROR] {exc}",
                           {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                           latency_ms, "error")
            error_response = ModelResponse(
                request_id=request.request_id,
                backend_id=self.backend_id,
                model=self.model,
                content="",
                status="error",
                finish_reason="error",
                latency_ms=latency_ms,
                usage={},
            )
            record_interaction(request, error_response, backend_id=self.backend_id)
            return error_response

    def healthcheck(self) -> HealthStatus:
        start = time.monotonic()
        try:
            if not self._api_key:
                return HealthStatus(
                    backend_id=self.backend_id,
                    healthy=False,
                    latency_ms=0,
                    error="No API key configured",
                )
            client = self._get_client()
            # 发一个极短请求验证连通性
            client.chat.completions.create(
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
        """确保 messages 格式兼容（含 tool / tool_calls）。"""
        normalized: list[dict] = []
        for msg in messages:
            role = msg.get("role", "user")
            if role not in ("system", "user", "assistant", "tool"):
                role = "user"
            out: dict[str, Any] = {"role": role}
            content = msg.get("content")
            if content is not None:
                out["content"] = content if isinstance(content, str) else str(content)
            elif role == "assistant":
                out["content"] = ""
            if role == "assistant" and msg.get("tool_calls"):
                out["tool_calls"] = msg["tool_calls"]
            if role == "tool":
                if msg.get("tool_call_id"):
                    out["tool_call_id"] = msg["tool_call_id"]
                if msg.get("name"):
                    out["name"] = msg["name"]
            normalized.append(out)
        return normalized

    @staticmethod
    def _supports_native_json_schema() -> bool:
        """判断后端是否原生支持 JSON Schema structured output。

        DeepSeek / OpenAI 兼容端点通常支持 response_format；
        vLLM/Ollama 视版本而定。默认返回 True，子类可覆盖。
        """
        return True
