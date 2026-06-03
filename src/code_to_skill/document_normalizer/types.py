"""模块 2 核心数据结构。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── DocumentManifest ────────────────────────────────────────

class DocumentManifest(BaseModel):
    """文档快照。"""
    schema_version: str = "1.0"
    source_id: str
    source_uri: str
    source_type: str  # markdown / pdf / html / docx / text
    source_provider: str = "local_file"
    source_version: str = ""
    source_subtype: str = ""
    sha256: str = ""
    authority_level: str = "team_runbook"
    language: str = ""
    normalized_at: str = ""


# ── DocumentIndex ───────────────────────────────────────────

class SectionInfo(BaseModel):
    section_id: str
    heading: str
    level: int = 1
    parent_id: str = "sec-root"
    chunk_ids: list[str] = Field(default_factory=list)
    page_range: list[int] = Field(default_factory=list)


class DocumentIndex(BaseModel):
    """文档结构树。"""
    schema_version: str = "1.0"
    title: str = ""
    sections: list[SectionInfo] = Field(default_factory=list)


# ── DocumentChunk ───────────────────────────────────────────

class DocumentChunk(BaseModel):
    """规范化文档块。"""
    schema_version: str = "1.0"
    chunk_id: str
    source_id: str
    section_id: str = ""
    heading_path: list[str] = Field(default_factory=list)
    content_type: str = "concept"  # concept/procedure/faq/error_code/api_contract/policy/template/example
    text: str
    page: int = 0
    char_start: int = 0
    char_end: int = 0
    authority_level: str = "team_runbook"
    validity: str = "active"
    sensitivity: str = "none"
    quality_flags: list[str] = Field(default_factory=list)
    semantic_unit: str = "section"
    token_estimate: int = 0
    tags: list[str] = Field(default_factory=list)


# ── DocumentTable ───────────────────────────────────────────

class DocumentTable(BaseModel):
    """结构化表格。"""
    schema_version: str = "1.0"
    table_id: str
    caption: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    source_ref: str = ""


# ── DocumentAsset ───────────────────────────────────────────

class DocumentAsset(BaseModel):
    """文档资产（图片、附件等）。"""
    schema_version: str = "1.0"
    asset_id: str
    asset_type: str = "image"
    source_ref: str = ""
    alt_text: str = ""
    ocr_text: str = ""
    file_path: str = ""


# ── ConflictRecord ─────────────────────────────────────────

class ConflictRecord(BaseModel):
    """冲突记录。"""
    conflict_id: str
    claim: str = ""
    sources: list[str] = Field(default_factory=list)
    status: Literal["needs_resolution", "resolved", "deferred"] = "needs_resolution"
    recommended_action: str = ""


# ── RawDocument（KnowledgeSource 返回）────────────────────

class RawDocument(BaseModel):
    """Provider 返回的原始文档内容。"""
    content: bytes = b""
    source_uri: str = ""
    source_type: str = ""
    source_version: str = ""
    metadata: dict = Field(default_factory=dict)
    text: str = ""
