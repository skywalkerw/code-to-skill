"""模块 5：模型与智能体交互管理（Model Provider）。

提供统一、可插拔的模型与智能体交互层。
业务模块通过 InteractionBackend 抽象接口调用，
具体后端（OpenAI-compatible、Mock 等）通过 provider 注册表按配置实例化。

命名约定：
- backend_type：高层类别（llm_api, agent_cli, mock）
- provider：具体实现（openai_compatible, mock），对应 Backend 子类
- backend_id：实例标识（deepseek, qwen-local-target 等）

使用方式（通过 config.yaml 统一配置）：
    from code_to_skill.cli.config_loader import load_config
    from code_to_skill.model_provider import build_router_from_app_config

    cfg = load_config("config.yaml")
    router, backends = build_router_from_app_config(cfg)
    response = router.invoke(request)
"""
from .types import (
    InteractionRequest,
    InteractionResponse,
    ModelResponse,
    AgentResponse,
    HealthStatus,
)
from .backends import InteractionBackend
from .router import Router
from .structured_output import invoke_with_structured_output
from .config import (
    build_router_from_dict,
    build_router_from_app_config,
    create_backend_from_config,
)
from .llm_backend import create_llm_backend, is_llm_available
from .tracer import configure_trace, record_interaction, is_trace_enabled

__all__ = [
    # 核心类型
    "InteractionRequest",
    "InteractionResponse",
    "ModelResponse",
    "AgentResponse",
    "HealthStatus",
    # 抽象接口
    "InteractionBackend",
    # 路由
    "Router",
    # 结构化输出
    "invoke_with_structured_output",
    # 配置加载
    "build_router_from_dict",
    "build_router_from_app_config",
    "create_backend_from_config",
    # 便捷工厂（读取 config.yaml）
    "create_llm_backend",
    "is_llm_available",
    # Trace
    "configure_trace",
    "record_interaction",
    "is_trace_enabled",
]
