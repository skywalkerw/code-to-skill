"""模块 1 核心数据结构。

定义 CodeGraph、ModuleTree、LeafContext、FileInventory 等类型。
所有类型基于 pydantic v2，含 schema_version。
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ── Node / Edge 枚举 ────────────────────────────────────────

class NodeKind(str, Enum):
    file = "file"
    module = "module"
    class_ = "class"
    interface = "interface"
    function = "function"
    method = "method"
    route = "route"
    job = "job"
    config = "config"
    test = "test"
    script = "script"


class EdgeKind(str, Enum):
    contains = "contains"
    calls = "calls"
    imports = "imports"
    extends = "extends"
    implements = "implements"
    references = "references"
    reads_config = "reads_config"
    writes_state = "writes_state"
    tested_by = "tested_by"
    entry_to = "entry_to"


# ── CodeGraph ───────────────────────────────────────────────

class GraphNode(BaseModel):
    """图谱节点。"""
    id: str  # e.g. "src/refund/client.py::retry_refund"
    kind: NodeKind
    name: str
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    language: str = ""
    source_hash: str = ""
    qualified_name: str = ""  # e.g. com.example.OrderService.placeOrder
    signature: str = ""
    docstring: str = ""
    metadata: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """图谱边。"""
    source: str  # node id
    target: str  # node id
    kind: EdgeKind
    confidence: float = 0.9
    provenance: Literal["static", "heuristic", "llm"] = "static"


class CodeGraph(BaseModel):
    """代码图谱。"""
    schema_version: str = "1.0"
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


# ── FileInventory ───────────────────────────────────────────

class FileEntry(BaseModel):
    """单个文件记录。"""
    path: str
    language: str = ""
    kind: str = "source"  # source / test / config / doc / generated / binary
    size_bytes: int = 0
    source_hash: str = ""


class FileInventory(BaseModel):
    """文件清单。"""
    schema_version: str = "1.0"
    files: list[FileEntry] = Field(default_factory=list)


class CodeGraphManifest(BaseModel):
    """代码图谱快照元数据（对齐设计文档 01 manifest.json）。"""
    schema_version: str = "1.0"
    repo_id: str = ""
    repo_root: str = ""
    snapshot_ref: str = "HEAD"
    analyzed_at: str = ""
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    tools: dict = Field(default_factory=lambda: {"codegraph": "code_to_skill.code_graph"})
    stats: dict = Field(default_factory=dict)


# ── Entrypoint ──────────────────────────────────────────────

class Entrypoint(BaseModel):
    """入口点。"""
    id: str
    kind: str  # rest / rpc / cli / job / message / spi / test
    handler_node_id: str = ""
    path: str = ""
    protocol: str = ""
    downstream: list[str] = Field(default_factory=list)
    confidence: float = 0.9


# ── ModuleTree ──────────────────────────────────────────────

class ModuleTreeNode(BaseModel):
    """模块树节点（递归结构）。"""
    name: str
    path: str = ""
    reason: str = ""
    components: list[str] = Field(default_factory=list)
    children: dict[str, "ModuleTreeNode"] = Field(default_factory=dict)


class ModuleTree(BaseModel):
    """模块树。"""
    schema_version: str = "1.0"
    root: dict[str, ModuleTreeNode] = Field(default_factory=dict)


# ── LeafContext ─────────────────────────────────────────────

class LeafContext(BaseModel):
    """叶子模块上下文包。"""
    schema_version: str = "1.0"
    leaf_id: str
    module_path: list[str] = Field(default_factory=list)
    component_ids: list[str] = Field(default_factory=list)
    entrypoints: list[str] = Field(default_factory=list)
    important_edges: list[dict] = Field(default_factory=list)
    source_snippets: list[dict] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    token_estimate: int = 0
    parent_leaf: str | None = None
    split_reason: str = ""
    cross_ref_ids: list[str] = Field(default_factory=list)


# ── Diagnostics ─────────────────────────────────────────────

class ParseError(BaseModel):
    """解析错误记录。"""
    file_path: str
    error: str
    line: int = 0
    language: str = ""


class UnresolvedEdge(BaseModel):
    """未解析边。"""
    source: str
    attempted_target: str
    kind: str
    reason: str = ""
