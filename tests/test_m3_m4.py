"""M3 + M4 集成测试。"""
import pytest
from code_to_skill.atom_extractor.types import SkillAtom, RawAtom, SourceRef
from code_to_skill.atom_extractor.scorer import score_atoms
from code_to_skill.atom_extractor.merger import merge_atoms, generate_benchmark_seeds
from code_to_skill.atom_extractor.extractor import extract_from_code, extract_from_docs
from code_to_skill.atom_extractor import run_atom_extraction

from code_to_skill.skillopt_loop.types import EditOp, BenchmarkItem, RolloutResult
from code_to_skill.skillopt_loop import (
    score_rollout_result, apply_edits, compute_semantic_hash,
    run_skillopt_loop, save_runtime_state,
)


class TestM3Types:
    def test_skill_atom(self):
        a = SkillAtom(atom_id="test", kind="constraint", claim="must do X")
        assert a.schema_version == "1.0"
        assert a.confidence == 0.5

    def test_source_ref(self):
        s = SourceRef(type="code", id="x::y")
        assert s.type == "code"


class TestM3Extractor:
    def test_extract_from_code_retry(self):
        ctx = [{"leaf_id": "test", "source_snippets": [
            {"node_id": "n1", "text": "public void retryWithBackoff() { ... }", "file_path": "a.java"}
        ]}]
        atoms = extract_from_code(ctx)
        assert len(atoms) >= 1
        assert atoms[0].atom.kind == "procedure"

    def test_extract_from_docs(self):
        chunks = [{"chunk_id": "c1", "text": "步骤 1: 检查幂等键", "content_type": "procedure"}]
        atoms = extract_from_docs(chunks)
        assert len(atoms) >= 1


class TestM3Scorer:
    def test_score_reject_no_source(self):
        atom = SkillAtom(atom_id="t", kind="constraint", claim="test")
        raw = RawAtom(raw_id="r1", atom=atom)
        scored = score_atoms([raw])
        assert scored[0].status == "rejected"

    def test_score_accept_with_code_source(self):
        atom = SkillAtom(atom_id="t", kind="constraint", claim="valid claim",
                         source_refs=[SourceRef(type="code", id="x")])
        raw = RawAtom(raw_id="r1", atom=atom)
        scored = score_atoms([raw])
        assert scored[0].status in ("accepted", "candidate")


class TestM3Merger:
    def test_merge_duplicates(self):
        a1 = SkillAtom(atom_id="a", kind="constraint", claim="same claim",
                       source_refs=[SourceRef(type="code", id="x")])
        a2 = SkillAtom(atom_id="b", kind="constraint", claim="same claim",
                       source_refs=[SourceRef(type="code", id="y")])
        merged = merge_atoms([a1, a2])
        assert len(merged) == 1
        assert len(merged[0].source_refs) == 2

    def test_benchmark_seeds(self):
        a = SkillAtom(atom_id="a", kind="constraint", claim="high risk",
                      confidence=0.8, risk="high", checks=["check1"])
        seeds = generate_benchmark_seeds([a])
        assert len(seeds) >= 1


class TestM4Scorer:
    def test_all_pass(self):
        result = score_rollout_result("must do idempotency check", ["idempotency"])
        assert result["hard"] == 1

    def test_partial(self):
        result = score_rollout_result("general answer", ["idempotency", "retry"])
        assert result["soft"] == 0.0


class TestM4Updater:
    def test_append(self):
        result = apply_edits("line 1", [EditOp(op="append", content="line 2")])
        assert "line 2" in result

    def test_delete(self):
        result = apply_edits("remove_me\ngood", [EditOp(op="delete", target="remove_me")])
        assert "remove_me" not in result
        assert "good" in result

    def test_semantic_hash(self):
        h1 = compute_semantic_hash("a  b")
        h2 = compute_semantic_hash("a b")
        assert h1 == h2


class TestM4Pipeline:
    def test_skillopt_mvp(self, tmp_path):
        skill = "# Test Skill\n## Workflow\n1. Check idempotency\n2. Limit retries to 3"
        items = [
            {"id": "t1", "question": "Review code", "task_type": "code_review",
             "expected_checks": ["idempotency", "retry"]},
            {"id": "t2", "question": "Review code", "task_type": "code_review",
             "expected_checks": ["idempotency"]},
            {"id": "t3", "question": "Review code", "task_type": "code_review",
             "expected_checks": ["retry"]},
            {"id": "t4", "question": "Review code", "task_type": "code_review",
             "expected_checks": ["audit"]},
        ]
        result = run_skillopt_loop(
            initial_skill=skill,
            benchmark_items=items,
            output_dir=str(tmp_path / "output"),
            num_epochs=2,
            batch_size=2,
            edit_budget=2,
        )
        assert len(result["history"]) > 0
        assert (tmp_path / "output" / "best_skill.md").exists()
        assert (tmp_path / "output" / "history.json").exists()


class TestM3Pipeline:
    def test_full_atom_extraction(self, tmp_path):
        leaf_ctx = [{"leaf_id": "l1", "source_snippets": [
            {"node_id": "n1", "text": "@Transactional public void process()", "file_path": "a.java"}
        ]}]
        doc_chunks = [{"chunk_id": "c1", "text": "步骤: 先查幂等再重试", "content_type": "procedure"}]
        result = run_atom_extraction(leaf_contexts=leaf_ctx, document_chunks=doc_chunks,
                                     output_root=str(tmp_path / "atoms"))
        assert len(result["merged_atoms"]) > 0
