"""模型路由。

按 role/stage 选择 backend，支持 fallback 链和重试。
"""
from __future__ import annotations

import logging
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .types import InteractionRequest, InteractionResponse, HealthStatus
from .backends import InteractionBackend

logger = logging.getLogger(__name__)


class Router:
    """根据 role/stage 选择后端并执行调用。"""

    def __init__(self, route_config: dict[str, Any], backends: dict[str, InteractionBackend]):
        """
        Args:
            route_config: 路由配置，格式 {"optimizer": {"primary": "deepseek-v4-pro", "fallback": ["azure-gpt4"]}}
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
                    return [route["primary"]] + route.get("fallback", [])
                return [route]

        # role 级路由
        if role in self._routes:
            route = self._routes[role]
            if isinstance(route, dict):
                return [route["primary"]] + route.get("fallback", [])
            return [route]

        # 全局默认
        default = self._routes.get("default", {}).get("primary")
        if default:
            return [default]

        raise ValueError(f"No route found for role={role}, stage={stage}")

    def invoke(self, request: InteractionRequest) -> InteractionResponse:
        """执行请求，自动 fallback。"""
        candidate_ids = self.resolve(request.role, request.stage)

        last_error: Exception | None = None
        for backend_id in candidate_ids:
            backend = self._backends.get(backend_id)
            if backend is None:
                logger.warning("Backend %s not found in registry, skipping", backend_id)
                continue

            try:
                return self._invoke_with_retry(backend, request)
            except Exception as exc:
                logger.warning("Backend %s failed: %s", backend_id, exc)
                last_error = exc
                continue

        # 所有 fallback 都失败
        raise RuntimeError(f"All backends failed for role={request.role}: {candidate_ids}") from last_error

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def _invoke_with_retry(self, backend: InteractionBackend, request: InteractionRequest) -> InteractionResponse:
        return backend.invoke(request)

    def healthcheck_all(self) -> dict[str, HealthStatus]:
        """对所有后端执行健康检查。"""
        return {bid: be.healthcheck() for bid, be in self._backends.items()}
