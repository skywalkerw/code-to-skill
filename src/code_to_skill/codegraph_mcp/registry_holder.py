"""MCP 用 GraphRegistry 单例 — 支持 db mtime 失效与 daemon 主动刷新。"""
from __future__ import annotations

import json
import os
import threading

_lock = threading.Lock()
_registry = None
_db_mtime: float = 0.0


def _db_mtime_key() -> float:
    """取主 graph.db 的 mtime（多库取最大）。"""
    mtimes: list[float] = []
    raw = os.environ.get("CODEGRAPH_DBS", "")
    if raw:
        try:
            for item in json.loads(raw):
                path = item.get("db_path") or item.get("graph_db_path", "")
                if path and os.path.isfile(path):
                    mtimes.append(os.path.getmtime(path))
        except (json.JSONDecodeError, OSError):
            pass
    else:
        path = os.environ.get("CODEGRAPH_DB_PATH", "")
        if path and os.path.isfile(path):
            try:
                mtimes.append(os.path.getmtime(path))
            except OSError:
                pass
    return max(mtimes) if mtimes else 0.0


def get_registry():
    """返回缓存的 GraphRegistry；db 更新后自动重建。"""
    global _registry, _db_mtime
    from code_to_skill.codegraph_mcp import _build_registry

    mtime = _db_mtime_key()
    with _lock:
        if _registry is None or mtime > _db_mtime:
            _registry = _build_registry()
            _db_mtime = mtime
        return _registry


def invalidate_registry() -> None:
    """daemon / watcher 同步后主动失效缓存。"""
    global _registry, _db_mtime
    with _lock:
        if _registry is not None:
            _registry.invalidate_caches()
        _registry = None
        _db_mtime = 0.0
