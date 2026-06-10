"""Design 08 — rule_id 注入与归因。"""
from __future__ import annotations

import json
import os
import re
from typing import Any

_RULE_COMMENT_RE = re.compile(
    r"<!--\s*rule_id:\s*([^;]+);\s*source:\s*([^>]+)\s*-->",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^(\s*[-*]\s+)", re.MULTILINE)


def make_rule_id(prefix: str = "rule") -> str:
    import uuid
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def inject_rule_ids_into_skill(
    skill: str,
    *,
    proposal_id: str = "",
    rule_prefix: str = "rule",
) -> tuple[str, list[str]]:
    """为尚无 rule_id 的 bullet 行注入 HTML 注释。"""
    rule_ids: list[str] = []
    lines = skill.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        bullet = _BULLET_RE.match(line)
        if bullet and i + 1 < len(lines) and _RULE_COMMENT_RE.search(lines[i + 1]):
            out.append(line)
            i += 1
            continue
        if bullet:
            rid = make_rule_id(rule_prefix)
            rule_ids.append(rid)
            src = proposal_id or "manual"
            out.append(f"<!-- rule_id: {rid}; source: {src} -->")
        out.append(line)
        i += 1
    return "\n".join(out), rule_ids


def parse_rule_ids(skill: str) -> dict[str, str]:
    """rule_id → source proposal。"""
    mapping: dict[str, str] = {}
    for m in _RULE_COMMENT_RE.finditer(skill):
        mapping[m.group(1).strip()] = m.group(2).strip()
    return mapping


def strip_rule_comments(skill: str) -> str:
    return "\n".join(
        ln for ln in skill.splitlines()
        if not _RULE_COMMENT_RE.match(ln.strip())
    )


class RuleAttributionTracker:
    """维护 rule_attribution.json。"""

    def __init__(self, output_dir: str):
        self.path = os.path.join(output_dir, "rule_attribution.json")
        self._data: dict[str, dict[str, Any]] = {}
        if os.path.isfile(self.path):
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._data = raw.get("rules") or raw

    def sync_from_skill(self, skill: str, *, step: int) -> None:
        for rule_id, source in parse_rule_ids(skill).items():
            entry = self._data.setdefault(rule_id, {
                "rule_id": rule_id,
                "source_proposal_ids": [],
                "rule_used_count": 0,
                "rule_helped_checks": [],
                "rule_associated_failures": [],
                "rule_regression_count": 0,
                "last_touched_step": step,
            })
            if source and source not in entry["source_proposal_ids"]:
                entry["source_proposal_ids"].append(source)
            entry["last_touched_step"] = max(entry.get("last_touched_step", 0), step)

    def record_rollout_usage(
        self,
        skill: str,
        rollout_results: list[dict],
        *,
        step: int,
    ) -> None:
        rules = parse_rule_ids(skill)
        if not rules:
            return
        skill_lower = skill.lower()
        for rule_id in rules:
            entry = self._data.setdefault(rule_id, {"rule_id": rule_id})
            # 粗略：rule 文本在 skill 中，计为可用；passed check 记入 helped
            entry["rule_used_count"] = entry.get("rule_used_count", 0) + len(rollout_results)
            for r in rollout_results:
                if r.get("hard") == 1:
                    for c in r.get("passed_checks") or []:
                        helped = entry.setdefault("rule_helped_checks", [])
                        if c not in helped:
                            helped.append(c)
                else:
                    for c in r.get("missed_checks") or []:
                        fails = entry.setdefault("rule_associated_failures", [])
                        if c not in fails:
                            fails.append(c)
            entry["last_touched_step"] = step
        _ = skill_lower  # reserved for finer matching later

    def record_regression(self, rule_ids: list[str]) -> None:
        for rid in rule_ids:
            entry = self._data.setdefault(rid, {"rule_id": rid})
            entry["rule_regression_count"] = entry.get("rule_regression_count", 0) + 1

    def save(self) -> str:
        payload = {"rules": self._data, "rule_count": len(self._data)}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return self.path
