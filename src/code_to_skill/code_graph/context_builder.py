"""上下文构建 — 对齐 external/codegraph ContextBuilder 精简版。"""
from __future__ import annotations

import os
from typing import Any

from .graph_queries import GraphQueryEngine, extract_search_terms


class ContextBuilder:
    """搜索 + 图扩展 + 源码片段，供 Agent / SkillOpt 注入。"""

    def __init__(self, engine: GraphQueryEngine, repo_root: str = ""):
        self.engine = engine
        self.repo_root = repo_root

    def build(
        self,
        query: str,
        max_blocks: int = 8,
        include_callers: bool = True,
        max_lines_per_block: int = 80,
    ) -> dict[str, Any]:
        """构建任务相关代码上下文。"""
        symbols = self.engine.search(query, limit=max_blocks * 2)
        blocks: list[dict[str, Any]] = []
        seen_files: set[str] = set()

        for sym in symbols:
            if len(blocks) >= max_blocks:
                break
            fpath = sym.get("file_path", "")
            if not fpath or fpath in seen_files:
                continue
            snippet = self._read_snippet(
                fpath,
                sym.get("start_line", 1),
                sym.get("end_line", 0),
                max_lines=max_lines_per_block,
            )
            if not snippet:
                continue
            seen_files.add(fpath)
            block: dict[str, Any] = {
                "symbol": sym.get("name"),
                "node_id": sym.get("id"),
                "kind": sym.get("kind"),
                "file_path": fpath,
                "start_line": sym.get("start_line"),
                "content": snippet,
            }
            if include_callers and sym.get("id"):
                block["callers"] = self.engine.callers(sym["id"], depth=1)[:5]
                block["callees"] = self.engine.callees(sym["id"], depth=1)[:5]
            blocks.append(block)

        return {
            "query": query,
            "search_terms": extract_search_terms(query),
            "symbol_count": len(symbols),
            "blocks": blocks,
            "stats": self.engine.stats(),
        }

    def build_deep(
        self,
        query: str,
        *,
        max_blocks: int = 6,
        explore_top: int = 2,
    ) -> dict[str, Any]:
        """深度上下文：搜索 + 顶部符号 explore（callers/callees/源码）。"""
        base = self.build(query, max_blocks=max_blocks, include_callers=True)
        explored: list[dict] = []
        seen_symbols: set[str] = set()

        for sym in self.engine.search(query, limit=explore_top * 3):
            name = sym.get("name", "")
            if not name or name in seen_symbols:
                continue
            seen_symbols.add(name)
            node_id = sym.get("id", "")
            card: dict = {
                "name": name,
                "kind": sym.get("kind"),
                "file_path": sym.get("file_path"),
                "qualified_name": "",
                "source": "",
                "callers": [],
                "callees": [],
            }
            if node_id:
                detail = self.engine.get_node(node_id)
                if detail:
                    card["qualified_name"] = detail.qualified_name
                card["callers"] = self.engine.callers(node_id, depth=1)[:5]
                card["callees"] = self.engine.callees(node_id, depth=1)[:5]
            card["source"] = self._read_snippet(
                sym.get("file_path", ""),
                sym.get("start_line", 1),
                sym.get("end_line", 0),
                max_lines=100,
            )
            explored.append(card)
            if len(explored) >= explore_top:
                break

        base["explored"] = explored
        base["markdown"] = self.format_markdown(base)
        if explored:
            base["markdown"] += "\n\n## Explored Symbols\n"
            for ex in explored:
                base["markdown"] += f"\n### `{ex['name']}` ({ex.get('qualified_name') or ex.get('kind')})\n"
                if ex.get("callers"):
                    base["markdown"] += "Callers: " + ", ".join(c["name"] for c in ex["callers"]) + "\n"
                if ex.get("callees"):
                    base["markdown"] += "Callees: " + ", ".join(c["name"] for c in ex["callees"]) + "\n"
                if ex.get("source"):
                    base["markdown"] += f"```\n{ex['source']}\n```\n"
        return base

    def format_markdown(self, ctx: dict[str, Any]) -> str:
        parts = [f"## Code Context: {ctx.get('query', '')}", ""]
        for i, b in enumerate(ctx.get("blocks", []), 1):
            parts.append(f"### {i}. `{b.get('symbol')}` ({b.get('kind')}) — `{b.get('file_path')}`")
            if b.get("callers"):
                parts.append(f"Callers: {', '.join(c['name'] for c in b['callers'])}")
            if b.get("callees"):
                parts.append(f"Callees: {', '.join(c['name'] for c in b['callees'])}")
            parts.append(f"```{self._lang_tag(b.get('file_path', ''))}")
            parts.append(b.get("content", ""))
            parts.append("```")
            parts.append("")
        return "\n".join(parts)

    def _read_snippet(
        self,
        rel_path: str,
        start_line: int,
        end_line: int,
        max_lines: int,
    ) -> str:
        if not self.repo_root:
            return ""
        full = os.path.join(self.repo_root, rel_path)
        if not os.path.isfile(full):
            return ""
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return ""
        start = max(1, start_line or 1)
        end = end_line if end_line and end_line >= start else start + max_lines - 1
        end = min(end, start + max_lines - 1, len(lines))
        return "".join(lines[start - 1:end])

    @staticmethod
    def _lang_tag(path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        return {".java": "java", ".py": "python", ".ts": "typescript", ".go": "go"}.get(ext, "")
