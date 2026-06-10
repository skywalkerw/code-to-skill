"""统一 YAML 配置加载与校验。

config.yaml 分为两个顶层段：
  settings    框架自身配置（控制 code-to-skill 如何工作）
  project     目标项目配置（要处理哪个项目的代码/文档）
"""
from __future__ import annotations

import os
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ContextRefPathRule(BaseModel):
    """benchmark context_ref 简写路径 → 仓库内候选路径（项目专用）。"""
    prefix: str
    expansions: list[str] = Field(default_factory=list)
    skip_if_contains: str = ""


class ProjectCodeGraphConfig(BaseModel):
    """目标项目代码图谱扩展（自定义框架模式等）。"""
    custom_patterns: dict[str, dict[str, str]] = Field(default_factory=dict)
    context_ref_path_rules: list[ContextRefPathRule] = Field(default_factory=list)


class RepoSource(BaseModel):
    id: str
    path: str
    ref: str = "HEAD"
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    framework_patterns: dict[str, dict[str, str]] = Field(default_factory=dict)


class DocSource(BaseModel):
    id: str
    path: str
    provider: str = "local_file"
    type: str  # markdown / pdf / wiki_export / html / docx / text
    version: str = "latest"
    authority: str = "team_runbook"
    domain_tags: list[str] = Field(default_factory=list)
    ocr_enabled: bool = False


class ProjectConfig(BaseModel):
    """目标项目定义 — 要处理哪个项目的代码/文档。"""
    name: str = ""
    domain: str = ""
    description: str = ""
    initial_skill_path: str = ""
    benchmark_path: str = ""
    code_graph: ProjectCodeGraphConfig = Field(default_factory=ProjectCodeGraphConfig)
    repos: list[RepoSource] = Field(default_factory=list)
    docs: list[DocSource] = Field(default_factory=list)
    graph_role_hints: dict[str, Any] = Field(default_factory=dict)
    reflect_prompts: dict[str, str] = Field(default_factory=dict)


class BackendConfig(BaseModel):
    type: str = ""
    provider: str = "openai_compatible"
    model: str = ""
    base_url: str = ""
    base_url_env: str = ""
    api_key_env: str = ""
    api_key: str = ""
    context_window: int = 128000
    max_output_tokens: int = 16384
    timeout_seconds: int = 180
    command: str = ""
    profile: str = ""
    sandbox: str = ""
    workspace_required: bool = False
    returns_trajectory: bool = False
    fixture_dir: str = ""


class RouteConfig(BaseModel):
    primary: str = ""
    fallback: list[str] = Field(default_factory=list)
    strategy: str = "fallback"
    quorum: int = 2
    backends: list[str] = Field(default_factory=list)


class ModelProviderSettings(BaseModel):
    backends: dict[str, BackendConfig] = Field(default_factory=dict)
    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    default_retries: int = 3
    retry_backoff: str = "exponential"
    trace_enabled: bool = True
    cache_enabled: bool = False
    redact_secrets: bool = True
    max_cost_per_run_usd: float = 20.0
    max_timeout_seconds: int = 900
    structured_output_fallback: bool = True


class SettingsConfig(BaseModel):
    code_graph: dict[str, Any] = Field(default_factory=dict)
    document_normalizer: dict[str, Any] = Field(default_factory=dict)
    atom_extractor: dict[str, Any] = Field(default_factory=dict)
    skillopt: dict[str, Any] = Field(default_factory=dict)
    self_evolution: dict[str, Any] = Field(default_factory=dict)
    pipeline: dict[str, Any] = Field(default_factory=dict)
    model_provider: ModelProviderSettings = Field(default_factory=ModelProviderSettings)
    output_root: str = "runs"
    publish_target: str = ""
    approvals_require_for: list[str] = Field(default_factory=list)
    approvals_auto_approve_in_batch: bool = False


class AppConfig(BaseModel):
    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    project: ProjectConfig = Field(default_factory=ProjectConfig)

    @classmethod
    def from_yaml(cls, path: str) -> "AppConfig":
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid YAML: expected dict, got {type(raw)}")
        return cls(
            settings=_parse_settings(raw.get("settings", {})),
            project=_parse_project(raw.get("project", {})),
        )


def load_config(path: str) -> AppConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    return AppConfig.from_yaml(path)


def _parse_project(raw: dict) -> ProjectConfig:
    if not raw:
        return ProjectConfig()

    from code_to_skill.code_graph.framework import parse_custom_patterns

    sources = raw.get("sources", {})
    code_graph_raw = raw.get("code_graph", {}) or {}
    repos_raw = sources.get("repos") or []
    repos: list[RepoSource] = []
    for item in repos_raw:
        repo_data = dict(item)
        repo_data["framework_patterns"] = parse_custom_patterns(
            repo_data.pop("framework_patterns", None)
        )
        repos.append(RepoSource(**repo_data))

    return ProjectConfig(
        name=raw.get("name", ""),
        domain=raw.get("domain", ""),
        description=raw.get("description", ""),
        initial_skill_path=raw.get("initial_skill", ""),
        benchmark_path=raw.get("benchmark", ""),
        code_graph=ProjectCodeGraphConfig(
            custom_patterns=parse_custom_patterns(
                code_graph_raw.get("custom_patterns"),
            ),
            context_ref_path_rules=_parse_context_ref_path_rules(
                code_graph_raw.get("context_ref_path_rules"),
            ),
        ),
        repos=repos,
        docs=[DocSource(**d) for d in (sources.get("docs") or [])],
        graph_role_hints=raw.get("graph_role_hints", {}) or {},
        reflect_prompts=raw.get("reflect_prompts", {}) or {},
    )


def _parse_context_ref_path_rules(raw: object) -> list[ContextRefPathRule]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("project.code_graph.context_ref_path_rules must be a list")
    rules: list[ContextRefPathRule] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each context_ref_path_rule must be a mapping")
        rules.append(ContextRefPathRule(**item))
    return rules


def _parse_settings(raw: dict) -> SettingsConfig:
    if not raw:
        return SettingsConfig()

    mp_raw = raw.get("model_provider", {})
    output = raw.get("output", {})
    approvals = raw.get("approvals", {})

    return SettingsConfig(
        code_graph=raw.get("code_graph", {}),
        document_normalizer=raw.get("document_normalizer", {}),
        atom_extractor=raw.get("atom_extractor", {}),
        skillopt=raw.get("skillopt", {}),
        self_evolution=raw.get("self_evolution", {}),
        pipeline=raw.get("pipeline", {}),
        model_provider=_parse_model_provider(mp_raw),
        output_root=output.get("root", "runs"),
        publish_target=output.get("publish_target", ""),
        approvals_require_for=approvals.get("require_for", []),
        approvals_auto_approve_in_batch=approvals.get("auto_approve_in_batch", False),
    )


def _parse_model_provider(raw: dict) -> ModelProviderSettings:
    if not raw:
        return ModelProviderSettings()

    backends = {bid: BackendConfig(**bcfg) for bid, bcfg in raw.get("backends", {}).items()}
    routes = {role: RouteConfig(**rcfg) for role, rcfg in raw.get("routes", {}).items()}
    policies = raw.get("policies", {})
    return ModelProviderSettings(
        backends=backends,
        routes=routes,
        default_retries=policies.get("default_retries", 3),
        retry_backoff=policies.get("retry_backoff", "exponential"),
        trace_enabled=policies.get("trace_enabled", True),
        cache_enabled=policies.get("cache_enabled", False),
        redact_secrets=policies.get("redact_secrets", True),
        max_cost_per_run_usd=policies.get("max_cost_per_run_usd", 20),
        max_timeout_seconds=policies.get("max_timeout_seconds", 900),
        structured_output_fallback=policies.get("structured_output_fallback", True),
    )
