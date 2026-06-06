"""多仓库图谱注册表 — 统一搜索 / 上下文 / trace。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .context_builder import ContextBuilder
from .graph_queries import GraphQueryEngine


@dataclass
class GraphSource:
    repo_id: str = ""
    db_path: str = ""
    repo_root: str = ""

    def valid(self) -> bool:
        return bool(self.db_path) and os.path.isfile(self.db_path)


@dataclass
class GraphRegistry:
    """聚合多个 graph.db，供 SkillOpt / MCP 查询。"""

    sources: list[GraphSource] = field(default_factory=list)
    _engines: dict[str, GraphQueryEngine] = field(default_factory=dict, repr=False)

    @classmethod
    def from_sources(cls, items: list[GraphSource | dict]) -> "GraphRegistry":
        parsed: list[GraphSource] = []
        for item in items:
            if isinstance(item, GraphSource):
                parsed.append(item)
            else:
                parsed.append(GraphSource(
                    repo_id=item.get("repo_id", ""),
                    db_path=item.get("db_path", item.get("graph_db_path", "")),
                    repo_root=item.get("repo_root", item.get("path", "")),
                ))
        return cls(sources=[s for s in parsed if s.valid()])

    @classmethod
    def single(cls, db_path: str, repo_root: str = "", repo_id: str = "") -> "GraphRegistry":
        return cls.from_sources([GraphSource(repo_id=repo_id, db_path=db_path, repo_root=repo_root)])

    @property
    def enabled(self) -> bool:
        return bool(self.sources)

    def _engine(self, source: GraphSource) -> GraphQueryEngine:
        key = source.db_path
        if key not in self._engines:
            self._engines[key] = GraphQueryEngine.from_path(key)
        return self._engines[key]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        per = max(limit // max(len(self.sources), 1), 3)

        for src in self.sources:
            for row in self._engine(src).search(query, limit=per):
                uid = f"{src.repo_id}:{row.get('id', '')}"
                if uid in seen:
                    continue
                seen.add(uid)
                row = dict(row)
                row["repo_id"] = src.repo_id
                hits.append(row)
                if len(hits) >= limit:
                    return hits
        return hits[:limit]

    def find_by_name(self, symbol: str, exact: bool = False) -> list[tuple[GraphSource, Any]]:
        out: list[tuple[GraphSource, Any]] = []
        for src in self.sources:
            for node in self._engine(src).find_by_name(symbol, exact=exact):
                out.append((src, node))
        return out

    @staticmethod
    def _rank_symbol_matches(
        matches: list[tuple[GraphSource, Any]],
        symbol: str,
        *,
        near_file: str = "",
    ) -> list[tuple[GraphSource, Any]]:
        """优先真实实现类，降低 Swagger/桩类误匹配。"""

        def _score(item: tuple[GraphSource, Any]) -> int:
            _src, node = item
            s = 0
            if node.name == symbol:
                s += 10
            if near_file and near_file in (node.file_path or ""):
                s += 25
            elif symbol and symbol in (node.file_path or ""):
                s += 12
            if node.kind.value in ("class", "interface"):
                s += 5
            fp = (node.file_path or "").lower()
            if "swagger" in fp or "generated" in fp:
                s -= 20
            return s

        return sorted(matches, key=_score, reverse=True)

    def _best_symbol_match(
        self,
        symbol: str,
        *,
        exact: bool = False,
        near_file: str = "",
    ) -> tuple[GraphSource, Any] | None:
        matches = self.find_by_name(symbol, exact=exact)
        if not matches:
            return None
        ranked = self._rank_symbol_matches(matches, symbol, near_file=near_file)
        return ranked[0]

    def build_context(self, query: str, max_blocks: int = 6, *, deep: bool = False) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        explored: list[dict[str, Any]] = []
        markdown_parts: list[str] = []
        for src in self.sources:
            builder = ContextBuilder(self._engine(src), src.repo_root)
            per = max(2, max_blocks // len(self.sources))
            if deep:
                ctx = builder.build_deep(query, max_blocks=per, explore_top=2)
                for ex in ctx.get("explored", []):
                    ex = dict(ex)
                    ex["repo_id"] = src.repo_id
                    explored.append(ex)
                if ctx.get("markdown"):
                    markdown_parts.append(ctx["markdown"])
            else:
                ctx = builder.build(query, max_blocks=per)
            for b in ctx.get("blocks", []):
                b = dict(b)
                b["repo_id"] = src.repo_id
                blocks.append(b)
            if len(blocks) >= max_blocks:
                break
        out: dict[str, Any] = {
            "query": query,
            "blocks": blocks[:max_blocks],
            "repos": [s.repo_id for s in self.sources],
        }
        if deep:
            out["explored"] = explored
            out["markdown"] = "\n\n".join(markdown_parts)
        return out

    def trace(
        self,
        symbol: str,
        direction: str = "both",
        to_symbol: str = "",
        *,
        depth: int = 2,
        path_max_depth: int = 12,
        from_entry: str = "",
    ) -> dict[str, Any]:
        best = self._best_symbol_match(symbol, exact=False)
        if best is None and not from_entry:
            return {"error": f"symbol not found: {symbol}"}
        src, node = best if best else (None, None)
        if node is None and from_entry:
            return {"error": f"symbol not found: {symbol}"}

        assert src is not None and node is not None
        engine = self._engine(src)
        depth = max(1, min(depth, 6))
        out: dict[str, Any] = {
            "symbol": symbol,
            "repo_id": src.repo_id,
            "node_id": node.id,
            "name": node.name,
            "kind": node.kind.value,
            "file_path": node.file_path,
        }
        if direction in ("callers", "both"):
            out["callers"] = engine.callers(node.id, depth=depth)
        if direction in ("callees", "both"):
            out["callees"] = engine.callees(node.id, depth=depth)

        start_id = node.id
        if from_entry:
            entry_id = self._resolve_entry_start(engine, from_entry, node.file_path)
            if entry_id:
                start_id = entry_id
                out["from_entry"] = from_entry
                out["start_node_id"] = entry_id

        if to_symbol:
            t_best = self._best_symbol_match(
                to_symbol, exact=False, near_file=node.file_path if node else "",
            )
            if t_best is None:
                out["paths_to_error"] = f"target symbol not found: {to_symbol}"
            else:
                t_src, t_node = t_best
                if t_src.db_path != src.db_path:
                    out["paths_to_error"] = "target symbol is in a different graph.db"
                else:
                    paths = engine.trace(
                        start_id, t_node.id,
                        max_depth=path_max_depth,
                    )
                    if paths:
                        out["paths_to"] = paths
                        out["target_symbol"] = to_symbol
                        out["target_node_id"] = t_node.id
                    else:
                        out["paths_to_error"] = (
                            f"no path from {symbol} to {to_symbol} "
                            f"(depth≤{path_max_depth}, edges: calls/references/entry_to/contains/implements)"
                        )
        return out

    @staticmethod
    def _resolve_entry_start(engine: GraphQueryEngine, entry_hint: str, near_file: str) -> str | None:
        """按 entry 节点 id 前缀或 kind 匹配起点（entry:kind:handler_id）。"""
        graph = engine._traverser_instance().graph
        hint = entry_hint.strip().lower()
        candidates: list[str] = []
        for n in graph.nodes:
            if n.kind.value != "route" or not n.id.startswith("entry:"):
                continue
            if hint in n.id.lower() or hint in n.name.lower():
                candidates.append(n.id)
            elif near_file and n.file_path == near_file:
                candidates.append(n.id)
        return candidates[0] if candidates else None

    def impact(self, symbol: str, depth: int = 2) -> dict[str, Any]:
        matches = self.find_by_name(symbol, exact=False)
        if not matches:
            return {"error": f"symbol not found: {symbol}"}
        src, node = matches[0]
        data = self._engine(src).impact(node.id, depth=depth)
        data["repo_id"] = src.repo_id
        return data

    def explore_symbol(self, symbol: str, *, include_source: bool = True, max_lines: int = 80) -> dict[str, Any]:
        """单符号深度卡片：详情 + 源码 + callers/callees（对齐 codegraph_explore）。"""
        best = self._best_symbol_match(symbol, exact=False)
        if best is None:
            return {"error": f"symbol not found: {symbol}"}

        src, node = best
        engine = self._engine(src)
        detail = engine.get_node(node.id)
        payload: dict[str, Any] = {
            "symbol": symbol,
            "repo_id": src.repo_id,
            "id": node.id,
            "name": node.name,
            "kind": node.kind.value,
            "file_path": node.file_path,
            "start_line": node.start_line,
            "end_line": node.end_line,
            "qualified_name": getattr(detail, "qualified_name", "") if detail else "",
            "signature": getattr(detail, "signature", "") if detail else "",
            "docstring": getattr(detail, "docstring", "") if detail else "",
            "callers": engine.callers(node.id, depth=2),
            "callees": engine.callees(node.id, depth=2),
        }
        if include_source and src.repo_root and node.file_path:
            from .context_builder import ContextBuilder

            cb = ContextBuilder(engine, src.repo_root)
            snippet = cb._read_snippet(
                node.file_path, node.start_line, node.end_line, max_lines=max_lines,
            )
            if snippet:
                payload["source"] = snippet
        return payload

    def get_symbol_source(self, symbol: str, max_lines: int = 120) -> dict[str, Any]:
        """读取符号对应源码片段。"""
        card = self.explore_symbol(symbol, include_source=True, max_lines=max_lines)
        if card.get("error"):
            return card
        return {
            "symbol": symbol,
            "file_path": card.get("file_path"),
            "start_line": card.get("start_line"),
            "end_line": card.get("end_line"),
            "qualified_name": card.get("qualified_name"),
            "source": card.get("source", ""),
        }

    def stats(self) -> dict[str, Any]:
        repos: list[dict[str, Any]] = []
        total_nodes = 0
        for src in self.sources:
            st = self._engine(src).stats()
            total_nodes += st.get("nodes", 0)
            repos.append({"repo_id": src.repo_id, "db_path": src.db_path, **st})
        return {"repos": repos, "total_nodes": total_nodes, "repo_count": len(self.sources)}

    def primary_engine(self) -> GraphQueryEngine | None:
        if not self.sources:
            return None
        return self._engine(self.sources[0])

    def invalidate_caches(self) -> None:
        """图谱增量更新后清空引擎缓存并关闭 SQLite 连接。"""
        for engine in self._engines.values():
            engine.invalidate_cache()
            if hasattr(engine, "db") and hasattr(engine.db, "close"):
                engine.db.close()
        self._engines.clear()

    def list_files(self, pattern: str = "**/*", limit: int = 50) -> list[dict]:
        """跨仓库列出索引文件。"""
        files: list[dict] = []
        per = max(limit // max(len(self.sources), 1), 5)
        for src in self.sources:
            for row in self._engine(src).db.list_files(pattern=pattern, limit=per):
                files.append({**row, "repo_id": src.repo_id})
            if len(files) >= limit:
                break
        return files[:limit]

    def callers_of(self, symbol: str, depth: int = 2) -> dict[str, Any]:
        matches = self.find_by_name(symbol, exact=False)
        if not matches:
            return {"error": f"symbol not found: {symbol}"}
        src, node = matches[0]
        return {
            "symbol": symbol,
            "repo_id": src.repo_id,
            "node_id": node.id,
            "callers": self._engine(src).callers(node.id, depth=depth),
        }

    def callees_of(self, symbol: str, depth: int = 2) -> dict[str, Any]:
        matches = self.find_by_name(symbol, exact=False)
        if not matches:
            return {"error": f"symbol not found: {symbol}"}
        src, node = matches[0]
        return {
            "symbol": symbol,
            "repo_id": src.repo_id,
            "node_id": node.id,
            "callees": self._engine(src).callees(node.id, depth=depth),
        }
