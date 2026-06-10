"""Design 08 — rejected edit buffer 持久化。"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .types import EditOp


def content_hash(content: str) -> str:
    norm = " ".join((content or "").split()).strip()
    digest = hashlib.sha256(norm.encode()).hexdigest()
    return f"sha256:{digest}"


class RejectedEditBuffer:
    """落盘 rejected_edit_buffer.jsonl 并供 reflect 注入。"""

    def __init__(self, output_dir: str):
        self.path = os.path.join(output_dir, "rejected_edit_buffer.jsonl")
        os.makedirs(output_dir, exist_ok=True)

    def append(
        self,
        *,
        edit: EditOp,
        step: int,
        reason: str,
        before_score: float,
        after_score: float,
        proposal_id: str = "",
        affected_rule_ids: list[str] | None = None,
        missed_checks_after: list[str] | None = None,
    ) -> dict[str, Any]:
        record = {
            "edit_id": f"edit-v{step:04d}-{content_hash(edit.content or '')[:8]}",
            "proposal_id": proposal_id,
            "op": edit.op,
            "content": (edit.content or "")[:500],
            "content_hash": content_hash(edit.content or ""),
            "reason": reason,
            "before_score": round(before_score, 4),
            "after_score": round(after_score, 4),
            "affected_rule_ids": list(affected_rule_ids or []),
            "missed_checks_after": list(missed_checks_after or []),
            "created_at_step": step,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def load_all(self) -> list[dict]:
        if not os.path.isfile(self.path):
            return []
        rows: list[dict] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def load_recent(self, limit: int = 20) -> list[dict]:
        return self.load_all()[-limit:]
