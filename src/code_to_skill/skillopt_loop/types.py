"""模块 4 核心数据结构。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── Benchmark Item ──────────────────────────────────────────

class BenchmarkItem(BaseModel):
    """单条评测任务。"""
    id: str
    question: str = ""
    task_type: str = ""
    context_refs: list[str] = Field(default_factory=list)
    context_mode: Literal["inline", "agent_read", "none"] = "inline"
    expected_checks: list[str] = Field(default_factory=list)
    scorer: str = "deterministic"


# ── Rollout ──────────────────────────────────────────────────

class RolloutResult(BaseModel):
    """单条任务 rollout 结果。"""
    id: str
    hard: int = 0
    soft: float = 0.0
    fail_reason: str = ""
    task_type: str = ""
    predicted_answer: str = ""
    target_system_prompt: str = ""
    target_user_prompt: str = ""


# ── Patch ────────────────────────────────────────────────────

class EditOp(BaseModel):
    op: Literal["append", "insert_after", "replace", "delete"]
    content: str = ""
    target: str = ""
    source_type: Literal["failure", "success"] = "failure"


class RawPatch(BaseModel):
    """Reflect 阶段产出的单条 patch。"""
    source_type: Literal["failure", "success"]
    batch_size: int = 0
    failure_summary: list[dict] = Field(default_factory=list)
    reasoning: str = ""
    edits: list[EditOp] = Field(default_factory=list)


class MergedPatch(BaseModel):
    """合并后的 patch。"""
    edits: list[EditOp] = Field(default_factory=list)
    reasoning: str = ""


class RankedEdit(BaseModel):
    """排序后的单条编辑。"""
    edit: EditOp
    rank: int = 0
    support_count: int = 0
    score: float = 0.0


# ── Candidate Skill ──────────────────────────────────────────

class CandidateSkill(BaseModel):
    """候选 Skill。"""
    content: str
    hash: str = ""
    version: str = "v0000"
    source_step: int = 0


# ── Step Record ──────────────────────────────────────────────

class StepRecord(BaseModel):
    """单步完整记录。"""
    step: int = 0
    rollout_score: float = 0.0
    selection_score: float = 0.0
    gate_action: Literal["accept_new_best", "accept", "reject", "skip"] = "skip"
    candidate_hash: str = ""
    edit_count: int = 0
    edit_budget: int = 3


# ── History Entry ───────────────────────────────────────────

class HistoryEntry(BaseModel):
    step: int
    hard_score: float = 0.0
    soft_score: float = 0.0
    gate_score: float = 0.0
    action: str = ""
    current_best_step: int = 0
    current_best_score: float = 0.0
