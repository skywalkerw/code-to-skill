"""图遍历与搜索。

提供：callee/caller/impact 分析 + 符号搜索 + 影响范围计算。
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterator

from .types import CodeGraph, GraphNode, GraphEdge, NodeKind, EdgeKind


class GraphTraverser:
    """图遍历器。"""

    def __init__(self, graph: CodeGraph):
        self.graph = graph
        self._node_map: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
        self._name_index: dict[str, list[str]] = defaultdict(list)
        self._kind_index: dict[str, list[str]] = defaultdict(list)
        self._callee_index: dict[str, list[str]] = defaultdict(list)  # caller → [callee]
        self._caller_index: dict[str, list[str]] = defaultdict(list)  # callee → [caller]
        self._path_callee_index: dict[str, list[str]] = defaultdict(list)  # 路径搜索专用
        self._path_caller_index: dict[str, list[str]] = defaultdict(list)
        self._file_index: dict[str, list[str]] = defaultdict(list)    # file_path → [node_id]

        self._build_indices()

    def _build_indices(self):
        for node in self.graph.nodes:
            self._name_index[node.name].append(node.id)
            self._kind_index[node.kind.value].append(node.id)
            self._file_index[node.file_path].append(node.id)

        # 邻域遍历：calls / references / entry_to（不含 imports，避免 Spring 图爆炸）
        _neighbor_kinds = (
            EdgeKind.calls,
            EdgeKind.references,
            EdgeKind.entry_to,
        )
        # 路径搜索：calls + references + entry_to + contains（类→方法）+ 接口→实现；不含 imports
        _path_kinds = (
            EdgeKind.calls,
            EdgeKind.references,
            EdgeKind.entry_to,
            EdgeKind.contains,
        )
        for edge in self.graph.edges:
            if edge.kind in _neighbor_kinds:
                self._callee_index[edge.source].append(edge.target)
                self._caller_index[edge.target].append(edge.source)
            elif edge.kind == EdgeKind.implements:
                self._callee_index[edge.target].append(edge.source)
                self._caller_index[edge.source].append(edge.target)
            if edge.kind in _path_kinds:
                self._path_callee_index[edge.source].append(edge.target)
                self._path_caller_index[edge.target].append(edge.source)
            elif edge.kind == EdgeKind.implements:
                self._path_callee_index[edge.target].append(edge.source)
                self._path_caller_index[edge.source].append(edge.target)

    # ── 遍历 API ───────────────────────────────────────────

    def callees(self, node_id: str, depth: int = 1) -> list[str]:
        """返回 node 调用的所有节点（支持深度遍历）。"""
        visited: set[str] = set()
        queue = deque([(node_id, 0)])
        result: list[str] = []

        while queue:
            current, d = queue.popleft()
            if current in visited or d > depth:
                continue
            visited.add(current)
            if d > 0:
                result.append(current)
            for target in self._callee_index.get(current, []):
                if target not in visited:
                    queue.append((target, d + 1))

        return result

    def callers(self, node_id: str, depth: int = 1) -> list[str]:
        """返回调用 node 的所有节点（谁调用了它）。"""
        visited: set[str] = set()
        queue = deque([(node_id, 0)])
        result: list[str] = []

        while queue:
            current, d = queue.popleft()
            if current in visited or d > depth:
                continue
            visited.add(current)
            if d > 0:
                result.append(current)
            for source in self._caller_index.get(current, []):
                if source not in visited:
                    queue.append((source, d + 1))

        return result

    def impact(self, node_id: str, depth: int = 3) -> dict:
        """分析修改影响范围：受影响的直接/间接 callee。"""
        return {
            "node_id": node_id,
            "node_name": self._node_map[node_id].name if node_id in self._node_map else "?",
            "direct_callees": self.callees(node_id, depth=1),
            "all_callees": self.callees(node_id, depth=depth),
            "direct_callers": self.callers(node_id, depth=1),
            "all_callers": self.callers(node_id, depth=depth),
        }

    def entry_to_target(
        self,
        entrypoint_id: str,
        target_id: str,
        *,
        max_depth: int = 12,
        max_paths: int = 3,
    ) -> list[dict] | None:
        """查找从起点到目标节点的路径（可读格式）。"""
        if entrypoint_id not in self._node_map or target_id not in self._node_map:
            return None

        raw_paths: list[list[str]] = []
        # BFS 最短路径；限制展开次数，避免大图超时
        max_expansions = 20_000
        expansions = 0
        queue: deque[tuple[str, list[str]]] = deque([(entrypoint_id, [entrypoint_id])])
        shortest_len: int | None = None

        while queue and len(raw_paths) < max_paths and expansions < max_expansions:
            current, path = queue.popleft()
            if shortest_len is not None and len(path) > shortest_len:
                break
            if current == target_id:
                shortest_len = len(path)
                raw_paths.append(path)
                continue
            if len(path) >= max_depth:
                continue
            for callee in self._path_callee_index.get(current, []):
                if callee in path:
                    continue
                expansions += 1
                queue.append((callee, path + [callee]))

        if not raw_paths:
            return None
        return [self._format_path(p) for p in raw_paths]

    def _format_path(self, node_ids: list[str]) -> dict:
        """将 node id 路径转为 LLM 可读结构。"""
        nodes = []
        for nid in node_ids:
            n = self._node_map.get(nid)
            if n:
                nodes.append({
                    "id": nid,
                    "name": n.name,
                    "kind": n.kind.value,
                    "file_path": n.file_path,
                    "start_line": n.start_line,
                })
            else:
                nodes.append({"id": nid, "name": nid.split("::")[-1]})
        labels = [n.get("name", "?") for n in nodes]
        return {
            "node_ids": node_ids,
            "nodes": nodes,
            "summary": " → ".join(labels),
            "length": len(node_ids),
        }

    # ── 搜索 API ───────────────────────────────────────────

    def find_symbol(self, name: str, exact: bool = True) -> list[GraphNode]:
        """按名称搜索符号。

        Args:
            name: 符号名称
            exact: True=精确匹配, False=模糊匹配
        """
        if exact:
            ids = self._name_index.get(name, [])
        else:
            ids = []
            lower = name.lower()
            for n, nids in self._name_index.items():
                if lower in n.lower():
                    ids.extend(nids)
        return [self._node_map[nid] for nid in ids if nid in self._node_map]

    def find_by_kind(self, kind: NodeKind | str) -> list[GraphNode]:
        """按节点类型搜索。"""
        k = kind.value if isinstance(kind, NodeKind) else kind
        ids = self._kind_index.get(k, [])
        return [self._node_map[nid] for nid in ids if nid in self._node_map]

    def find_by_file(self, file_path: str) -> list[GraphNode]:
        """查找文件中的所有节点。"""
        ids = self._file_index.get(file_path, [])
        return [self._node_map[nid] for nid in ids if nid in self._node_map]

    def search(self, query: str) -> list[GraphNode]:
        """综合搜索：名称模糊匹配 + 类型匹配。"""
        results: list[GraphNode] = []
        lower = query.lower()

        # 按名称模糊
        results.extend(self.find_symbol(query, exact=False))

        # 按类型
        for kind in NodeKind:
            if kind.value == lower:
                results.extend(self.find_by_kind(kind))

        # 按文件路径片段
        for fpath, nids in self._file_index.items():
            if lower in fpath.lower():
                for nid in nids:
                    if nid in self._node_map:
                        n = self._node_map[nid]
                        if n not in results:
                            results.append(n)

        return results

    # ── 统计 ───────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self.graph.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.graph.edges)

    def stats(self) -> dict:
        from collections import Counter
        return {
            "nodes": self.node_count,
            "edges": self.edge_count,
            "kinds": dict(Counter(n.kind.value for n in self.graph.nodes)),
        }
