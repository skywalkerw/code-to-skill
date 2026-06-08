"""MCP 用 CodeToolsHandler 单例（文件读取 + 图谱查询）。"""
from __future__ import annotations

import json
import os
import threading

_lock = threading.Lock()
_handler = None


def _repo_include_exclude() -> tuple[list[str], list[str]]:
    include_raw = os.environ.get("CODEGRAPH_INCLUDE", "**/*")
    exclude_raw = os.environ.get("CODEGRAPH_EXCLUDE", "")
    include = [p.strip() for p in include_raw.split(",") if p.strip()] or ["**/*"]
    exclude = [p.strip() for p in exclude_raw.split(",") if p.strip()]
    return include, exclude


def get_code_tools_handler():
    """返回缓存的 CodeToolsHandler（依赖 CODEGRAPH_REPO_ROOT / CODEGRAPH_DB_PATH）。"""
    global _handler
    from code_to_skill.codegraph_mcp.handler import build_code_tools_handler

    with _lock:
        if _handler is not None:
            return _handler

        repo_root = os.environ.get("CODEGRAPH_REPO_ROOT", "")
        db_path = os.environ.get("CODEGRAPH_DB_PATH", "")
        graph_sources = None
        raw_dbs = os.environ.get("CODEGRAPH_DBS", "")
        if raw_dbs:
            try:
                graph_sources = json.loads(raw_dbs)
            except json.JSONDecodeError:
                graph_sources = None

        repos = None
        if repo_root and os.path.isdir(repo_root):
            include, exclude = _repo_include_exclude()
            repos = [{"path": repo_root, "include": include, "exclude": exclude}]

        _handler = build_code_tools_handler(
            repos,
            enable_code_tools=bool(repos),
            graph_db_path=db_path,
            repo_root=repo_root,
            graph_sources=graph_sources,
        )
        return _handler


def invalidate_handler() -> None:
    global _handler
    with _lock:
        _handler = None
