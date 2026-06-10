"""LLM Backend 工厂。

从 config.yaml 的 ``settings.model_provider`` 创建 Backend；无配置时降级 Mock。
自动加载项目根目录的 .env 文件（系统环境变量优先）。
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

from code_to_skill.model_provider.backends import InteractionBackend
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


def _simulate_fixture_dir() -> str:
    return str(
        Path(__file__).resolve().parent.parent
        / "cli" / "fixtures" / "full_simulate" / "mock-backend"
    )


def _config_paths() -> list[Path]:
    paths: list[Path] = []
    env_config = os.environ.get("SKILL_LAB_CONFIG_PATH", "").strip()
    if env_config:
        paths.append(Path(env_config))
    paths.append(Path("config.yaml"))
    seen: set[str] = set()
    unique: list[Path] = []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def _backend_from_config(backend_id: str) -> InteractionBackend | None:
    from code_to_skill.cli.config_loader import load_config
    from code_to_skill.model_provider.config import create_backend_from_config

    for project_yaml in _config_paths():
        if not project_yaml.exists():
            continue
        try:
            cfg = load_config(str(project_yaml))
            backend_cfg = cfg.settings.model_provider.backends.get(backend_id)
            if backend_cfg:
                logger.info("Using config.yaml backend: %s", backend_id)
                return create_backend_from_config(backend_id, backend_cfg)
        except Exception as e:
            logger.debug("config load failed (%s): %s", project_yaml, e)
    return None


def _create_mock(backend_id: str) -> MockReplayBackend:
    """创建降级 MockBackend。"""
    import tempfile
    fixture_dir = os.path.join(tempfile.gettempdir(), f"code-to-skill-mock-{backend_id}")
    os.makedirs(fixture_dir, exist_ok=True)
    return MockReplayBackend(backend_id=backend_id, fixture_dir=fixture_dir)


def create_llm_backend(backend_id: str | None = None) -> InteractionBackend:
    """从 config.yaml 创建 LLM backend，失败则降级 Mock。

    Args:
        backend_id: ``model_provider.backends`` 中的 ID。
                    为 None 时从 ``SKILL_LAB_LLM_BACKEND`` 读取，默认 ``deepseek``。
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

    backend = _backend_from_config(backend_id)
    if backend is not None:
        return backend

    logger.warning("Backend '%s' not found in config.yaml, using mock", backend_id)
    return _create_mock(backend_id)


def is_llm_available(backend_id: str | None = None) -> bool:
    """检查 config.yaml 中是否配置了指定 backend。"""
    if os.environ.get("SKILL_LAB_SIMULATE"):
        return True

    if backend_id is None:
        backend_id = os.environ.get("SKILL_LAB_LLM_BACKEND", "deepseek")

    for project_yaml in _config_paths():
        if not project_yaml.exists():
            continue
        try:
            from code_to_skill.cli.config_loader import load_config
            cfg = load_config(str(project_yaml))
            if cfg.settings.model_provider.backends.get(backend_id):
                return True
        except Exception:
            pass
    return False
