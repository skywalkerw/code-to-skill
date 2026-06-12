"""Pure code tools: file search/read plus graph queries.

This module is intentionally independent of SkillOpt/M4. CLI, MCP, and M4 all
consume this tool layer through the same handler interface.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_to_skill.codegraph_mcp.tool_registry import (
    execute_graph_tool,
    file_tool_definitions,
    resolve_file_tool,
    resolve_tool,
)

logger = logging.getLogger(__name__)

_MAX_READ_LINES = 300
_MAX_SEARCH_RESULTS = 15
_MAX_FILE_BYTES = 120_000


@dataclass
class CodeRepoConfig:
    """单个代码仓库的读取范围。"""
    path: str
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


class CodeToolsHandler:
    """执行文件读取与图谱查询工具。"""

    def __init__(
        self,
        repos: list[CodeRepoConfig] | list[dict] | None = None,
        graph_db_path: str | None = None,
        repo_root: str | None = None,
        graph_sources: list[dict] | None = None,
    ):
        self.repos: list[CodeRepoConfig] = []
        for r in repos or []:
            if isinstance(r, CodeRepoConfig):
                self.repos.append(r)
            else:
                self.repos.append(CodeRepoConfig(
                    path=r.get("path", ""),
                    include=list(r.get("include") or []),
                    exclude=list(r.get("exclude") or []),
                ))
        self.graph_db_path = graph_db_path or ""
        self.repo_root = repo_root or (self.repos[0].path if self.repos else "")
        self.graph_sources = list(graph_sources or [])
        if not self.graph_sources and self.graph_db_path:
            self.graph_sources = [{
                "repo_id": "default",
                "db_path": self.graph_db_path,
                "repo_root": self.repo_root,
            }]
        self._indexed_files: list[str] | None = None
        self._graph_registry = None

    @property
    def graph_enabled(self) -> bool:
        return bool(self.graph_sources) and any(
            os.path.isfile(s.get("db_path", "")) for s in self.graph_sources
        )

    @property
    def file_enabled(self) -> bool:
        return bool(self.repos) and any(
            os.path.isdir(os.path.abspath(r.path)) for r in self.repos
        )

    @property
    def enabled(self) -> bool:
        return self.file_enabled or self.graph_enabled

    @property
    def definitions(self) -> list[dict]:
        if not self.enabled:
            return []
        defs: list[dict] = []
        if self.file_enabled:
            defs.extend(file_tool_definitions())
        if self.graph_enabled:
            from code_to_skill.codegraph_mcp.tool_registry import skillopt_tool_definitions
            defs.extend(skillopt_tool_definitions())
        return defs

    def _graph_registry_instance(self):
        if self._graph_registry is None:
            from code_to_skill.code_graph.registry import GraphRegistry
            self._graph_registry = GraphRegistry.from_sources(self.graph_sources)
        return self._graph_registry

    def execute(self, tool_call: dict) -> str:
        fn = tool_call.get("function", {})
        name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            return json.dumps({"error": "invalid JSON arguments"}, ensure_ascii=False)

        if resolve_file_tool(name):
            if not self.file_enabled:
                return json.dumps({"error": "file tools not available"}, ensure_ascii=False)
            result = self._execute_file_tool(name, args)
            return json.dumps(result, ensure_ascii=False)

        if resolve_tool(name):
            if not self.graph_enabled:
                return json.dumps({"error": "graph index not available"}, ensure_ascii=False)
            result = execute_graph_tool(self._graph_registry_instance(), name, args)
            return json.dumps(result, ensure_ascii=False)

        return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)

    def _execute_file_tool(self, name: str, args: dict[str, Any]) -> Any:
        if name == "search_code":
            return self._search_code(args.get("query", ""), int(args.get("max_results", 10)))
        if name == "read_code_file":
            return self._read_code_file(
                args.get("path", ""),
                int(args.get("start_line", 1)),
                args.get("end_line"),
            )
        if name == "list_code_files":
            return self._list_code_files(
                args.get("pattern", "**/*.java"),
                int(args.get("max_results", 20)),
            )
        return {"error": f"unknown file tool: {name}"}

    def _resolve_allowed_file(self, rel_path: str) -> Path | None:
        rel = rel_path.strip().lstrip("/")
        if not rel or ".." in Path(rel).parts:
            return None
        for repo in self.repos:
            root = Path(repo.path).resolve()
            if not root.is_dir():
                continue
            candidate = (root / rel).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if not candidate.is_file():
                continue
            rel_from_root = str(candidate.relative_to(root)).replace(os.sep, "/")
            if self._path_allowed(rel_from_root, repo):
                return candidate
        basename = Path(rel).name
        if basename:
            for indexed in self._iter_indexed_files():
                if indexed.endswith("/" + basename) or indexed == basename:
                    resolved = self._resolve_allowed_file(indexed)
                    if resolved:
                        return resolved
        return None

    def _path_allowed(self, rel_path: str, repo: CodeRepoConfig) -> bool:
        norm = rel_path.replace(os.sep, "/")
        for pat in repo.exclude:
            if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(norm, pat.lstrip("**/")):
                return False
        if not repo.include:
            return norm.endswith((".java", ".kt", ".py", ".md", ".xml", ".yaml", ".yml"))
        for pat in repo.include:
            p = pat.rstrip("/")
            if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(norm, p + "/**") or norm.startswith(p + "/"):
                return True
        return False

    def _iter_indexed_files(self) -> list[str]:
        if self._indexed_files is not None:
            return self._indexed_files
        files: list[str] = []
        for repo in self.repos:
            root = Path(repo.path).resolve()
            if not root.is_dir():
                continue
            for dirpath, _, filenames in os.walk(root):
                if any(x in dirpath for x in ("/target/", "/.git/", "/node_modules/", "/build/")):
                    continue
                for fname in filenames:
                    if not fname.endswith((".java", ".kt", ".py", ".md", ".xml")):
                        continue
                    full = Path(dirpath) / fname
                    rel = str(full.relative_to(root)).replace(os.sep, "/")
                    if self._path_allowed(rel, repo):
                        files.append(rel)
        self._indexed_files = sorted(files)
        logger.info("[CodeGraph] indexed %d readable files across %d repos", len(files), len(self.repos))
        return self._indexed_files

    def _search_code(self, query: str, max_results: int) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"error": "query is required"}
        max_results = min(max(max_results, 1), _MAX_SEARCH_RESULTS)
        hits: list[dict[str, Any]] = []

        if self.graph_enabled:
            for row in self._graph_registry_instance().search(query, limit=max_results):
                hits.append({
                    "path": row.get("file_path"),
                    "match": "symbol",
                    "line": row.get("start_line"),
                    "snippet": row.get("name"),
                    "node_id": row.get("id"),
                    "kind": row.get("kind"),
                })

        q_lower = query.lower()
        for rel in self._iter_indexed_files():
            if q_lower in rel.lower():
                hits.append({"path": rel, "match": "filename", "snippet": ""})
                if len(hits) >= max_results:
                    break

        if len(hits) < max_results:
            for repo in self.repos:
                root = Path(repo.path).resolve()
                if not root.is_dir():
                    continue
                for rel in self._iter_indexed_files():
                    if any(h["path"] == rel for h in hits):
                        continue
                    full = root / rel
                    try:
                        text = full.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if q_lower not in text.lower():
                        continue
                    line_no = next(
                        (i + 1 for i, line in enumerate(text.splitlines()) if q_lower in line.lower()),
                        0,
                    )
                    snippet = ""
                    if line_no:
                        lines = text.splitlines()
                        snippet = lines[line_no - 1].strip()[:200]
                    hits.append({"path": rel, "match": "content", "line": line_no, "snippet": snippet})
                    if len(hits) >= max_results:
                        break
                if len(hits) >= max_results:
                    break

        return {"query": query, "results": hits}

    def _read_code_file(self, rel_path: str, start_line: int, end_line: int | None) -> dict[str, Any]:
        resolved = self._resolve_allowed_file(rel_path)
        if not resolved:
            return {"error": f"file not found or not allowed: {rel_path}"}
        try:
            raw = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"error": str(e)}
        if len(raw.encode("utf-8")) > _MAX_FILE_BYTES:
            raw = raw[:_MAX_FILE_BYTES]
        lines = raw.splitlines()
        start = max(1, start_line)
        end = end_line if end_line is not None else start + _MAX_READ_LINES - 1
        end = min(max(start, end), len(lines))
        chunk = lines[start - 1:end]
        return {
            "path": rel_path,
            "start_line": start,
            "end_line": end,
            "total_lines": len(lines),
            "content": "\n".join(chunk),
        }

    def _list_code_files(self, pattern: str, max_results: int) -> dict[str, Any]:
        max_results = min(max(max_results, 1), 30)
        matches = [p for p in self._iter_indexed_files() if fnmatch.fnmatch(p, pattern)][:max_results]
        return {"pattern": pattern, "files": matches, "count": len(matches)}


def build_code_tools_handler(
    code_repos: list[CodeRepoConfig] | list[dict] | None = None,
    *,
    enable_code_tools: bool = True,
    graph_db_path: str = "",
    repo_root: str = "",
    graph_sources: list[dict] | None = None,
) -> CodeToolsHandler:
    """Build the pure code tools handler used by CLI, MCP, and SkillOpt."""
    return CodeToolsHandler(
        code_repos if enable_code_tools else None,
        graph_db_path=graph_db_path,
        repo_root=repo_root,
        graph_sources=graph_sources,
    )
