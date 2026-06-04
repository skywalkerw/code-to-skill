"""LLM Backend 工厂。

从环境变量自动创建 OpenAI-compatible backend 或降级到 MockBackend。
自动加载项目根目录的 .env 文件（系统环境变量优先）。
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

from code_to_skill.model_gateway.backends import InteractionBackend
from code_to_skill.model_gateway.backends.openai_compatible import OpenAICompatibleBackend
from code_to_skill.model_gateway.backends.mock import MockReplayBackend

logger = logging.getLogger(__name__)


def _load_dotenv():
    """加载项目根目录 .env 文件。系统环境变量优先。"""
    # 从当前模块向上查找项目根目录
    env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    if not env_path.exists():
        return

    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


# 模块加载时自动读取 .env
_load_dotenv()

# 环境变量映射
_ENV_MAP = {
    "deepseek": {
        "base_url_env": "DEEPSEEK_BASE_URL",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
        "default_base_url": "https://api.deepseek.com",
    },
    "openai": {
        "base_url_env": "OPENAI_BASE_URL",
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o",
        "default_base_url": "https://api.openai.com/v1",
    },
}


def create_llm_backend(backend_id: str | None = None) -> InteractionBackend:
    """从环境变量创建 LLM backend，不可用时降级为 MockBackend。

    Args:
        backend_id: 预配置的 backend ID。为 None 时从 SKILL_LAB_LLM_BACKEND 环境变量读取，
                   未设置则默认 "deepseek"

    Returns:
        InteractionBackend 实例（真实 LLM 或 Mock）
    """
    if backend_id is None:
        backend_id = os.environ.get("SKILL_LAB_LLM_BACKEND", "deepseek")
    cfg = _ENV_MAP.get(backend_id)
    if cfg is None:
        logger.warning("Unknown backend_id '%s', falling back to mock", backend_id)
        return _create_mock(backend_id)

    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key:
        logger.info("No API key found for %s (%s), using mock backend",
                     backend_id, cfg["api_key_env"])
        return _create_mock(backend_id)

    base_url = os.environ.get(cfg["base_url_env"], cfg["default_base_url"])
    model = os.environ.get(cfg.get("model_env", ""), cfg["default_model"])

    logger.info("Creating %s backend (base_url=%s, model=%s)", backend_id, base_url, model)
    return OpenAICompatibleBackend(
        backend_id=backend_id,
        base_url=base_url,
        api_key=api_key,
        model=model,
    )


def _create_mock(backend_id: str) -> MockReplayBackend:
    """创建降级 MockBackend。"""
    import tempfile
    fixture_dir = os.path.join(tempfile.gettempdir(), f"code-to-skill-mock-{backend_id}")
    os.makedirs(fixture_dir, exist_ok=True)
    return MockReplayBackend(backend_id=backend_id, fixture_dir=fixture_dir)


def is_llm_available(backend_id: str | None = None) -> bool:
    """检查 LLM backend 是否可用（API key 已设置）。"""
    if backend_id is None:
        backend_id = os.environ.get("SKILL_LAB_LLM_BACKEND", "deepseek")
    cfg = _ENV_MAP.get(backend_id)
    if cfg is None:
        return False
    return bool(os.environ.get(cfg["api_key_env"], ""))
