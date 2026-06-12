"""Target 输出卫生检查 — prompt echo / tool residue（设计 08 §11）。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .rollout_helpers import looks_like_tool_call_leak

DEFAULT_ECHO_PATTERNS: list[str] = [
    r"^Task:\s",
    r"Skill reference:",
    r"Code context:",
    r"^Context references:",
    r"Follow the skill document",
    r"tool_calls",
    r"<\s*[｜|].*tool",
]

ECHO_RETRY_HINT = (
    "Your previous answer leaked prompt or tool context. "
    "Output ONLY the final deliverable in markdown. "
    "Do NOT repeat Task:, Skill reference:, Code context:, or context_refs."
)


@dataclass
class OutputHygieneConfig:
    enabled: bool = True
    retry_on_prompt_echo: bool = True
    hard_fail_on_persistent_echo: bool = True
    patterns: list[str] = field(default_factory=lambda: list(DEFAULT_ECHO_PATTERNS))

    @classmethod
    def from_skillopt_settings(cls, skillopt_settings: dict[str, Any] | None) -> "OutputHygieneConfig":
        raw = (skillopt_settings or {}).get("output_hygiene") or {}
        pats = raw.get("patterns")
        return cls(
            enabled=bool(raw.get("enabled", True)),
            retry_on_prompt_echo=bool(raw.get("retry_on_prompt_echo", True)),
            hard_fail_on_persistent_echo=bool(raw.get("hard_fail_on_persistent_echo", True)),
            patterns=[str(p) for p in pats] if pats else list(DEFAULT_ECHO_PATTERNS),
        )


def _compile_patterns(patterns: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    out: list[tuple[str, re.Pattern[str]]] = []
    for pat in patterns:
        if not pat:
            continue
        try:
            out.append((pat, re.compile(pat, re.I | re.M)))
        except re.error:
            out.append((pat, re.compile(re.escape(pat), re.I | re.M)))
    return out


def detect_output_hygiene(
    text: str,
    config: OutputHygieneConfig | None = None,
) -> tuple[bool, str, list[str]]:
    """Return (is_clean, reason, matched_patterns)."""
    cfg = config or OutputHygieneConfig()
    t = (text or "").strip()
    if not t:
        return True, "", []
    if looks_like_tool_call_leak(t):
        return False, "tool_leak", ["tool_call_leak"]
    matched: list[str] = []
    for label, pat in _compile_patterns(cfg.patterns):
        if pat.search(t):
            matched.append(label)
    if matched:
        return False, "prompt_echo", matched
    return True, "", []


def build_hygiene_report(
    rollout_results: list[dict],
    *,
    step: int,
    config: OutputHygieneConfig | None = None,
) -> dict[str, Any]:
    cfg = config or OutputHygieneConfig()
    bad: list[dict[str, Any]] = []
    for row in rollout_results:
        pred = str(row.get("predicted_answer") or "")
        clean, reason, matched = detect_output_hygiene(pred, cfg)
        if not clean:
            bad.append({
                "id": row.get("id", ""),
                "reason": reason,
                "matched_patterns": matched,
            })
    return {
        "schema_version": "1.0",
        "step": step,
        "prompt_echo_count": sum(1 for b in bad if b.get("reason") == "prompt_echo"),
        "tool_residue_count": sum(1 for b in bad if b.get("reason") == "tool_leak"),
        "bad_outputs": bad,
    }


def write_output_hygiene_report(
    output_dir: str,
    step: int,
    report: dict[str, Any],
) -> str:
    import os

    step_dir = os.path.join(output_dir, "steps", f"step_{step:04d}")
    os.makedirs(step_dir, exist_ok=True)
    path = os.path.join(step_dir, "output_hygiene_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return path


def summarize_hygiene_results(results: list[dict]) -> dict[str, Any]:
    bad = [
        r for r in results
        if r.get("output_hygiene_reason") in ("prompt_echo", "tool_leak")
    ]
    return {
        "prompt_echo_count": sum(
            1 for r in bad if r.get("output_hygiene_reason") == "prompt_echo"
        ),
        "tool_residue_count": sum(
            1 for r in bad if r.get("output_hygiene_reason") == "tool_leak"
        ),
        "bad_output_ids": [str(r.get("id", "")) for r in bad if r.get("id")],
    }


def apply_hygiene_to_rollout_results(
    results: list[dict],
    config: OutputHygieneConfig | None = None,
) -> list[dict]:
    """Annotate rollout rows; force failed scores on persistent echo when configured."""
    cfg = config or OutputHygieneConfig()
    if not cfg.enabled:
        return results
    out: list[dict] = []
    for row in results:
        r = dict(row)
        pred = str(r.get("predicted_answer") or "")
        clean, reason, matched = detect_output_hygiene(pred, cfg)
        if not clean:
            r["output_hygiene_reason"] = reason
            r["output_hygiene_patterns"] = matched
            if cfg.hard_fail_on_persistent_echo:
                r["hard"] = 0
                r["accuracy"] = 0.0
                r["soft"] = 0.0
                r["precision"] = 0.0
                r["recall"] = 0.0
                r["f1"] = 0.0
                r["passed"] = 0
                r["passed_checks"] = []
                expected = list(r.get("expected_checks") or [])
                if expected:
                    r["missed_checks"] = expected
                r["fail_reason"] = f"{reason}: " + ", ".join(matched[:3])
        out.append(r)
    return out
