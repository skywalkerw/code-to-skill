"""模型/Agent 后端抽象接口。

所有 backend 必须实现 InteractionBackend。
接口抽象层次对齐设计文档 §5.3 Provider 接口。

capabilities 字典约定（对齐 §5.2）：
- chat: bool          — 是否支持 chat completion
- messages: bool      — 是否支持多轮对话
- json_schema: bool   — 是否原生支持 JSON Schema structured output
- tool_calling: bool  — 是否支持 tool/function calling
- vision: bool        — 是否支持图片输入
- workspace_execution: bool  — 是否能读写工作区（agent 类后端）
- file_write: bool    — 是否支持写文件
- shell_command: bool — 是否支持执行命令
- returns_trajectory: bool — 是否返回执行轨迹
- structured_output_level: int  — 结构化输出能力等级（L0-L3，见 structured_output.py）
- context_window: int — 上下文窗口大小
- max_output_tokens: int — 最大输出 token 数
- timeout_seconds: int — 超时时间
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import InteractionRequest, InteractionResponse, HealthStatus


class InteractionBackend(ABC):
    """可插拔的模型/Agent 后端统一接口。"""

    backend_id: str
    backend_type: str
    provider: str = ""  # provider 名称（如 "openai_compatible", "mock"）

    @abstractmethod
    def capabilities(self) -> dict:
        """返回后端能力声明。

        返回值必须包含上述约定的所有 key，以便 Router 进行能力校验。
        """
        ...

    @abstractmethod
    def invoke(self, request: InteractionRequest) -> InteractionResponse:
        """处理请求，返回统一响应。"""
        ...

    @abstractmethod
    def healthcheck(self) -> HealthStatus:
        """检查后端是否可用。"""
        ...
