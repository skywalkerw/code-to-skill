"""Module 5 配置加载与 Provider 工厂。

支持两种配置来源：
1. 独立 interaction_config.yaml（向后兼容 + 测试用）
2. 内嵌在 config.yaml 的 model_provider 段（推荐，各模块统一配置）

名词约定（对齐设计文档 §5-§6）：
- backend_type：高层类别（llm_api, agent_cli, mock 等）
- provider：具体实现类名（openai_compatible, mock 等），通过注册表映射到类
- backend_id：实例标识（deepseek, codex-cli-target 等）
"""
from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from .backends import InteractionBackend
from .router import Router

if TYPE_CHECKING:
    from code_to_skill.cli.config_loader import AppConfig

logger = logging.getLogger(__name__)

# ── Provider 注册表 ──────────────────────────────────────────────


def _get_builtin_providers() -> dict[str, type[InteractionBackend]]:
    """返回内置 provider 名称到 Backend 类的映射（延迟导入避免循环依赖）。"""
    from .backends.openai_compatible import OpenAICompatibleBackend
    from .backends.mock import MockReplayBackend
    return {
        "openai_compatible": OpenAICompatibleBackend,
        "mock": MockReplayBackend,
    }


# ── 环境变量解析 ─────────────────────────────────────────────────


def _resolve_env_vars(config: dict) -> dict:
    """解析 config 中的环境变量引用。

    支持两种形式：
    - ${VAR_NAME} 内联替换（os.path.expandvars）
    - api_key_env 字段：读取对应环境变量的值作为 api_key
    """
    resolved: dict[str, Any] = {}
    for k, v in config.items():
        if isinstance(v, str):
            resolved[k] = os.path.expandvars(v)
        else:
            resolved[k] = v

    # api_key_env → 实际 api_key
    api_key_env = resolved.pop("api_key_env", None)
    if api_key_env:
        resolved["api_key"] = os.environ.get(api_key_env, "")

    # base_url_env → 若未显式设置 base_url，从环境变量读取
    base_url_env = resolved.pop("base_url_env", None)
    if base_url_env and not resolved.get("base_url"):
        resolved["base_url"] = os.environ.get(base_url_env, "")

    return resolved


# ── Backend 工厂 ─────────────────────────────────────────────────


def create_backend_from_config(backend_id: str, backend_config: dict) -> InteractionBackend:
    """根据配置字典创建单个 Backend 实例。

    Args:
        backend_id: 唯一标识（如 "deepseek"）
        backend_config: backends.<id> 的配置块（支持 pydantic model dump 或原生 dict）

    Returns:
        已实例化的 InteractionBackend
    """
    # 兼容 pydantic model（有 model_dump）和原生 dict
    if hasattr(backend_config, 'model_dump'):
        backend_config = backend_config.model_dump()

    provider_name = backend_config.get("provider", "openai_compatible")
    providers = _get_builtin_providers()
    provider_cls = providers.get(provider_name)
    if provider_cls is None:
        raise ValueError(
            f"Unknown provider '{provider_name}' for backend '{backend_id}'. "
            f"Available: {list(providers.keys())}"
        )

    resolved = _resolve_env_vars(backend_config)

    if provider_name == "openai_compatible":
        return provider_cls(
            backend_id=backend_id,
            base_url=resolved.get("base_url", ""),
            api_key=resolved.get("api_key", ""),
            model=resolved.get("model", "gpt-4o"),
            context_window=resolved.get("context_window", 128000),
            timeout_seconds=resolved.get("timeout_seconds", 180),
        )

    elif provider_name == "mock":
        import tempfile
        fixture_dir = resolved.get("fixture_dir") or os.path.join(
            tempfile.gettempdir(), f"code-to-skill-mock-{backend_id}"
        )
        os.makedirs(fixture_dir, exist_ok=True)
        return provider_cls(
            backend_id=backend_id,
            fixture_dir=fixture_dir,
            model=resolved.get("model", "mock-model"),
        )

    else:
        raise ValueError(
            f"Provider '{provider_name}' is registered but has no factory branch"
        )


# ── Dict-based 构建（从 config.yaml 内嵌配置）─────────────────────


def build_router_from_dict(config_dict: dict[str, Any]) -> tuple[Router, dict[str, InteractionBackend]]:
    """从配置字典构建 Router 和所有 Backend 实例。

    这是推荐的主入口：caller 从 config.yaml 解析 model_provider 段后传入。

    Args:
        config_dict: 完整的 model_provider 配置字典，结构如下：
            {
                "backends": {backend_id: {...}, ...},
                "routes": {"optimizer": {"primary": "...", ...}, ...},
                "policies": {...},
            }

    Returns:
        (Router, backends_dict)
    """
    # 1. 构建 Backend 实例
    backends: dict[str, InteractionBackend] = {}
    backend_configs = config_dict.get("backends", {})
    for backend_id, backend_cfg in backend_configs.items():
        backends[backend_id] = create_backend_from_config(backend_id, backend_cfg)
        logger.info("Backend created: %s (type=%s, provider=%s)",
                     backend_id,
                     backend_cfg.get("type", "unknown") if isinstance(backend_cfg, dict) else getattr(backend_cfg, 'type', 'unknown'),
                     backend_cfg.get("provider", "default") if isinstance(backend_cfg, dict) else getattr(backend_cfg, 'provider', 'default'))

    # 2. 构建 Router
    routes = config_dict.get("routes", {})
    # 如果 routes 的值是 RouteConfig pydantic model，转为 dict
    routes_for_router: dict[str, Any] = {}
    for key, val in routes.items():
        if hasattr(val, 'model_dump'):
            routes_for_router[key] = val.model_dump()
        else:
            routes_for_router[key] = val

    router = Router(route_config=routes_for_router, backends=backends)
    return router, backends


def build_router_from_app_config(cfg: "AppConfig") -> tuple[Router, dict[str, InteractionBackend]]:
    """从 AppConfig.settings.model_provider 构建 Router。

    Args:
        cfg: 已加载的 AppConfig 实例

    Returns:
        (Router, backends_dict)
    """
    mp = cfg.settings.model_provider
    return build_router_from_dict(mp.model_dump())


# ── 独立 YAML 文件加载（向后兼容）──────────────────────────────────


def load_interaction_config(config_path: str | Path) -> dict[str, Any]:
    """加载独立的 interaction_config.yaml，返回解析后的配置字典。"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Interaction config not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"Empty config: {path}")

    return raw


def build_router_from_config(config_path: str | Path) -> tuple[Router, dict[str, InteractionBackend]]:
    """从独立 interaction_config.yaml 构建 Router 和所有 Backend 实例。

    推荐改用 build_router_from_dict() 配合 config.yaml 内嵌配置。
    """
    raw = load_interaction_config(config_path)
    return build_router_from_dict(raw)


def create_llm_backend_from_yaml(
    config_path: str | Path,
    backend_id: str | None = None,
) -> InteractionBackend:
    """从独立 YAML 配置创建单个 LLM Backend（便捷函数）。"""
    raw = load_interaction_config(config_path)
    backend_configs = raw.get("backends", {})

    if backend_id and backend_id in backend_configs:
        return create_backend_from_config(backend_id, backend_configs[backend_id])

    routes = raw.get("routes", {})
    default_route = routes.get("default", {}).get("primary")
    if default_route and default_route in backend_configs:
        return create_backend_from_config(default_route, backend_configs[default_route])

    if backend_configs:
        first_id = next(iter(backend_configs))
        return create_backend_from_config(first_id, backend_configs[first_id])

    raise ValueError(f"No backends found in {config_path}")


# ── 配置校验 ─────────────────────────────────────────────────────


def validate_interaction_config(config_path: str | Path) -> list[str]:
    """校验独立 interaction_config.yaml 的完整性和合法性。

    Returns:
        警告信息列表（空列表表示通过）
    """
    warnings: list[str] = []

    try:
        raw = load_interaction_config(config_path)
    except FileNotFoundError:
        return [f"Config file not found: {config_path}"]
    except Exception as e:
        return [f"Config parse error: {e}"]

    backend_configs = raw.get("backends", {})
    if not backend_configs:
        warnings.append("No backends defined in config")

    for backend_id, cfg in backend_configs.items():
        provider_name = cfg.get("provider", "openai_compatible")
        providers = _get_builtin_providers()
        if provider_name not in providers:
            warnings.append(
                f"Backend '{backend_id}': unknown provider '{provider_name}'. "
                f"Available: {list(providers.keys())}"
            )

        backend_type = cfg.get("type", "")
        valid_types = {"llm_api", "local_llm", "agent_cli", "agent_service", "mock"}
        if backend_type not in valid_types:
            warnings.append(
                f"Backend '{backend_id}': unknown type '{backend_type}'. "
                f"Valid: {valid_types}"
            )

        if provider_name == "openai_compatible":
            has_key = cfg.get("api_key_env") or cfg.get("api_key")
            if not has_key:
                warnings.append(
                    f"Backend '{backend_id}': no api_key_env or api_key configured"
                )

    routes = raw.get("routes", {})
    known_backend_ids = set(backend_configs.keys())
    for role, route_cfg in routes.items():
        if isinstance(route_cfg, dict):
            primary = route_cfg.get("primary", "")
            if primary and primary not in known_backend_ids:
                warnings.append(
                    f"Route '{role}': primary backend '{primary}' not found in backends"
                )
            for fb in route_cfg.get("fallback", []):
                if fb not in known_backend_ids:
                    warnings.append(
                        f"Route '{role}': fallback backend '{fb}' not found in backends"
                    )

    policies = raw.get("policies", {})
    max_cost = policies.get("max_cost_per_run_usd")
    if max_cost is not None and not isinstance(max_cost, (int, float)):
        warnings.append(f"policies.max_cost_per_run_usd must be numeric, got {type(max_cost)}")

    return warnings
