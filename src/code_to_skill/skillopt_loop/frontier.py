"""M4 self_evolution — Frontier Pool 多候选 Skill 前沿集合。"""
from __future__ import annotations

import json
import os
from typing import Any

from . import compute_semantic_hash


class FrontierPool:
    """固定容量的 selection 分数前沿 Skill 池。"""

    def __init__(self, output_dir: str, max_size: int = 3):
        self.output_dir = os.path.join(output_dir, "frontier")
        self.max_size = max(1, max_size)
        self.manifest_path = os.path.join(self.output_dir, "frontier.json")
        self.entries: list[dict[str, Any]] = []
        os.makedirs(self.output_dir, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self.manifest_path):
            return
        with open(self.manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        self.entries = list(data.get("entries") or [])

    def _save(self) -> None:
        payload = {
            "max_size": self.max_size,
            "entries": self.entries,
            "count": len(self.entries),
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def try_add(self, skill: str, score: float, step: int) -> bool:
        """尝试将候选 Skill 加入前沿；满员时替换最低分条目。"""
        if not skill.strip():
            return False
        skill_hash = compute_semantic_hash(skill)
        for e in self.entries:
            if e.get("skill_hash") == skill_hash:
                if score > float(e.get("score", 0)):
                    e["score"] = round(score, 4)
                    e["step"] = step
                    self._write_snapshot(e, skill)
                    self._save()
                return False

        skill_path = os.path.join(self.output_dir, f"skill_v{step:04d}.md")
        entry = {
            "step": step,
            "score": round(score, 4),
            "skill_hash": skill_hash,
            "skill_path": skill_path,
            "chars": len(skill),
        }
        if len(self.entries) < self.max_size:
            self.entries.append(entry)
            self._write_snapshot(entry, skill)
            self._save()
            return True

        worst = min(self.entries, key=lambda e: float(e.get("score", 0)))
        if score <= float(worst.get("score", 0)):
            return False
        old_path = worst.get("skill_path", "")
        if old_path and os.path.isfile(old_path) and old_path != skill_path:
            try:
                os.remove(old_path)
            except OSError:
                pass
        self.entries.remove(worst)
        self.entries.append(entry)
        self._write_snapshot(entry, skill)
        self._save()
        return True

    def _write_snapshot(self, entry: dict[str, Any], skill: str) -> None:
        path = entry.get("skill_path", "")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(skill)

    def load_skill(self, entry: dict[str, Any]) -> str:
        path = entry.get("skill_path", "")
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        return ""

    def select_parent(
        self,
        current_skill: str,
        best_score: float,
        *,
        score_tolerance: float = 0.05,
    ) -> tuple[str, str]:
        """父代选择：在分数接近 best 的前沿候选中选与 current 不同的 Skill。"""
        if not self.entries:
            return current_skill, "current"
        current_hash = compute_semantic_hash(current_skill)
        candidates = sorted(self.entries, key=lambda e: -float(e.get("score", 0)))
        for entry in candidates:
            if entry.get("skill_hash") == current_hash:
                continue
            if float(entry.get("score", 0)) < best_score - score_tolerance:
                continue
            content = self.load_skill(entry)
            if content.strip():
                return content, f"frontier:step_{entry.get('step', 0):04d}"
        return current_skill, "current"

    def summary(self) -> dict[str, Any]:
        return {
            "max_size": self.max_size,
            "count": len(self.entries),
            "entries": [
                {
                    "step": e.get("step"),
                    "score": e.get("score"),
                    "skill_hash": (e.get("skill_hash") or "")[:8],
                    "chars": e.get("chars", 0),
                }
                for e in sorted(self.entries, key=lambda x: -float(x.get("score", 0)))
            ],
        }
