"""Phase 2 benchmark bootstrap 测试。"""
from __future__ import annotations

import json
from pathlib import Path

from code_to_skill.atom_extractor.types import SkillAtom, SourceRef
from code_to_skill.cli.benchmark_bootstrap import (
    AUTO_RULES_HEADER,
    append_atom_rules_to_skill,
    apply_benchmark_bootstrap,
    high_confidence_train_items,
    load_m3_from_run,
    merge_train_items,
    write_train_items,
)
from code_to_skill.skillopt_loop.benchmark_splits import BenchmarkSplits


def _m3(high_conf: float = 0.85, low_conf: float = 0.5):
    high = SkillAtom(
        atom_id="a1",
        kind="constraint",
        claim="Always validate idempotency",
        confidence=high_conf,
        status="accepted",
        source_refs=[SourceRef(type="code", id="Svc.java")],
        checks=["idempotency"],
    )
    low = SkillAtom(
        atom_id="a2",
        kind="constraint",
        claim="low value",
        confidence=low_conf,
        status="candidate",
        source_refs=[SourceRef(type="code", id="x")],
    )
    from code_to_skill.atom_extractor.merger import generate_benchmark_seeds
    merged = [high, low]
    return {"merged_atoms": merged, "benchmark_seeds": generate_benchmark_seeds(merged)}


def test_high_confidence_train_items_filters():
    items = high_confidence_train_items(_m3(), min_confidence=0.8)
    assert len(items) == 1
    assert items[0]["id"] == "seed-a1"
    assert items[0]["question"]


def test_merge_train_items_dedupes():
    existing = [{"id": "t1", "question": "q1"}]
    new = [{"id": "t1", "question": "dup"}, {"id": "t2", "question": "q2"}]
    merged = merge_train_items(existing, new)
    assert len(merged) == 2
    assert merged[1]["id"] == "t2"


def test_apply_benchmark_bootstrap_merge():
    m3 = _m3()
    splits = BenchmarkSplits(
        train=[{"id": "manual-1", "question": "manual"}],
        selection=[],
        test=[],
    )
    out = apply_benchmark_bootstrap(splits, m3, merge=True, min_confidence=0.8)
    assert len(out.train) == 2
    ids = {i["id"] for i in out.train}
    assert "manual-1" in ids
    assert "seed-a1" in ids


def test_append_atom_rules_to_skill():
    skill = "# Skill\n- rule one\n"
    updated = append_atom_rules_to_skill(skill, _m3(), min_confidence=0.8)
    assert AUTO_RULES_HEADER in updated
    assert "idempotency" in updated


def test_load_m3_from_run(tmp_path):
    atoms_dir = tmp_path / "atoms"
    atoms_dir.mkdir()
    atom = SkillAtom(
        atom_id="x",
        kind="constraint",
        claim="claim",
        confidence=0.9,
        status="accepted",
        source_refs=[SourceRef(type="code", id="f")],
    )
    with open(atoms_dir / "merged_atoms.jsonl", "w", encoding="utf-8") as f:
        f.write(atom.model_dump_json() + "\n")
    with open(atoms_dir / "benchmark_seeds.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "seed-x", "question": "claim"}) + "\n")

    loaded = load_m3_from_run(str(tmp_path))
    assert loaded is not None
    assert len(loaded["merged_atoms"]) == 1
    assert loaded["benchmark_seeds"][0]["id"] == "seed-x"


def test_write_train_items(tmp_path):
    bench = tmp_path / "bench"
    items = [{"id": "a1", "question": "Q", "expected_checks": ["x"]}]
    path = write_train_items(str(bench), items)
    assert path.endswith("items.json")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["items"][0]["id"] == "a1"
