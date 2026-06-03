"""YAML 配置加载与校验。

符合 §2.4 project.yaml 完整 schema。
"""
from __future__ import annotations

import os
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError


# ── Schema ────────────────────────────────────────────────────

class RepoSource(BaseModel):
    id: str
    path: str
    ref: str = "HEAD"
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


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
    """project.yaml 的 pydantic schema。"""

    # 项目基础
    name: str = "code-to-skill"
    domain: str = ""
    description: str = ""

    # 数据源
    repos: list[RepoSource] = Field(default_factory=list)
    docs: list[DocSource] = Field(default_factory=list)

    # 模块配置（松散模式，允许各模块有自己的子配置）
    code_graph: dict[str, Any] = Field(default_factory=dict)
    document_normalizer: dict[str, Any] = Field(default_factory=dict)
    atom_extractor: dict[str, Any] = Field(default_factory=dict)
    skillopt: dict[str, Any] = Field(default_factory=dict)
    model_layer: dict[str, Any] = Field(default_factory=dict)

    # 输出
    output_root: str = "runs"
    publish_target: str = ""

    # 审批
    approvals_require_for: list[str] = Field(default_factory=list)
    approvals_auto_approve_in_batch: bool = False

    @classmethod
    def from_yaml(cls, path: str) -> "ProjectConfig":
        """从 YAML 文件加载配置。"""
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid YAML: expected dict, got {type(raw)}")

        # 扁平化嵌套结构
        project = raw.get("project", {})
        sources = raw.get("sources", {})
        config = {
            "name": project.get("name", "code-to-skill"),
            "domain": project.get("domain", ""),
            "description": project.get("description", ""),
            "repos": sources.get("repos", []),
            "docs": sources.get("docs", []),
            "code_graph": raw.get("code_graph", {}),
            "document_normalizer": raw.get("document_normalizer", {}),
            "atom_extractor": raw.get("atom_extractor", {}),
            "skillopt": raw.get("skillopt", {}),
            "model_layer": raw.get("model_layer", {}),
            "output_root": raw.get("output", {}).get("root", "runs"),
            "publish_target": raw.get("output", {}).get("publish_target", ""),
            "approvals_require_for": raw.get("approvals", {}).get("require_for", []),
            "approvals_auto_approve_in_batch": raw.get("approvals", {}).get("auto_approve_in_batch", False),
        }
        return cls(**config)

    def validate_sources_exist(self) -> list[str]:
        """校验数据源路径是否存在。返回警告列表。"""
        warnings: list[str] = []
        for repo in self.repos:
            if not os.path.exists(repo.path):
                warnings.append(f"Repo path not found: {repo.path}")
        for doc in self.docs:
            if doc.provider == "local_file" and not os.path.exists(doc.path):
                warnings.append(f"Doc path not found: {doc.path}")
            if doc.provider not in ("local_file", "feishu_api", "confluence_api", "notion_api"):
                warnings.append(f"Unknown provider '{doc.provider}' for doc {doc.id} (not yet implemented)")
        return warnings


def load_project_config(path: str) -> ProjectConfig:
    """加载并校验 project.yaml。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    return ProjectConfig.from_yaml(path)
