"""图查询引擎 — 对齐 external/codegraph QueryBuilder 子集。"""
from __future__ import annotations

import re
from typing import Any

from .db import GraphDB
from .query_parser import parse_query
from .traversal import GraphTraverser
from .types import GraphNode, NodeKind


def extract_search_terms(query: str) -> list[str]:
    """从自然语言/符号查询中提取检索词。"""
    terms: set[str] = set()
    for pat in (
        r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)+)\b",  # CamelCase
        r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b",  # snake_case
        r"\b([A-Z]{2,})\b",  # ACRONYM
    ):
        for m in re.finditer(pat, query):
            if len(m.group(1)) >= 2:
                terms.add(m.group(1))
    for word in re.findall(r"[\w.]+", query):
        if len(word) >= 3 and not word.isdigit():
            terms.add(word)
            if "." in word:
                terms.update(p for p in word.split(".") if len(p) >= 2)
    return list(terms)[:12]


class GraphQueryEngine:
    """基于 GraphDB + GraphTraverser 的查询门面。"""

    def __init__(self, db: GraphDB):
        self.db = db
        self._traverser: GraphTraverser | None = None

    @classmethod
    def from_path(cls, db_path: str) -> "GraphQueryEngine":
        return cls(GraphDB(db_path))

    def _traverser_instance(self) -> GraphTraverser:
        if self._traverser is None:
            self._traverser = GraphTraverser(self.db.load_graph())
        return self._traverser

    def invalidate_cache(self):
        self._traverser = None

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """综合符号搜索：FTS + 多词 OR + 遍历器模糊。支持 kind:/file: 前缀。"""
        parsed = parse_query(query)
        hits: list[dict[str, Any]] = []
        seen: set[str] = set()

        for row in self.db.search_nodes(
            parsed.text_query or query,
            limit=limit,
            kinds=parsed.kinds or None,
            file_patterns=parsed.file_patterns or None,
        ):
            if row["id"] not in seen:
                seen.add(row["id"])
                hits.append(row)

        if len(hits) < limit:
            for term in extract_search_terms(query):
                for node in self._traverser_instance().find_symbol(term, exact=False):
                    if node.id not in seen:
                        seen.add(node.id)
                        hits.append({
                            "id": node.id,
                            "name": node.name,
                            "kind": node.kind.value,
                            "file_path": node.file_path,
                            "start_line": node.start_line,
                            "end_line": node.end_line,
                            "score": 0.5,
                        })
                    if len(hits) >= limit:
                        break
                if len(hits) >= limit:
                    break

        return hits[:limit]

    def get_node(self, node_id: str) -> GraphNode | None:
        return self.db.get_node(node_id)

    def find_by_name(self, name: str, exact: bool = True) -> list[GraphNode]:
        return self._traverser_instance().find_symbol(name, exact=exact)

    def callers(self, node_id: str, depth: int = 1) -> list[dict[str, Any]]:
        t = self._traverser_instance()
        return [_node_brief(t._node_map[nid]) for nid in t.callers(node_id, depth=depth) if nid in t._node_map]

    def callees(self, node_id: str, depth: int = 1) -> list[dict[str, Any]]:
        t = self._traverser_instance()
        return [_node_brief(t._node_map[nid]) for nid in t.callees(node_id, depth=depth) if nid in t._node_map]

    def trace(
        self,
        from_id: str,
        to_id: str,
        *,
        max_depth: int = 12,
        max_paths: int = 3,
    ) -> list[dict] | None:
        return self._traverser_instance().entry_to_target(
            from_id, to_id, max_depth=max_depth, max_paths=max_paths,
        )

    def impact(self, node_id: str, depth: int = 2) -> dict[str, Any]:
        return self._traverser_instance().impact(node_id, depth=depth)

    def stats(self) -> dict[str, Any]:
        return self.db.get_stats()


def _node_brief(node: GraphNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "name": node.name,
        "kind": node.kind.value,
        "file_path": node.file_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
    }
