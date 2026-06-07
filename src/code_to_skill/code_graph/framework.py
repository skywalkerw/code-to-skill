"""框架元数据提取：通用 Spring/MyBatis + 可配置自定义模式。"""
from __future__ import annotations

import os
import re
from typing import Mapping

from .types import CodeGraph, GraphNode, GraphEdge, NodeKind, EdgeKind

# framework_name -> substring -> role（由 project.code_graph.custom_patterns 注入）
CustomFrameworkPatterns = dict[str, dict[str, str]]

_SPRING_ANNOTATIONS: dict[str, NodeKind] = {
    "@Service": NodeKind.class_,
    "@Repository": NodeKind.class_,
    "@Component": NodeKind.class_,
    "@RestController": NodeKind.route,
    "@Controller": NodeKind.route,
    "@Configuration": NodeKind.config,
    "@ConfigurationProperties": NodeKind.config,
    "@RequestMapping": NodeKind.route,
    "@GetMapping": NodeKind.route,
    "@PostMapping": NodeKind.route,
    "@PutMapping": NodeKind.route,
    "@DeleteMapping": NodeKind.route,
    "@PatchMapping": NodeKind.route,
    "@Path": NodeKind.route,
}

_SPRING_FEATURES = {
    "@Transactional": "transactional",
    "@Autowired": "dependency_injection",
    "@Scheduled": "scheduled_job",
    "@Quartz": "scheduled_job",
    "@Bean": "bean_definition",
}

_MYBATIS_ANNOTATIONS = ("@Mapper", "@Select", "@Insert", "@Update", "@Delete")


def merge_custom_patterns(
    project_patterns: CustomFrameworkPatterns | None,
    repo_patterns: CustomFrameworkPatterns | None = None,
) -> CustomFrameworkPatterns:
    """合并 project 级与 repo 级自定义框架模式（repo 覆盖同名 framework 的 pattern）。"""
    merged: CustomFrameworkPatterns = {
        name: dict(patterns)
        for name, patterns in (project_patterns or {}).items()
    }
    for name, patterns in (repo_patterns or {}).items():
        merged.setdefault(name, {}).update(patterns)
    return merged


def parse_custom_patterns(raw: object) -> CustomFrameworkPatterns:
    """解析 YAML ``custom_patterns`` / ``framework_patterns`` 段。"""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        return {}

    out: CustomFrameworkPatterns = {}
    for framework_name, patterns in raw.items():
        if not isinstance(framework_name, str) or not framework_name.strip():
            continue
        if not isinstance(patterns, dict):
            continue
        parsed: dict[str, str] = {}
        for pattern, role in patterns.items():
            if isinstance(pattern, str) and pattern.strip() and isinstance(role, str) and role.strip():
                parsed[pattern.strip()] = role.strip()
        if parsed:
            out[framework_name.strip()] = parsed
    return out


def extract_framework_metadata(
    file_paths: list[str],
    repo_root: str,
    graph: CodeGraph | None = None,
    *,
    custom_patterns: CustomFrameworkPatterns | None = None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫描 Java 文件的 Spring/MyBatis 注解，并应用自定义框架模式。"""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    custom_patterns = custom_patterns or {}

    name_to_id: dict[str, list[str]] = {}
    if graph:
        for n in graph.nodes:
            if n.kind in (NodeKind.class_, NodeKind.interface, NodeKind.method):
                name_to_id.setdefault(n.name, []).append(n.id)

    for rel_path in file_paths:
        full_path = os.path.join(repo_root, rel_path)
        if not os.path.exists(full_path) or not rel_path.endswith(".java"):
            continue

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        package = _extract_package(content)
        class_name = _extract_primary_class(content)

        for annotation, kind in _SPRING_ANNOTATIONS.items():
            aname = annotation.lstrip("@")
            if class_name and aname in (
                "Component", "Service", "Repository", "Controller", "RestController",
                "Path", "Configuration", "ConfigurationProperties",
            ):
                continue
            if annotation in content or f"@{aname}" in content:
                node_id = f"{rel_path}::Spring::{aname}"
                nodes.append(GraphNode(
                    id=node_id,
                    kind=kind,
                    name=f"spring:{aname}",
                    file_path=rel_path,
                    language="java",
                    qualified_name=f"{package}.{aname}" if package else aname,
                    metadata={"framework": "spring", "annotation": annotation},
                ))

        for annotation, feature in _SPRING_FEATURES.items():
            if annotation in content:
                node_id = f"{rel_path}::Spring::{feature}"
                nodes.append(GraphNode(
                    id=node_id,
                    kind=NodeKind.config if feature in ("transactional", "dependency_injection") else NodeKind.job,
                    name=feature,
                    file_path=rel_path,
                    language="java",
                    metadata={"framework": "spring", "feature": feature},
                ))

        if any(a in content for a in _MYBATIS_ANNOTATIONS):
            mapper_name = class_name or os.path.splitext(os.path.basename(rel_path))[0]
            node_id = f"{rel_path}::MyBatis::mapper"
            nodes.append(GraphNode(
                id=node_id,
                kind=NodeKind.interface,
                name=f"{mapper_name}Mapper",
                file_path=rel_path,
                language="java",
                qualified_name=f"{package}.{mapper_name}" if package else mapper_name,
                metadata={"framework": "mybatis", "role": "mapper"},
            ))

        nodes.extend(
            _extract_custom_framework_nodes(content, rel_path, package, custom_patterns)
        )

        for method_name in re.findall(
            r"@Transactional\b[^;]*?\n\s*(?:public|private|protected)\s+[\w<>\[\],\s]+?\s+(\w+)\s*\([^)]*\)",
            content, re.DOTALL,
        ):
            _add_edge(edges, seen_edges,
                      f"{rel_path}::{method_name}", f"{rel_path}::Spring::transactional",
                      EdgeKind.references, 0.85)

        for inj_type in re.findall(
            r"@Autowired\b(?:\s*\n\s*)?(?:private|protected|public)?\s*([\w.]+)\s+\w+\s*;",
            content,
        ):
            targets = name_to_id.get(inj_type, [])
            owner = f"{rel_path}::{class_name}" if class_name else rel_path
            for target in targets[:2]:
                _add_edge(edges, seen_edges, owner, target, EdgeKind.references, 0.75)

        if class_name:
            ctor = re.search(
                rf"public\s+{re.escape(class_name)}\s*\(([^)]{{0,500}})\)",
                content, re.DOTALL,
            )
            if ctor:
                for param_type in re.findall(r"([\w.]+)\s+\w+", ctor.group(1)):
                    short = param_type.split(".")[-1]
                    for target in name_to_id.get(short, [])[:2]:
                        _add_edge(edges, seen_edges,
                                  f"{rel_path}::{class_name}", target,
                                  EdgeKind.references, 0.8)

        for ret_type, method_name in re.findall(
            r"@Bean\b[^;]*?\n\s*(?:public|protected)\s+([\w.]+)\s+(\w+)\s*\(",
            content, re.DOTALL,
        ):
            short = ret_type.split(".")[-1]
            bean_id = f"{rel_path}::Spring::bean_definition"
            for target in name_to_id.get(short, [])[:1]:
                _add_edge(edges, seen_edges, bean_id, target, EdgeKind.references, 0.7)
            _add_edge(edges, seen_edges,
                      f"{rel_path}::{method_name}", bean_id, EdgeKind.references, 0.65)

    return nodes, edges


def extract_spring_metadata(
    file_paths: list[str],
    repo_root: str,
    graph: CodeGraph | None = None,
    *,
    custom_patterns: CustomFrameworkPatterns | None = None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """兼容旧名；等价于 :func:`extract_framework_metadata`。"""
    return extract_framework_metadata(
        file_paths, repo_root, graph, custom_patterns=custom_patterns,
    )


def _extract_custom_framework_nodes(
    content: str,
    rel_path: str,
    package: str,
    custom_patterns: Mapping[str, Mapping[str, str]],
) -> list[GraphNode]:
    nodes: list[GraphNode] = []
    for framework_name, patterns in custom_patterns.items():
        for pattern, role in patterns.items():
            if pattern not in content:
                continue
            node_id = f"{rel_path}::{framework_name}::{role}"
            nodes.append(GraphNode(
                id=node_id,
                kind=NodeKind.class_,
                name=role,
                file_path=rel_path,
                language="java",
                qualified_name=f"{package}.{role}" if package else role,
                metadata={
                    "framework": framework_name,
                    "role": role,
                    "pattern": pattern,
                    "custom": True,
                },
            ))
    return nodes


def _extract_package(content: str) -> str:
    m = re.search(r"package\s+([\w.]+)\s*;", content)
    return m.group(1) if m else ""


def _extract_primary_class(content: str) -> str:
    m = re.search(r"(?:public\s+)?(?:class|interface)\s+(\w+)", content)
    return m.group(1) if m else ""


def _add_edge(
    edges: list[GraphEdge],
    seen: set[tuple[str, str, str]],
    source: str,
    target: str,
    kind: EdgeKind,
    confidence: float,
):
    key = (source, target, kind.value)
    if key in seen or source == target:
        return
    seen.add(key)
    edges.append(GraphEdge(
        source=source, target=target, kind=kind,
        provenance="heuristic", confidence=confidence,
    ))
