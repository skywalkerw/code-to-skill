"""SourceRef normalization utilities for M3 artifacts."""
from __future__ import annotations

from .types import SkillAtom, SourceRef


DEFAULT_MAX_SOURCE_REFS_PER_ATOM = 24


def max_source_refs_from_settings(settings: dict | None) -> int:
    """Return the configured per-atom source_ref cap with a safe default."""
    try:
        value = int((settings or {}).get("max_source_refs_per_atom", DEFAULT_MAX_SOURCE_REFS_PER_ATOM))
    except (TypeError, ValueError):
        return DEFAULT_MAX_SOURCE_REFS_PER_ATOM
    return value if value > 0 else DEFAULT_MAX_SOURCE_REFS_PER_ATOM


def normalize_source_refs(
    source_refs: list[SourceRef],
    *,
    max_refs: int = DEFAULT_MAX_SOURCE_REFS_PER_ATOM,
) -> list[SourceRef]:
    """Deduplicate and cap source refs, prioritizing resolvable code evidence."""
    limit = max_refs if max_refs > 0 else DEFAULT_MAX_SOURCE_REFS_PER_ATOM
    best_by_key: dict[tuple[str, str], tuple[int, SourceRef]] = {}

    for idx, ref in enumerate(source_refs or []):
        key = _source_ref_key(ref)
        if not key[1]:
            continue
        current = best_by_key.get(key)
        if current is None:
            best_by_key[key] = (idx, ref)
            continue
        if _source_ref_rank(ref) < _source_ref_rank(current[1]):
            best_by_key[key] = (current[0], ref)

    ranked = sorted(
        best_by_key.values(),
        key=lambda item: (_source_ref_rank(item[1]), item[0]),
    )
    refs = [ref for _, ref in ranked]
    if len(refs) <= limit:
        return refs

    code_refs = [ref for ref in refs if (ref.type or "").strip().lower() == "code"]
    other_refs = [ref for ref in refs if (ref.type or "").strip().lower() != "code"]

    other_keep = min(len(other_refs), max(1, limit // 4)) if other_refs else 0
    code_keep = min(len(code_refs), limit - other_keep)
    selected = code_refs[:code_keep] + other_refs[:other_keep]

    if len(selected) < limit:
        selected_keys = {_source_ref_key(ref) for ref in selected}
        for ref in refs:
            if _source_ref_key(ref) in selected_keys:
                continue
            selected.append(ref)
            selected_keys.add(_source_ref_key(ref))
            if len(selected) >= limit:
                break
    return selected[:limit]


def cap_atom_source_refs(
    atom: SkillAtom,
    *,
    max_refs: int = DEFAULT_MAX_SOURCE_REFS_PER_ATOM,
) -> SkillAtom:
    capped = normalize_source_refs(atom.source_refs, max_refs=max_refs)
    if capped == atom.source_refs:
        return atom
    return atom.model_copy(update={"source_refs": capped})


def cap_atoms_source_refs(
    atoms: list[SkillAtom],
    *,
    max_refs: int = DEFAULT_MAX_SOURCE_REFS_PER_ATOM,
) -> list[SkillAtom]:
    return [cap_atom_source_refs(atom, max_refs=max_refs) for atom in atoms]


def _source_ref_key(ref: SourceRef) -> tuple[str, str]:
    return ((ref.type or "").strip().lower(), (ref.id or "").strip())


def _source_ref_rank(ref: SourceRef) -> tuple[int, int]:
    ref_type = (ref.type or "").strip().lower()
    authority = (ref.authority or "").strip().lower()
    has_edge_path = bool(ref.edge_path)
    if ref_type == "code" and has_edge_path:
        base = 0
    elif ref_type == "code":
        base = 1
    elif authority in {"official_doc", "official_spec"}:
        base = 2
    elif ref_type == "doc":
        base = 3
    else:
        base = 4
    return (base, -len(ref.edge_path or []))
