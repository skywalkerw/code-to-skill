"""步骤缓冲模块。存储历史成功/失败编辑，避免 Reflect 重复提出已尝试的编辑。

对齐 external/SkillOpt 中的 buffer 概念。
"""
from __future__ import annotations

from .types import EditOp, StepBuffer


class StepBufferManager:
    """步骤缓冲管理器。

    追踪：
    - 已成功过的 task IDs
    - 已失败过的 task IDs
    - 已拒绝的编辑（避免重复尝试）
    - 已接受的编辑（避免撤消有效改进）
    """

    def __init__(self):
        self._buffers: dict[int, StepBuffer] = {}
        self._global_rejected: set[str] = set()   # 全局已拒绝的内容摘要
        self._global_accepted: set[str] = set()    # 全局已接受的内容摘要

    def record_success(self, step: int, task_id: str):
        buf = self._get_or_create(step)
        if task_id not in buf.success_ids:
            buf.success_ids.append(task_id)

    def record_failure(self, step: int, task_id: str):
        buf = self._get_or_create(step)
        if task_id not in buf.failure_ids:
            buf.failure_ids.append(task_id)

    def record_rejected_edit(self, step: int, edit: EditOp):
        buf = self._get_or_create(step)
        buf.rejected_edits.append(edit)
        self._global_rejected.add(self._edit_fingerprint(edit))

    def record_accepted_edit(self, step: int, edit: EditOp):
        buf = self._get_or_create(step)
        buf.accepted_edits.append(edit)
        self._global_accepted.add(self._edit_fingerprint(edit))

    def is_edit_redundant(self, edit: EditOp) -> bool:
        """检查编辑是否与已拒绝的编辑重复。"""
        return self._edit_fingerprint(edit) in self._global_rejected

    def get_rejected_edits(self, step: int | None = None) -> list[EditOp]:
        """获取已拒绝的编辑（可选指定 step）。"""
        if step is not None:
            return self._buffers.get(step, StepBuffer()).rejected_edits
        all_rejected = []
        for buf in self._buffers.values():
            all_rejected.extend(buf.rejected_edits)
        return all_rejected

    def _get_or_create(self, step: int) -> StepBuffer:
        if step not in self._buffers:
            self._buffers[step] = StepBuffer(step=step)
        return self._buffers[step]

    @staticmethod
    def _edit_fingerprint(edit: EditOp) -> str:
        """生成编辑指纹（前100字符的空白归一化摘要）。"""
        content = (edit.content or "").strip()[:100]
        content = " ".join(content.split())
        return f"{edit.op}:{content}"

    @property
    def stats(self) -> dict:
        return {
            "total_steps": len(self._buffers),
            "global_accepted": len(self._global_accepted),
            "global_rejected": len(self._global_rejected),
        }
