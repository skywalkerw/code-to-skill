"""代码图谱相关配置解析（project YAML → pipeline 参数）。"""
from __future__ import annotations

from code_to_skill.code_graph.framework import merge_custom_patterns

from .config_loader import ProjectConfig, RepoSource


def resolve_framework_patterns(
    project: ProjectConfig,
    repo: RepoSource | None = None,
) -> dict[str, dict[str, str]]:
    """合并 project.code_graph.custom_patterns 与 repo.framework_patterns。"""
    return merge_custom_patterns(
        project.code_graph.custom_patterns or None,
        repo.framework_patterns if repo else None,
    )
