"""框架专用提取器：Spring / MyBatis / Fineract 模式。"""
from __future__ import annotations

import os
import re

from .types import CodeGraph, GraphNode, GraphEdge, NodeKind, EdgeKind


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

_FINERACT_PATTERNS = {
    "CommandHandler": "command_handler",
    "ReadPlatformService": "read_service",
    "WritePlatformService": "write_service",
    "AccountingProcessor": "accounting_processor",
    "ApiResource": "api_resource",
}


def extract_spring_metadata(
    file_paths: list[str],
    repo_root: str,
    graph: CodeGraph | None = None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """扫描 Java 文件的 Spring/MyBatis 注解，补充节点和边。"""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

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
            # 注解挂在真实类上时不再建同名伪节点（避免污染 Component/Path 等符号搜索）
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

        for pattern, role in _FINERACT_PATTERNS.items():
            if pattern in content:
                node_id = f"{rel_path}::Fineract::{role}"
                nodes.append(GraphNode(
                    id=node_id,
                    kind=NodeKind.class_,
                    name=role,
                    file_path=rel_path,
                    language="java",
                    metadata={"framework": "fineract", "role": role},
                ))

        # @Transactional → method references
        for method_name in re.findall(
            r"@Transactional\b[^;]*?\n\s*(?:public|private|protected)\s+[\w<>\[\],\s]+?\s+(\w+)\s*\([^)]*\)",
            content, re.DOTALL,
        ):
            _add_edge(edges, seen_edges,
                      f"{rel_path}::{method_name}", f"{rel_path}::Spring::transactional",
                      EdgeKind.references, 0.85)

        # @Autowired 字段注入 → references 边（支持注解与字段分行）
        for inj_type in re.findall(
            r"@Autowired\b(?:\s*\n\s*)?(?:private|protected|public)?\s*([\w.]+)\s+\w+\s*;",
            content,
        ):
            targets = name_to_id.get(inj_type, [])
            owner = f"{rel_path}::{class_name}" if class_name else rel_path
            for target in targets[:2]:
                _add_edge(edges, seen_edges, owner, target, EdgeKind.references, 0.75)

        # 构造器注入：public Foo(Bar bar, Baz baz)
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

        # @Bean 方法 → config 节点连到返回类型
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
