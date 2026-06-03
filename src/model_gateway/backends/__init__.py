"""模型/Agent 后端抽象接口。

所有 backend 必须实现 InteractionBackend。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import InteractionRequest, InteractionResponse, HealthStatus


class InteractionBackend(ABC):
    """可插拔的模型/Agent 后端统一接口。"""

    backend_id: str
    backend_type: str

    @abstractmethod
    def capabilities(self) -> dict:
        """返回后端能力声明（chat, json_schema, tool_calling, vision, etc.）"""
        ...

    @abstractmethod
    def invoke(self, request: InteractionRequest) -> InteractionResponse:
        """处理请求，返回统一响应。"""
        ...

    @abstractmethod
    def healthcheck(self) -> HealthStatus:
        """检查后端是否可用。"""
        ...
