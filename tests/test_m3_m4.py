"""M3 + M4 集成测试。"""
import pytest
from code_to_skill.atom_extractor.types import SkillAtom, RawAtom, SourceRef
from code_to_skill.atom_extractor.scorer import score_atoms
from code_to_skill.atom_extractor.merger import merge_atoms, generate_benchmark_seeds
from code_to_skill.atom_extractor.extractor import extract_from_code, extract_from_docs
from code_to_skill.atom_extractor import run_atom_extraction

from code_to_skill.skillopt_loop.types import EditOp, BenchmarkItem, RolloutResult
from code_to_skill.skillopt_loop.skill_ops import apply_edits
from code_to_skill.skillopt_loop import (
    score_rollout_result, compute_semantic_hash,
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

    def test_score_respects_settings_thresholds(self):
        atom = SkillAtom(
            atom_id="t", kind="constraint", claim="valid claim",
            source_refs=[SourceRef(type="code", id="x")],
            checks=["a"],
        )
        raw = RawAtom(raw_id="r1", atom=atom)
        scored = score_atoms([raw], settings={
            "confidence_tier_1_max": 0.7,
            "llm_adjustment": 0.0,
            "accepted_min": 0.99,
            "candidate_min": 0.50,
        })
        assert scored[0].status == "candidate"


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
        content, _ = apply_edits("line 1", [EditOp(op="append", content="line 2")])
        assert "line 2" in content

    def test_delete(self):
        content, _ = apply_edits("remove_me\ngood", [EditOp(op="delete", target="remove_me")])
        assert "remove_me" not in content
        assert "good" in content

    def test_semantic_hash(self):
        h1 = compute_semantic_hash("a  b")
        h2 = compute_semantic_hash("a b")
        assert h1 == h2


class _OfflineStubAdapter:
    """离线确定性 adapter：rollout 固定失败以驱动 reflect/history。"""

    code_tools = None

    def setup(self, cfg=None):
        return None

    def rollout(self, skill, items, target_backend=None, out_dir=""):
        out = []
        for item in items:
            checks = item.get("expected_checks", [])
            out.append({
                "id": item.get("id", ""),
                "question": item.get("question", ""),
                "expected_checks": checks,
                "passed_checks": checks[:1],
                "missed_checks": checks[1:3] or checks[:1],
                "hard": 0,
                "soft": 0.25,
                "accuracy": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "predicted_answer": "## 会计凭证\n借\n贷",
                "fail_reason": "stub: missed checks",
                "task_type": item.get("task_type", ""),
            })
        return out

    def evaluate(self, skill, items, target_backend=None):
        return {"soft": 0.4, "accuracy": 0.0, "f1": 0.2}


class TestM4Pipeline:
    def test_skillopt_mvp(self, tmp_path):
        skill = (
            "# Test Skill\n### 2.3 生成会计凭证\n\n"
            "## 六、验证检查清单"
        )
        items = [
            {"id": "jv_001", "question": "买入库存 100", "task_type": "journal_entry",
             "expected_checks": ["会计凭证", "借", "贷", "库存", "100.00"]},
            {"id": "jv_002", "question": "付款 50", "task_type": "journal_entry",
             "expected_checks": ["会计凭证", "借", "贷", "银行", "50.00"]},
        ]
        result = run_skillopt_loop(
            initial_skill=skill,
            benchmark_items=items,
            selection_items=[{
                "id": "jv_sel_001",
                "question": "发放贷款",
                "task_type": "journal_entry",
                "expected_checks": ["会计凭证", "借", "贷", "贷款"],
            }],
            output_dir=str(tmp_path / "output"),
            num_epochs=1,
            batch_size=2,
            edit_budget=2,
            use_llm_rollout=False,
            enable_code_tools=False,
            adapter=_OfflineStubAdapter(),
        )
        assert len(result["history"]) > 0
        assert (tmp_path / "output" / "best_skill.md").exists()
        assert (tmp_path / "output" / "history.json").exists()
        assert (tmp_path / "output" / "training_curve.json").exists()
        assert (tmp_path / "output" / "training_curve.csv").exists()


class TestM3Pipeline:
    def test_full_atom_extraction(self, tmp_path):
        leaf_ctx = [{"leaf_id": "l1", "source_snippets": [
            {"node_id": "n1", "text": "@Transactional public void process()", "file_path": "a.java"}
        ]}]
        doc_chunks = [{"chunk_id": "c1", "text": "步骤: 先查幂等再重试", "content_type": "procedure"}]
        result = run_atom_extraction(leaf_contexts=leaf_ctx, document_chunks=doc_chunks,
                                     output_root=str(tmp_path / "atoms"))
        assert len(result["merged_atoms"]) > 0
