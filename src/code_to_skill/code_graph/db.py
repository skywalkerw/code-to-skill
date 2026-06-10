"""SQLite 持久化层。

支持 CodeGraph 的存储、查询和增量更新。
"""
from __future__ import annotations

import fnmatch
import os
import sqlite3
import hashlib
from pathlib import Path

from .types import CodeGraph, GraphNode, GraphEdge, NodeKind, EdgeKind, UnresolvedEdge


def _match_any_pattern(path: str, patterns: list[str]) -> bool:
    norm = path.replace(os.sep, "/")
    for pat in patterns:
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(norm, pat.lstrip("**/")):
            return True
    return False


def _row_to_hit(row, score: float) -> dict:
    return {
        "id": row[0],
        "kind": row[1],
        "name": row[2],
        "file_path": row[3],
        "start_line": row[4],
        "end_line": row[5],
        "score": score,
    }


class GraphDB:
    """SQLite 持久化的代码图谱数据库。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else ".", exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
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
                qualified_name TEXT,
                signature TEXT,
                docstring TEXT,
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

            CREATE TABLE IF NOT EXISTS unresolved_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_node_id TEXT,
                reference_name TEXT,
                reference_kind TEXT,
                file_path TEXT,
                reason TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                node_id UNINDEXED,
                name,
                file_path,
                qualified_name,
                tokenize='porter'
            );
        """)

    # ── 写入 ────────────────────────────────────────────────

    def save_graph(self, graph: CodeGraph, *, merge: bool = False):
        """保存 CodeGraph 到数据库。merge=True 时仅 upsert，不清空全表。"""
        conn = self._connect()
        with conn:
            if not merge:
                conn.execute("DELETE FROM edges")
                conn.execute("DELETE FROM nodes")
                conn.execute("DELETE FROM files")
                try:
                    conn.execute("DELETE FROM nodes_fts")
                except sqlite3.OperationalError:
                    pass
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
                    "INSERT OR REPLACE INTO nodes("
                    "id, kind, name, file_path, start_line, end_line, language, source_hash, "
                    "qualified_name, signature, docstring"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        node.id, node.kind.value, node.name, node.file_path,
                        node.start_line, node.end_line, node.language, node.source_hash,
                        node.qualified_name, node.signature, node.docstring,
                    ),
                )

            # 边（按 source+target+kind 去重）
            seen_edges: set[tuple[str, str, str]] = set()
            for edge in graph.edges:
                key = (edge.source, edge.target, edge.kind.value)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                conn.execute(
                    "INSERT INTO edges(source, target, kind, confidence, provenance) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (edge.source, edge.target, edge.kind.value, edge.confidence, edge.provenance)
                )

        self._rebuild_fts()
        self._conn and self._conn.commit()

    def _rebuild_fts(self):
        conn = self._connect()
        try:
            conn.execute("DELETE FROM nodes_fts")
        except sqlite3.OperationalError:
            return
        for row in conn.execute(
            "SELECT id, name, file_path, qualified_name, signature, docstring FROM nodes"
        ):
            qn = row[3] or row[1]
            conn.execute(
                "INSERT INTO nodes_fts(node_id, name, file_path, qualified_name) VALUES (?, ?, ?, ?)",
                (row[0], row[1], row[2] or "", qn),
            )

    # ── 读取 ────────────────────────────────────────────────

    def load_graph(self) -> CodeGraph:
        """从数据库加载 CodeGraph。"""
        conn = self._connect()
        graph = CodeGraph()

        for row in conn.execute(
            "SELECT id, kind, name, file_path, start_line, end_line, language, source_hash, "
            "qualified_name, signature, docstring FROM nodes"
        ):
            graph.nodes.append(GraphNode(
                id=row[0], kind=NodeKind(row[1]), name=row[2], file_path=row[3] or "",
                start_line=row[4] or 0, end_line=row[5] or 0, language=row[6] or "", source_hash=row[7] or "",
                qualified_name=row[8] or "", signature=row[9] or "", docstring=row[10] or "",
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

    def is_fresh(self, repo_root: str = "") -> bool:
        """检查数据库是否包含数据。"""
        conn = self._connect()
        row = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
        return (row[0] if row else 0) > 0

    # ── 符号搜索（FTS + LIKE 降级）──────────────────────────

    def search_nodes(
        self,
        query: str,
        limit: int = 20,
        *,
        kinds: list[str] | None = None,
        file_patterns: list[str] | None = None,
    ) -> list[dict]:
        conn = self._connect()
        results: list[dict] = []
        seen: set[str] = set()
        q = query.strip()

        def _append(row, score: float):
            if row[0] in seen:
                return
            if kinds and row[1].lower() not in kinds:
                return
            if file_patterns and not _match_any_pattern(row[3] or "", file_patterns):
                return
            seen.add(row[0])
            hit = _row_to_hit(row, score=score)
            qrow = conn.execute(
                "SELECT qualified_name FROM nodes WHERE id = ?", (row[0],),
            ).fetchone()
            if qrow and qrow[0]:
                hit["qualified_name"] = qrow[0]
            results.append(hit)

        if q:
            try:
                fts_q = " OR ".join(f'"{t}"' for t in q.split() if t)
                rows = conn.execute(
                    """
                    SELECT n.id, n.kind, n.name, n.file_path, n.start_line, n.end_line
                    FROM nodes_fts f
                    JOIN nodes n ON n.id = f.node_id
                    WHERE nodes_fts MATCH ?
                    LIMIT ?
                    """,
                    (fts_q or q, limit * 3),
                ).fetchall()
                for row in rows:
                    _append(row, 1.0)
                    if len(results) >= limit:
                        return results[:limit]
            except sqlite3.OperationalError:
                pass

        if len(results) < limit and q:
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT id, kind, name, file_path, start_line, end_line FROM nodes
                WHERE name LIKE ? OR file_path LIKE ? OR qualified_name LIKE ?
                LIMIT ?
                """,
                (like, like, like, (limit - len(results)) * 3),
            ).fetchall()
            for row in rows:
                _append(row, 0.6)
                if len(results) >= limit:
                    break

        if not q and (kinds or file_patterns):
            sql = "SELECT id, kind, name, file_path, start_line, end_line FROM nodes WHERE 1=1"
            params: list = []
            if kinds:
                sql += f" AND kind IN ({','.join('?' * len(kinds))})"
                params.extend(kinds)
            sql += " LIMIT ?"
            params.append(limit * 3)
            for row in conn.execute(sql, params).fetchall():
                _append(row, 0.4)
                if len(results) >= limit:
                    break

        return results[:limit]

    def get_node(self, node_id: str) -> GraphNode | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT id, kind, name, file_path, start_line, end_line, language, source_hash, "
            "qualified_name, signature, docstring FROM nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            return None
        return GraphNode(
            id=row[0], kind=NodeKind(row[1]), name=row[2], file_path=row[3] or "",
            start_line=row[4] or 0, end_line=row[5] or 0, language=row[6] or "", source_hash=row[7] or "",
            qualified_name=row[8] or "", signature=row[9] or "", docstring=row[10] or "",
        )

    def save_unresolved_refs(self, unresolved: list[UnresolvedEdge]):
        """将未解析引用写入 unresolved_refs 表。"""
        conn = self._connect()
        with conn:
            conn.execute("DELETE FROM unresolved_refs")
            for item in unresolved:
                conn.execute(
                    "INSERT INTO unresolved_refs(from_node_id, reference_name, reference_kind, file_path, reason) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (item.source, item.attempted_target, item.kind, "", item.reason),
                )

    def list_files(self, pattern: str = "**/*", limit: int = 50) -> list[dict]:
        """列出索引中的文件（比扫磁盘快）。"""
        conn = self._connect()
        rows = conn.execute(
            "SELECT path, language, kind, size_bytes FROM files ORDER BY path LIMIT ?",
            (max(limit * 5, limit),),
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            path = row[0] or ""
            if pattern and pattern not in ("*", "**/*") and not _match_any_pattern(path, [pattern]):
                continue
            out.append({
                "path": path,
                "language": row[1] or "",
                "kind": row[2] or "",
                "size_bytes": row[3] or 0,
            })
            if len(out) >= limit:
                break
        return out

    def get_stats(self) -> dict:
        conn = self._connect()
        nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        kinds: dict[str, int] = {}
        for row in conn.execute("SELECT kind, COUNT(*) FROM nodes GROUP BY kind"):
            kinds[row[0]] = row[1]
        return {"nodes": nodes, "edges": edges, "files": files, "kinds": kinds}

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
