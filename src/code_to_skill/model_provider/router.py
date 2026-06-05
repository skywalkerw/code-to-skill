"""模型路由。

按 role/stage 选择 backend，支持 fallback 链、重试和能力校验。

能力校验规则（对齐 §7.3 路由解析）：
- 请求包含 json_schema response_format → backend 必须有 json_schema 或 tool_calling
- 请求包含 tools → backend 必须有 tool_calling
- 请求包含 workspace → agent 类 backend 必须有 workspace_execution
- 请求包含 attachments（图片等） → backend 必须有 vision
"""
from __future__ import annotations

import logging
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .types import InteractionRequest, InteractionResponse, HealthStatus
from .backends import InteractionBackend

logger = logging.getLogger(__name__)


class CapabilityMismatchError(Exception):
    """后端能力不匹配请求要求。"""
    pass


class Router:
    """根据 role/stage 选择后端并执行调用。"""

    def __init__(self, route_config: dict[str, Any], backends: dict[str, InteractionBackend]):
        """
        Args:
            route_config: 路由配置，格式 {"optimizer": {"primary": "deepseek", "fallback": ["azure-gpt4"]}}
            backends: backend_id → InteractionBackend 实例
        """
        self._routes = route_config
        self._backends = backends

    def resolve(self, role: str, stage: str | None = None) -> list[str]:
        """返回 backend 优先级列表（primary + fallback）。"""
        # 精确 role+stage 路由（预留）
        if stage:
            key = f"{role}.{stage}"
            if key in self._routes:
                route = self._routes[key]
                if isinstance(route, dict):
                    # 支持 quorum 多后端配置
                    if "strategy" in route and route["strategy"] == "quorum":
                        return route.get("backends", [])
                    return [route["primary"]] + route.get("fallback", [])
                return [route]

        # role 级路由
        if role in self._routes:
            route = self._routes[role]
            if isinstance(route, dict):
                if "strategy" in route and route["strategy"] == "quorum":
                    return route.get("backends", [])
                return [route["primary"]] + route.get("fallback", [])
            return [route]

        # 全局默认
        default = self._routes.get("default", {}).get("primary")
        if default:
            return [default]

        raise ValueError(f"No route found for role={role}, stage={stage}")

    def invoke(self, request: InteractionRequest) -> InteractionResponse:
        """执行请求，自动 fallback，并校验能力。"""
        candidate_ids = self.resolve(request.role, request.stage)

        last_error: Exception | None = None
        capability_skips: list[str] = []

        for backend_id in candidate_ids:
            backend = self._backends.get(backend_id)
            if backend is None:
                logger.warning("Backend '%s' not found in registry, skipping", backend_id)
                continue

            # 能力校验
            caps = backend.capabilities()
            mismatch = self._check_capabilities(request, caps)
            if mismatch:
                logger.info(
                    "Backend '%s' skipped: capability mismatch → %s",
                    backend_id, mismatch,
                )
                capability_skips.append(f"{backend_id}: {mismatch}")
                continue

            try:
                response = self._invoke_with_retry(backend, request)
                # 检查响应状态 — 如果调用返回了但标记为失败，也走 fallback
                if response.status == "ok":
                    return response
                logger.warning("Backend '%s' returned non-ok status: %s", backend_id, response.status)
                last_error = RuntimeError(f"Backend returned status={response.status}: {getattr(response, 'content', '')[:120]}")
                continue
            except Exception as exc:
                logger.warning("Backend '%s' failed: %s", backend_id, exc)
                last_error = exc
                continue

        # 构建详细错误信息
        error_detail = f"All backends failed for role={request.role}: {candidate_ids}"
        if capability_skips:
            error_detail += f"\nCapability skips: {', '.join(capability_skips)}"
        if last_error:
            error_detail += f"\nLast error: {last_error}"

        raise RuntimeError(error_detail) from last_error

    def healthcheck_all(self) -> dict[str, HealthStatus]:
        """对所有后端执行健康检查。"""
        return {bid: be.healthcheck() for bid, be in self._backends.items()}

    # ── 内部 ──────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def _invoke_with_retry(self, backend: InteractionBackend, request: InteractionRequest) -> InteractionResponse:
        return backend.invoke(request)

    @staticmethod
    def _check_capabilities(request: InteractionRequest, caps: dict) -> str | None:
        """检查后端能力是否满足请求需求。

        Returns:
            None 表示满足，字符串表示不满足的原因。
        """
        # 结构化输出需求
        response_format = request.response_format
        if response_format and response_format.get("type") == "json_schema":
            has_json_schema = caps.get("json_schema", False)
            has_tool_calling = caps.get("tool_calling", False)
            structured_level = caps.get("structured_output_level", 1)

            # L3: 原生支持 → 直接通过
            if has_json_schema and structured_level >= 3:
                pass
            # L2: tool_calling 模拟 → 可通过
            elif has_tool_calling and structured_level >= 2:
                pass
            # L1: prompt 约束 → 最低保证
            elif structured_level >= 1:
                pass
            else:
                return "requires structured output but backend has no json_schema/tool_calling capability"

        # Tool calling 需求
        if request.tools and len(request.tools) > 0:
            if not caps.get("tool_calling", False):
                return "request has tools but backend lacks tool_calling capability"

        # Workspace 需求
        if request.workspace:
            if not caps.get("workspace_execution", False):
                return "request has workspace but backend lacks workspace_execution capability"

        # Vision 需求（attachments）
        has_image = any(
            a.get("type", "").startswith("image/") or a.get("mime_type", "").startswith("image/")
            for a in (request.attachments or [])
        )
        if has_image and not caps.get("vision", False):
            return "request has image attachments but backend lacks vision capability"

        return None
