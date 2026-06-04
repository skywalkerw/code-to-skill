"""SQLite 持久化层。

支持 CodeGraph 的存储、查询和增量更新。
"""
from __future__ import annotations

import os
import sqlite3
import hashlib
from pathlib import Path

from .types import CodeGraph, GraphNode, GraphEdge, NodeKind, EdgeKind, FileEntry


class GraphDB:
    """SQLite 持久化的代码图谱数据库。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else ".", exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._create_tables()
        return self._conn

    def _create_tables(self):
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                language TEXT,
                kind TEXT,
                size_bytes INTEGER,
                source_hash TEXT,
                parsed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                kind TEXT,
                name TEXT,
                file_path TEXT,
                start_line INTEGER,
                end_line INTEGER,
                language TEXT,
                source_hash TEXT,
                FOREIGN KEY (file_path) REFERENCES files(path)
            );

            CREATE TABLE IF NOT EXISTS edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                target TEXT,
                kind TEXT,
                confidence REAL,
                provenance TEXT,
                FOREIGN KEY (source) REFERENCES nodes(id),
                FOREIGN KEY (target) REFERENCES nodes(id)
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
            CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
            CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        """)

    # ── 写入 ────────────────────────────────────────────────

    def save_graph(self, graph: CodeGraph):
        """保存完整的 CodeGraph 到数据库。"""
        conn = self._connect()
        with conn:
            # 文件
            file_nodes = {n.id for n in graph.nodes if n.kind == NodeKind.file}
            seen_files: set[str] = set()
            for node in graph.nodes:
                fpath = node.file_path
                if fpath and fpath not in seen_files:
                    seen_files.add(fpath)
                    conn.execute(
                        "INSERT OR REPLACE INTO files(path, language, kind, size_bytes, source_hash, parsed_at) "
                        "VALUES (?, ?, 'source', 0, ?, datetime('now'))",
                        (fpath, node.language, node.source_hash)
                    )

            # 节点
            for node in graph.nodes:
                conn.execute(
                    "INSERT OR REPLACE INTO nodes(id, kind, name, file_path, start_line, end_line, language, source_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (node.id, node.kind.value, node.name, node.file_path,
                     node.start_line, node.end_line, node.language, node.source_hash)
                )

            # 边
            for edge in graph.edges:
                conn.execute(
                    "INSERT OR REPLACE INTO edges(source, target, kind, confidence, provenance) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (edge.source, edge.target, edge.kind.value, edge.confidence, edge.provenance)
                )

        self._conn and self._conn.commit()

    # ── 读取 ────────────────────────────────────────────────

    def load_graph(self) -> CodeGraph:
        """从数据库加载 CodeGraph。"""
        conn = self._connect()
        graph = CodeGraph()

        for row in conn.execute("SELECT id, kind, name, file_path, start_line, end_line, language, source_hash FROM nodes"):
            graph.nodes.append(GraphNode(
                id=row[0], kind=NodeKind(row[1]), name=row[2], file_path=row[3] or "",
                start_line=row[4] or 0, end_line=row[5] or 0, language=row[6] or "", source_hash=row[7] or "",
            ))

        for row in conn.execute("SELECT source, target, kind, confidence, provenance FROM edges"):
            graph.edges.append(GraphEdge(
                source=row[0], target=row[1], kind=EdgeKind(row[2]),
                confidence=row[3] or 0.9, provenance=row[4] or "static",
            ))

        return graph

    # ── 增量更新 ────────────────────────────────────────────

    def get_changed_files(self, file_hashes: dict[str, str]) -> list[str]:
        """比较文件 hash，返回变更/新增的文件列表。

        Args:
            file_hashes: {file_path: sha256_hash}

        Returns:
            需要重新解析的文件路径列表
        """
        conn = self._connect()
        changed: list[str] = []

        for fpath, sha in file_hashes.items():
            row = conn.execute("SELECT source_hash FROM files WHERE path = ?", (fpath,)).fetchone()
            if row is None or row[0] != sha:
                changed.append(fpath)

        return changed

    def remove_nodes_for_files(self, file_paths: list[str]):
        """删除指定文件的所有节点和边。"""
        conn = self._connect()
        with conn:
            for fpath in file_paths:
                conn.execute("DELETE FROM edges WHERE source IN (SELECT id FROM nodes WHERE file_path = ?)", (fpath,))
                conn.execute("DELETE FROM edges WHERE target IN (SELECT id FROM nodes WHERE file_path = ?)", (fpath,))
                conn.execute("DELETE FROM nodes WHERE file_path = ?", (fpath,))
                conn.execute("DELETE FROM files WHERE path = ?", (fpath,))

    # ── 查询 ────────────────────────────────────────────────

    def get_node_count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    def get_edge_count(self) -> int:
        conn = self._connect()
        return conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    def is_fresh(self, repo_root: str) -> bool:
        """检查数据库是否包含指定仓库的数据。"""
        conn = self._connect()
        # 检查是否有以 repo 路径开头的文件
        row = conn.execute("SELECT COUNT(*) FROM files WHERE path LIKE ?", (f"{repo_root}%",)).fetchone()
        return (row[0] if row else 0) > 0

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
