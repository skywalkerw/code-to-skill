"""Spring 框架专用提取器。

识别 Spring Boot/JAX-RS 特有的注解和模式：
- @Service, @Repository, @Component, @RestController
- @Transactional, @Autowired
- @RequestMapping, @GetMapping, @PostMapping, @Path, @GET
- CommandHandler 模式（Fineract 特有）
"""
from __future__ import annotations

import re
import os

from .types import CodeGraph, GraphNode, GraphEdge, NodeKind, EdgeKind

# Spring 注解 → NodeKind 映射
_SPRING_ANNOTATIONS: dict[str, NodeKind] = {
    # 组件类型
    "@Service": NodeKind.class_,
    "@Repository": NodeKind.class_,
    "@Component": NodeKind.class_,
    "@RestController": NodeKind.route,
    "@Controller": NodeKind.route,
    # 配置
    "@Configuration": NodeKind.config,
    "@ConfigurationProperties": NodeKind.config,
    # REST/JAX-RS
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

# Fineract 特有模式
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
    """扫描 Java 文件的 Spring 注解，补充节点和边。

    Returns:
        (new_nodes, new_edges) 可合并到现有 CodeGraph
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    for rel_path in file_paths:
        full_path = os.path.join(repo_root, rel_path)
        if not os.path.exists(full_path) or not rel_path.endswith(".java"):
            continue

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        # 检测 Spring 注解
        for annotation, kind in _SPRING_ANNOTATIONS.items():
            aname = annotation.lstrip("@")
            if annotation in content or f"@{aname}" in content:
                node_id = f"{rel_path}::Spring::{aname}"
                nodes.append(GraphNode(
                    id=node_id,
                    kind=kind,
                    name=aname,
                    file_path=rel_path,
                    language="java",
                    metadata={"framework": "spring", "annotation": annotation},
                ))

        # 检测 Spring 特性
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

        # 检测 Fineract 特有模式
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

        # 检测 @Transactional 方法 → 连接边
        tx_matches = re.findall(r"@Transactional\b[^;]*?\n\s*(?:public|private|protected)\s+[\w<>\[\],\s]+?\s+(\w+)\s*\([^)]*\)", content, re.DOTALL)
        for method_name in tx_matches:
            method_node_id = f"{rel_path}::{method_name}"
            tx_node_id = f"{rel_path}::Spring::transactional"
            edges.append(GraphEdge(
                source=method_node_id,
                target=tx_node_id,
                kind=EdgeKind.references,
                provenance="heuristic",
                confidence=0.85,
            ))

    return nodes, edges
