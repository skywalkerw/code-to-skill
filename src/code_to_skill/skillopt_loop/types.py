"""模块 4 核心数据结构。

对齐 external/SkillOpt 的类型设计：Edit/Patch/RolloutResult/CandidateSkill 等。
"""
from __future__ import annotations

from typing import Any, Literal

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
    """单条任务 rollout 结果。对齐 external/SkillOpt RolloutResult。"""
    id: str
    hard: int = 0
    soft: float = 0.0
    n_turns: int = 0
    fail_reason: str = ""
    task_type: str = ""
    task_description: str = ""
    predicted_answer: str = ""
    question: str = ""
    reference_text: str = ""
    target_system_prompt: str = ""
    target_user_prompt: str = ""
    extras: dict[str, Any] = Field(default_factory=dict)


# ── Edit / Patch ─────────────────────────────────────────────

class EditOp(BaseModel):
    """单条编辑操作。对齐 external/SkillOpt Edit。"""
    op: Literal["append", "insert_after", "replace", "delete"]
    content: str = ""
    target: str = ""
    support_count: int | None = None
    source_type: Literal["failure", "success"] | None = None
    merge_level: int | None = None
    related_task_ids: list[str] = Field(default_factory=list)
    related_missed_checks: list[str] = Field(default_factory=list)


class FailureSummaryEntry(BaseModel):
    """单条失败原因汇总。对齐 external/SkillOpt FailureSummaryEntry。"""
    failure_type: str
    count: int = 0
    description: str = ""


class RawPatch(BaseModel):
    """Reflect 阶段产出的单条 patch。"""
    source_type: Literal["failure", "success"]
    batch_size: int = 0
    failure_summary: list[FailureSummaryEntry] = Field(default_factory=list)
    reasoning: str = ""
    edits: list[EditOp] = Field(default_factory=list)


class MergedPatch(BaseModel):
    """合并后的 patch。对齐 external/SkillOpt Patch。"""
    edits: list[EditOp] = Field(default_factory=list)
    reasoning: str = ""
    ranking_details: dict[str, Any] | None = None


class RankedEdit(BaseModel):
    """排序后的单条编辑。"""
    edit: EditOp
    rank: int = 0
    support_count: int = 0
    score: float = 0.0


# ── Candidate Skill ──────────────────────────────────────────

class CandidateSkill(BaseModel):
    """候选 Skill。对齐 external/SkillOpt CandidateSkill。"""
    content: str
    semantic_hash: str = ""
    origin_step: int = 0
    skill_path: str = ""
    version: str = "v0000"


# ── Step Tracking ───────────────────────────────────────────

class StepRecord(BaseModel):
    """单步完整记录。"""
    step: int = 0
    epoch: int = 1
    rollout_hard: float = 0.0
    rollout_soft: float = 0.0
    selection_score: float = 0.0
    best_score: float = 0.0
    gate_action: Literal["accept_new_best", "accept", "reject", "skip"] = "skip"
    candidate_hash: str = ""
    edit_count: int = 0
    edit_budget: int = 3


class StepInternal(BaseModel):
    """Step 内检查点状态。"""
    step: int = 0
    phase: str = ""
    rollout_completed: int = 0
    rollout_total: int = 0
    last_minibatch_completed: int = 0


class StepBuffer(BaseModel):
    """步骤缓冲：存储成功/失败历史供后续 Reflect 避免重复。"""
    step: int = 0
    success_ids: list[str] = Field(default_factory=list)
    failure_ids: list[str] = Field(default_factory=list)
    rejected_edits: list[EditOp] = Field(default_factory=list)
    accepted_edits: list[EditOp] = Field(default_factory=list)


# ── History ──────────────────────────────────────────────────

class HistoryEntry(BaseModel):
    step: int
    hard_score: float = 0.0
    soft_score: float = 0.0
    gate_score: float = 0.0
    action: str = ""
    current_best_step: int = 0
    current_best_score: float = 0.0
