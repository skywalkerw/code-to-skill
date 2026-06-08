"""LLM Backend 工厂。

支持三种创建方式（按优先级降序）：
1. config.yaml 内嵌 model_provider 配置 → create_llm_backend_from_project()
2. 独立 interaction_config.yaml → create_llm_backend_from_yaml()
3. 环境变量 → create_llm_backend() / is_llm_available()

自动加载项目根目录的 .env 文件（系统环境变量优先）。
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

from code_to_skill.model_provider.backends import InteractionBackend
from code_to_skill.model_provider.backends.openai_compatible import OpenAICompatibleBackend
from code_to_skill.model_provider.backends.mock import MockReplayBackend

logger = logging.getLogger(__name__)


def _load_dotenv():
    """加载项目根目录 .env 文件。系统环境变量优先。"""
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

# ── 环境变量映射（legacy）─────────────────────────────

_ENV_MAP = {
    "deepseek": {
        "base_url_env": "DEEPSEEK_BASE_URL",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
        "default_base_url": "https://api.deepseek.com",
        "context_window": 1_000_000,
        "max_output_tokens": 384_000,
    },
    "openai": {
        "base_url_env": "OPENAI_BASE_URL",
        "api_key_env": "OPENAI_API_KEY",
        "model_env": "OPENAI_MODEL",
        "default_model": "gpt-4o",
        "default_base_url": "https://api.openai.com/v1",
    },
}


def _simulate_fixture_dir() -> str:
    from pathlib import Path
    return str(
        Path(__file__).resolve().parent.parent
        / "cli" / "fixtures" / "full_simulate" / "mock-backend"
    )


def create_llm_backend(backend_id: str | None = None) -> InteractionBackend:
    """自动发现并创建 LLM backend。

    查找顺序：config.yaml → interaction_config.yaml → 环境变量 → mock fallback

    Args:
        backend_id: 预配置的 backend ID。为 None 时从 SKILL_LAB_LLM_BACKEND 环境变量读取，
                   未设置则默认 "deepseek"

    Returns:
        InteractionBackend 实例（真实 LLM 或 Mock）
    """
    if os.environ.get("SKILL_LAB_SIMULATE"):
        logger.info("Using simulate MockReplayBackend (SKILL_LAB_SIMULATE=1)")
        return MockReplayBackend(
            backend_id="mock-backend",
            fixture_dir=_simulate_fixture_dir(),
            model="mock-simulate",
        )

    if backend_id is None:
        backend_id = os.environ.get("SKILL_LAB_LLM_BACKEND", "deepseek")

    # 1. 尝试项目 config（推荐）
    config_candidates: list[Path] = []
    env_config = os.environ.get("SKILL_LAB_CONFIG_PATH", "").strip()
    if env_config:
        config_candidates.append(Path(env_config))
    config_candidates.extend([Path("config.yaml"), Path("skill-lab.yaml")])
    seen: set[str] = set()
    for project_yaml in config_candidates:
        key = str(project_yaml.resolve()) if project_yaml.exists() else str(project_yaml)
        if key in seen:
            continue
        seen.add(key)
        if project_yaml.exists():
            try:
                from code_to_skill.cli.config_loader import load_config
                from code_to_skill.model_provider.config import create_backend_from_config
                cfg = load_config(str(project_yaml))
                mp = cfg.settings.model_provider
                backend_cfg = mp.backends.get(backend_id)
                if backend_cfg:
                    logger.info("Using config.yaml backend: %s", backend_id)
                    return create_backend_from_config(backend_id, backend_cfg)
            except Exception as e:
                logger.debug("config.yaml load failed: %s", e)

    # 2. 尝试独立 interaction_config.yaml
    yaml_paths = [
        Path("interaction_config.yaml"),
        Path("config") / "interaction_config.yaml",
    ]
    for yaml_path in yaml_paths:
        if yaml_path.exists():
            try:
                from code_to_skill.model_provider.config import create_llm_backend_from_yaml
                logger.info("Using standalone YAML config: %s", yaml_path)
                return create_llm_backend_from_yaml(yaml_path, backend_id)
            except Exception as e:
                logger.warning("YAML config failed (%s): %s", yaml_path, e)

    # 3. 降级：环境变量模式
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
        context_window=cfg.get("context_window", 128000),
        max_output_tokens=cfg.get("max_output_tokens", 16384),
    )


def _create_mock(backend_id: str) -> MockReplayBackend:
    """创建降级 MockBackend。"""
    import tempfile
    fixture_dir = os.path.join(tempfile.gettempdir(), f"code-to-skill-mock-{backend_id}")
    os.makedirs(fixture_dir, exist_ok=True)
    return MockReplayBackend(backend_id=backend_id, fixture_dir=fixture_dir)


def is_llm_available(backend_id: str | None = None) -> bool:
    """检查 LLM backend 是否可用。"""
    if os.environ.get("SKILL_LAB_SIMULATE"):
        return True

    if backend_id is None:
        backend_id = os.environ.get("SKILL_LAB_LLM_BACKEND", "deepseek")

    # 1. 检查项目 config
    config_candidates: list[Path] = []
    env_config = os.environ.get("SKILL_LAB_CONFIG_PATH", "").strip()
    if env_config:
        config_candidates.append(Path(env_config))
    config_candidates.extend([Path("config.yaml"), Path("skill-lab.yaml")])
    seen: set[str] = set()
    for project_yaml in config_candidates:
        key = str(project_yaml.resolve()) if project_yaml.exists() else str(project_yaml)
        if key in seen:
            continue
        seen.add(key)
        if project_yaml.exists():
            try:
                from code_to_skill.cli.config_loader import load_config
                cfg = load_config(str(project_yaml))
                if cfg.settings.model_provider.backends.get(backend_id):
                    return True
            except Exception:
                pass

    # 2. 检查独立 YAML
    for yaml_path in [Path("interaction_config.yaml"), Path("config") / "interaction_config.yaml"]:
        if yaml_path.exists():
            try:
                from code_to_skill.model_provider.config import load_interaction_config
                raw = load_interaction_config(yaml_path)
                if raw.get("backends", {}).get(backend_id):
                    return True
            except Exception:
                pass

    # 3. 环境变量
    cfg = _ENV_MAP.get(backend_id)
    if cfg is None:
        return False
    return bool(os.environ.get(cfg["api_key_env"], ""))
