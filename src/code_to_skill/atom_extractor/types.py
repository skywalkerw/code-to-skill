"""模块 3 核心数据结构：SkillAtom, BenchmarkSeed, EvidenceRef 等。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── SourceRef ────────────────────────────────────────────────

class SourceRef(BaseModel):
    type: str  # "doc" or "code"
    id: str
    authority: str = ""
    edge_path: list[str] = Field(default_factory=list)


class EvidenceSummary(BaseModel):
    text: str = ""
    alignment_score: float = 0.0


# ── SkillAtom ────────────────────────────────────────────────

class SkillAtom(BaseModel):
    """最小可复用技能单元。"""
    schema_version: str = "1.0"
    atom_id: str
    kind: Literal["concept", "procedure", "tool_policy", "constraint",
                  "failure_mode", "output_format", "coding_convention", "validation"]
    claim: str
    applicability: dict = Field(default_factory=dict)  # domain, task_types, trigger_terms, code_scope
    action: str = ""
    negative_rule: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    evidence_summary: str = ""
    checks: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    risk: Literal["low", "medium", "high", "needs_review"] = "medium"
    status: Literal["candidate", "accepted", "rejected", "needs_review"] = "candidate"


# ── RawAtom（LLM 原始输出）──────────────────────────────────

class RawAtom(BaseModel):
    """从 LLM 抽取的原始候选 atom。"""
    raw_id: str
    atom: SkillAtom
    extractor_confidence: float = 0.5
    extraction_stage: str = ""


# ── BenchmarkSeed ────────────────────────────────────────────

class BenchmarkSeed(BaseModel):
    """评测种子（对齐 benchmark items.json 子集）。"""
    id: str
    question: str = ""
    source_atom_ids: list[str] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    expected_checks: list[str] = Field(default_factory=list)
    risk: str = "medium"


# ── EvidenceIndex ────────────────────────────────────────────

class EvidenceIndexEntry(BaseModel):
    evidence_id: str
    type: str  # code_node / doc_chunk / trace
    source_ref: str = ""
    atom_ids: list[str] = Field(default_factory=list)
    confidence_contribution: float = 0.0
