"""CLI 核心数据结构。

定义 run_manifest、run_state、events、approvals 的 pydantic model。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from code_to_skill.time_utils import local_timestamp


class ModuleName(str, Enum):
    code_graph = "code_graph_module_tree"
    doc_normalizer = "document_normalization"
    atom_extractor = "skillatom_extraction"
    skillopt = "skillopt_loop"
    model_check = "model_check"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


# ── Run Manifest ────────────────────────────────────────────

class RunManifest(BaseModel):
    """记录一次运行的元数据。"""
    schema_version: str = "1.0"
    run_id: str
    domain: str = ""
    created_at: str = Field(default_factory=local_timestamp)
    workspace: str = ""
    commands: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    operator: str = "local-user"


class PipelinePhaseRecord(BaseModel):
    """单次流水线阶段记录。"""
    phase: str  # m1_code_graph | m2_docs | m3_atoms | m4_skillopt
    status: str = "completed"  # completed | skipped | failed
    skip_reason: str = ""
    duration_sec: float = 0.0
    artifacts: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)


class PipelineRunManifest(BaseModel):
    """``run all`` 流水线 manifest（skip 原因、耗时、产物路径）。"""
    schema_version: str = "1.0"
    run_id: str
    domain: str = ""
    output_root: str = ""
    created_at: str = Field(default_factory=local_timestamp)
    completed_at: str = ""
    status: str = "running"
    duration_sec: float = 0.0
    flags: dict[str, Any] = Field(default_factory=dict)
    phases: list[PipelinePhaseRecord] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    effective_settings: dict[str, Any] = Field(default_factory=dict)


# ── Run State（断点恢复）────────────────────────────────────

class StepInternal(BaseModel):
    """Step 内的检查点状态（模块 4 用）。"""
    step: int = 0
    phase: str = ""  # rollout / reflect / aggregate / select / update / evaluate
    rollout_completed: int = 0
    rollout_total: int = 0
    last_minibatch_completed: int = 0
    current_batch_file: str = ""


class RunState(BaseModel):
    """运行状态，用于断点恢复。"""
    schema_version: str = "1.0"
    run_id: str
    status: RunStatus = RunStatus.pending
    current_module: str = ""
    completed_modules: list[str] = Field(default_factory=list)
    failed_modules: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    step_internal: StepInternal | None = None
    updated_at: str = Field(default_factory=local_timestamp)


# ── Events ──────────────────────────────────────────────────

class ModuleEvent(BaseModel):
    """单条事件。"""
    schema_version: str = "1.0"
    ts: str = Field(default_factory=local_timestamp)
    level: Literal["info", "warn", "error"] = "info"
    module: str = ""
    event: str = ""
    message: str = ""
    artifact: str = ""


# ── Approval ─────────────────────────────────────────────────

class ApprovalRecord(BaseModel):
    """审批记录。"""
    schema_version: str = "1.0"
    approval_id: str
    requested_action: str
    module: str = ""
    decision: Literal["approved", "denied", "pending"] = "pending"
    scope: str = ""
    ts: str = Field(default_factory=local_timestamp)
