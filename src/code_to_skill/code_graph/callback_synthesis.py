"""动态派发 / 接口回调边合成。"""
from __future__ import annotations

from collections import defaultdict

from .types import CodeGraph, GraphEdge, EdgeKind, GraphNode, NodeKind


def synthesize_interface_dispatch(graph: CodeGraph) -> list[GraphEdge]:
    """为 Java 风格 interface 调用补充实现类 method 的合成 calls 边。

    当 calls 边指向 interface 上的 method 时，为每个 implementor 的同名 method
    添加低置信度派发边，便于 trace 穿透到具体实现。
    """
    node_map: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
    seen: set[tuple[str, str, str]] = {
        (e.source, e.target, e.kind.value) for e in graph.edges
    }
    synthesized: list[GraphEdge] = []

    # interface class node -> implementor class nodes
    implementors: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.kind != EdgeKind.implements:
            continue
        implementors[edge.target].append(edge.source)

    # class node id -> method name -> method node id
    class_methods: dict[str, dict[str, str]] = defaultdict(dict)
    for node in graph.nodes:
        if node.kind != NodeKind.method:
            continue
        parent = _parent_class_id(node, node_map, graph)
        if parent:
            class_methods[parent][node.name] = node.id

    for edge in list(graph.edges):
        if edge.kind != EdgeKind.calls:
            continue
        target = node_map.get(edge.target)
        if not target or target.kind != NodeKind.method:
            continue
        iface_class = _parent_class_id(target, node_map, graph)
        if not iface_class or iface_class not in implementors:
            continue
        method_name = target.name
        for impl_class in implementors[iface_class]:
            impl_method = class_methods.get(impl_class, {}).get(method_name)
            if not impl_method or impl_method == edge.target:
                continue
            key = (edge.source, impl_method, EdgeKind.calls.value)
            if key in seen:
                continue
            seen.add(key)
            synthesized.append(GraphEdge(
                source=edge.source,
                target=impl_method,
                kind=EdgeKind.calls,
                confidence=0.42,
                provenance="heuristic",
            ))

    graph.edges.extend(synthesized)
    return synthesized


def _parent_class_id(
    method: GraphNode,
    node_map: dict[str, GraphNode],
    graph: CodeGraph,
) -> str | None:
    """从 contains 边或同文件 class 节点推断 method 所属 class。"""
    for edge in graph.edges:
        if edge.kind != EdgeKind.contains or edge.target != method.id:
            continue
        parent = node_map.get(edge.source)
        if parent and parent.kind in (NodeKind.class_, NodeKind.interface):
            return parent.id
    # 同文件唯一 class/interface（单类型文件常见）
    candidates = [
        n.id for n in graph.nodes
        if n.file_path == method.file_path
        and n.kind in (NodeKind.class_, NodeKind.interface)
    ]
    return candidates[0] if len(candidates) == 1 else None
