"""模块 5 核心数据结构。

所有类型均基于 pydantic v2，强制 schema_version 校验。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── 标准请求 / 响应 ────────────────────────────────────────────

class InteractionRequest(BaseModel):
    """统一的模型/Agent 请求。"""
    schema_version: str = "1.0"
    request_id: str = Field(default_factory=lambda: f"req-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{id(object())}")
    role: Literal["extractor", "clusterer", "optimizer", "target", "judge", "agent_worker"]
    stage: str = ""  # e.g. "skillatom_extract", "rollout", "reflect_failure_minibatch"
    messages: list[dict] = Field(default_factory=list)  # [{"role": "system/user/assistant", "content": "..."}]
    response_format: dict | None = None  # {"type": "json_schema", "schema_name": "...", "schema": {...}}
    tools: list[dict] = Field(default_factory=list)
    attachments: list[dict] = Field(default_factory=list)
    workspace: str | None = None
    timeout_seconds: int = 120
    max_output_tokens: int = 4096
    temperature: float = 0.0
    metadata: dict = Field(default_factory=dict)


class InteractionResponse(BaseModel):
    """统一响应基类。"""
    schema_version: str = "1.0"
    request_id: str
    backend_id: str
    backend_type: Literal["llm_api", "local_llm", "agent_cli", "agent_service", "mcp_agent", "mock"] = "llm_api"
    content: str
    parsed: Any = None
    status: Literal["ok", "error", "parse_error", "timeout", "rate_limited"] = "ok"
    finish_reason: str = "stop"
    latency_ms: int = 0
    usage: dict = Field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})


class ModelResponse(InteractionResponse):
    """裸模型调用响应。"""
    model: str = ""
    tool_calls: list[dict] = Field(default_factory=list)


class AgentResponse(InteractionResponse):
    """Agent CLI/服务调用响应，含执行轨迹。"""
    agent: str = ""
    trajectory: dict = Field(default_factory=lambda: {
        "messages": [], "tool_calls": [], "file_changes": [], "commands": [], "exit_code": 0
    })
    artifacts: list[dict] = Field(default_factory=list)
    estimation_method: Literal["parsed", "api_count", "fixed"] = "fixed"


class HealthStatus(BaseModel):
    """后端健康信号。"""
    schema_version: str = "1.0"
    backend_id: str
    healthy: bool
    latency_ms: int = 0
    error: str | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
