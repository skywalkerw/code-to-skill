"""Atom extractor 通用关键词提取测试。"""
from __future__ import annotations

from code_to_skill.atom_extractor.aligner import align_atoms
from code_to_skill.atom_extractor.keywords import (
    extract_alignment_tokens,
    extract_seed_check_tokens,
)
from code_to_skill.atom_extractor.merger import generate_benchmark_seeds
from code_to_skill.atom_extractor.types import SkillAtom, SourceRef


def test_extract_alignment_tokens_from_identifiers():
    tokens = extract_alignment_tokens(
        "RetryService calls PaymentProcessor with idempotency key"
    )
    assert "retryservice" in tokens or "retry" in tokens
    assert "paymentprocessor" in tokens or "payment" in tokens
    assert "idempotency" in tokens


def test_extract_alignment_tokens_skips_stopwords():
    tokens = extract_alignment_tokens("the handler must validate input")
    assert "the" not in tokens
    assert "handler" in tokens
    assert "validate" in tokens


def test_extract_seed_check_tokens_from_claim():
    checks = extract_seed_check_tokens(
        "Idempotency guard in OrderService before charge",
        limit=3,
    )
    assert checks
    assert any("idempotency" in c.lower() or "order" in c.lower() for c in checks)


def test_align_atoms_merges_by_shared_tokens():
    a1 = SkillAtom(
        atom_id="a",
        kind="procedure",
        claim="RetryService handles payment retry with backoff",
        source_refs=[SourceRef(type="code", id="x")],
    )
    a2 = SkillAtom(
        atom_id="b",
        kind="procedure",
        claim="Payment retry logic documented in RetryService guide",
        source_refs=[SourceRef(type="doc", id="y")],
    )
    aligned = align_atoms([a1, a2])
    assert len(aligned) == 1
    assert len(aligned[0].source_refs) == 2


def test_generate_benchmark_seeds_uses_generic_tokens():
    atom = SkillAtom(
        atom_id="a",
        kind="constraint",
        claim="OrderService validates idempotency before charge",
        action="Confirm idempotency key before retry",
        confidence=0.8,
        checks=[],
    )
    seeds = generate_benchmark_seeds([atom])
    assert seeds
    checks = seeds[0]["expected_checks"]
    assert any("idempotency" in c.lower() for c in checks)
