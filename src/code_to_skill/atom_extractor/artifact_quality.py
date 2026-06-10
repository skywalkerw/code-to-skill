"""M3 artifact_quality.json — 质量摘要与门禁判定。"""
from __future__ import annotations

from typing import Any

from .types import SkillAtom


def _is_generic_check(check: str) -> bool:
    c = (check or "").strip().lower()
    if not c or len(c) < 2:
        return True
    generic = {"answer", "response", "result", "output", "correct", "valid"}
    return c in generic


def compute_artifact_quality(
    merged_atoms: list[SkillAtom],
    seeds: list[dict],
    evidence_index: list[Any] | None = None,
    *,
    atom_settings: dict | None = None,
) -> dict[str, Any]:
    """按 design/03 §3.6 计算 M3 质量指标。"""
    settings = atom_settings or {}
    max_refs = int(settings.get("max_source_refs_per_atom", 24))

    accepted = [a for a in merged_atoms if a.status == "accepted"]
    candidate = [a for a in merged_atoms if a.status in ("accepted", "candidate")]

    seed_missing_id = sum(1 for s in seeds if not s.get("id"))
    seed_missing_context_refs = sum(
        1 for s in seeds
        if not (s.get("context_refs") or []) and s.get("risk") != "needs_review"
    )
    generic_expected_checks = sum(
        1 for s in seeds
        for c in (s.get("expected_checks") or [])
        if _is_generic_check(c)
    )

    ref_total = 0
    ref_resolved = 0
    max_source_refs = 0
    for atom in candidate:
        refs = atom.source_refs or []
        max_source_refs = max(max_source_refs, len(refs))
        for ref in refs:
            ref_total += 1
            if (ref.id or "").strip():
                ref_resolved += 1

    ev_entries = len(evidence_index or [])
    ev_exact = 0
    for entry in evidence_index or []:
        src = getattr(entry, "source_ref", "") or (entry.get("source_ref") if isinstance(entry, dict) else "")
        if (src or "").strip():
            ev_exact += 1
    ev_hit_rate = (ev_exact / ev_entries) if ev_entries else 1.0

    quality = {
        "atoms_total": len(merged_atoms),
        "accepted_total": len(accepted),
        "candidate_total": len(candidate),
        "seeds_total": len(seeds),
        "seed_missing_id": seed_missing_id,
        "seed_missing_context_refs": seed_missing_context_refs,
        "generic_expected_checks": generic_expected_checks,
        "max_source_refs_per_atom": max_source_refs,
        "max_source_refs_limit": max_refs,
        "source_ref_resolve_rate": (ref_resolved / ref_total) if ref_total else 1.0,
        "evidence_entries_total": ev_entries,
        "evidence_exact_hit_rate": ev_hit_rate,
    }

    failures: list[str] = []
    if seed_missing_id > 0:
        failures.append("seed_missing_id")
    if seed_missing_context_refs > 0:
        failures.append("seed_missing_context_refs")
    if generic_expected_checks > 0:
        failures.append("generic_expected_checks")
    if max_source_refs > max_refs:
        failures.append("max_source_refs_exceeded")
    if candidate and quality["source_ref_resolve_rate"] < 0.90:
        failures.append("source_ref_resolve_rate_low")

    quality["passed"] = len(failures) == 0
    quality["failures"] = failures
    return quality
