"""流水线 run_manifest 记录（Phase 4）。"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from code_to_skill.time_utils import local_timestamp

from .types import PipelinePhaseRecord, PipelineRunManifest


class PipelineRunRecorder:
    """记录 M1–M4 各阶段 skip/耗时/产物路径。"""

    def __init__(
        self,
        run_id: str,
        output_root: str,
        *,
        domain: str = "",
        flags: dict[str, Any] | None = None,
        effective_settings: dict[str, Any] | None = None,
    ):
        self.output_root = output_root
        self._started = time.monotonic()
        self._phase_started: dict[str, float] = {}
        self.manifest = PipelineRunManifest(
            run_id=run_id,
            domain=domain,
            output_root=output_root,
            flags=flags or {},
            effective_settings=effective_settings or {},
        )

    def skip_phase(self, phase: str, reason: str, *, artifacts: dict[str, str] | None = None) -> None:
        self.manifest.phases.append(PipelinePhaseRecord(
            phase=phase,
            status="skipped",
            skip_reason=reason,
            artifacts=artifacts or {},
        ))

    def start_phase(self, phase: str) -> None:
        self._phase_started[phase] = time.monotonic()

    def end_phase(
        self,
        phase: str,
        *,
        status: str = "completed",
        artifacts: dict[str, str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        started = self._phase_started.pop(phase, self._started)
        duration = round(time.monotonic() - started, 3)
        self.manifest.phases.append(PipelinePhaseRecord(
            phase=phase,
            status=status,
            duration_sec=duration,
            artifacts=artifacts or {},
            metrics=metrics or {},
        ))

    def set_summary(self, **fields: Any) -> None:
        self.manifest.summary.update({k: v for k, v in fields.items() if v is not None})

    def finalize(self, status: str = "completed") -> PipelineRunManifest:
        self.manifest.status = status
        self.manifest.completed_at = local_timestamp()
        self.manifest.duration_sec = round(time.monotonic() - self._started, 3)
        return self.manifest

    def write(self) -> str:
        path = os.path.join(self.output_root, "run_manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.manifest.model_dump_json(indent=2))
        return path
