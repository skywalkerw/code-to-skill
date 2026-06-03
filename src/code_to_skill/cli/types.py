"""CLI 核心数据结构。

定义 run_manifest、run_state、events、approvals 的 pydantic model。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    workspace: str = ""
    commands: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    operator: str = "local-user"


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
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


# ── Events ──────────────────────────────────────────────────

class ModuleEvent(BaseModel):
    """单条事件。"""
    schema_version: str = "1.0"
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
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
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
