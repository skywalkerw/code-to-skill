"""M4 self_evolution — trace pool、proposals、artifact_quality 测试。"""
from __future__ import annotations

import json
import os

import pytest

from code_to_skill.atom_extractor.artifact_quality import compute_artifact_quality
from code_to_skill.atom_extractor.types import SkillAtom, SourceRef
from code_to_skill.skillopt_loop.proposal_merge import proposals_to_edits, proposals_to_patches
from code_to_skill.skillopt_loop.proposals import (
    build_failure_proposals,
    build_success_proposals,
    generate_step_proposals,
    write_proposals,
)
from code_to_skill.skillopt_loop.self_evolution_config import SelfEvolutionConfig
from code_to_skill.skillopt_loop.trace_pool import TracePoolManager
from code_to_skill.skillopt_loop.gate import GateManager
from code_to_skill.skillopt_loop.frontier import FrontierPool
from code_to_skill.skillopt_loop.hygiene import (
    apply_hygiene_with_gate,
    estimate_skill_tokens,
    should_run_hygiene,
)
from code_to_skill.skillopt_loop import run_skillopt_loop
from code_to_skill.skillopt_loop.self_evolution_validate import validate_self_evolution_run


def _sample_failure_results():
    base = {
        "task_type": "journal_entry",
        "hard": 0,
        "soft": 0.3,
        "context_refs": ["sym:JournalEntry"],
        "expected_checks": ["balance", "debit"],
        "passed_checks": [],
        "predicted_answer": "x",
        "fail_reason": "missed: balance",
    }
    return [
        {**base, "id": "jv_001", "missed_checks": ["balance"]},
        {**base, "id": "jv_002", "missed_checks": ["balance"]},
        {**base, "id": "jv_003", "missed_checks": ["debit"]},
    ]


class TestTracePool:
    def test_record_and_cluster(self, tmp_path):
        cfg = SelfEvolutionConfig(enabled=True, min_support_count=2)
        pool = TracePoolManager(str(tmp_path), cfg)
        results = _sample_failure_results()
        pool.record_batch(1, results, "v0001")
        clusters, summary = pool.cluster_traces(step=1)
        assert summary["failure_traces"] == 3
        assert len(clusters) >= 1
        balance_cluster = next(c for c in clusters if "balance" in c.get("missed_checks", []))
        assert balance_cluster["support_count"] == 2

    def test_traces_jsonl_written(self, tmp_path):
        cfg = SelfEvolutionConfig(enabled=True)
        pool = TracePoolManager(str(tmp_path), cfg)
        pool.record_batch(2, _sample_failure_results(), "v0002")
        path = os.path.join(tmp_path, "trace_pool", "traces.jsonl")
        assert os.path.isfile(path)
        rows = [json.loads(ln) for ln in open(path, encoding="utf-8")]
        assert len(rows) == 3
        assert rows[0]["trace_id"].startswith("step_0002:")


class TestProposals:
    def test_failure_proposals_min_support(self):
        cfg = SelfEvolutionConfig(min_support_count=2)
        clusters = [
            {
                "cluster_id": "cluster-000-balance",
                "support_count": 2,
                "missed_checks": ["balance"],
                "trace_ids": ["t1", "t2"],
                "task_type": "journal_entry",
            },
            {
                "cluster_id": "cluster-001-debit",
                "support_count": 1,
                "missed_checks": ["debit"],
                "trace_ids": ["t3"],
                "task_type": "journal_entry",
            },
        ]
        props = build_failure_proposals(clusters, cfg, step=1)
        ready = [p for p in props if p["status"] == "ready"]
        review = [p for p in props if p["status"] == "needs_review"]
        assert len(ready) == 1
        assert len(review) == 1

    def test_proposals_to_edits(self):
        cfg = SelfEvolutionConfig(min_support_count=2, max_new_rules_per_step=2)
        proposals = [
            {
                "status": "ready",
                "source": "failure_cluster",
                "support_count": 3,
                "candidate_rule": "When handling journal entries, ensure balance.",
                "missed_checks": ["balance"],
                "support_trace_ids": ["step_0001:item_a"],
            },
        ]
        edits = proposals_to_edits(proposals, cfg)
        assert len(edits) == 1
        assert edits[0].op == "append"
        assert "balance" in edits[0].content

    def test_write_proposals_keeps_per_step_history(self, tmp_path):
        opt = tmp_path / "optimization"
        opt.mkdir()
        row = [{"proposal_id": "p1", "status": "ready", "support_count": 2}]
        write_proposals(
            str(opt),
            failure_proposals=row,
            success_proposals=[],
            step=2,
        )
        write_proposals(
            str(opt),
            failure_proposals=[{**row[0], "proposal_id": "p2"}],
            success_proposals=[],
            step=6,
        )
        step2 = opt / "proposals" / "steps" / "step_0002" / "merged_proposals.jsonl"
        step6 = opt / "proposals" / "steps" / "step_0006" / "merged_proposals.jsonl"
        assert step2.is_file()
        assert step6.is_file()
        assert json.loads(step2.read_text(encoding="utf-8").strip())["proposal_id"] == "p1"
        assert json.loads(step6.read_text(encoding="utf-8").strip())["proposal_id"] == "p2"
        index = (opt / "proposals" / "steps_index.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(index) == 2

    def test_success_proposals_require_trace_id(self):
        cfg = SelfEvolutionConfig(min_support_count=2)
        traces = [
            {
                "id": "jv_a",
                "trace_id": "step_0003:item_jv_a",
                "hard": 1,
                "task_type": "journal_entry",
                "passed_checks": ["balance"],
            },
            {
                "id": "jv_b",
                "trace_id": "step_0003:item_jv_b",
                "hard": 1,
                "task_type": "journal_entry",
                "passed_checks": ["balance"],
            },
        ]
        props = build_success_proposals(traces, cfg, step=3)
        assert len(props) == 1
        assert props[0]["support_trace_ids"] == [
            "step_0003:item_jv_a",
            "step_0003:item_jv_b",
        ]

    def test_generate_step_proposals_integration(self):
        cfg = SelfEvolutionConfig(enabled=True, min_support_count=2)
        clusters = [
            {
                "cluster_id": "c0",
                "support_count": 2,
                "missed_checks": ["balance"],
                "trace_ids": ["t1", "t2"],
                "task_type": "journal_entry",
            },
        ]
        traces = _sample_failure_results()
        for i, t in enumerate(traces):
            traces[i] = {**t, "trace_id": f"step_0001:item_{t['id']}", "step": 1}
        fail_p, succ_p = generate_step_proposals(clusters, traces, cfg, step=1)
        patches = proposals_to_patches(fail_p + succ_p, cfg)
        assert fail_p
        assert patches

    def test_success_proposals_without_failure_clusters(self):
        cfg = SelfEvolutionConfig(enabled=True, min_support_count=2)
        traces = [
            {
                "trace_id": "step_0001:item_jv_a",
                "item_id": "jv_a",
                "step": 1,
                "hard": 1,
                "task_type": "journal_entry",
                "question": "loan disbursement 100",
                "passed_checks": ["会计凭证", "借", "贷", "贷款", "现金", "100"],
                "context_refs": ["CashBasedAccountingProcessorForLoan.java#createJournalEntriesForDisbursements"],
            },
            {
                "trace_id": "step_0001:item_jv_b",
                "item_id": "jv_b",
                "step": 1,
                "hard": 1,
                "task_type": "journal_entry",
                "question": "loan repayment 50",
                "passed_checks": ["会计凭证", "借", "贷", "还款", "现金", "贷款", "50"],
                "context_refs": ["CashBasedAccountingProcessorForLoan.java#createJournalEntriesForRepayments"],
            },
        ]
        fail_p, succ_p = generate_step_proposals([], traces, cfg, step=1)
        patches = proposals_to_patches(fail_p + succ_p, cfg, current_skill="# Skill\n## 输出要求\n")
        assert not fail_p
        assert succ_p
        assert patches
        assert "jv_a" in patches[0]["edits"][0]["content"]
        assert "CashBasedAccountingProcessorForLoan" in patches[0]["edits"][0]["content"]

    def test_success_proposals_use_configured_domain_filters(self):
        cfg = SelfEvolutionConfig(
            enabled=True,
            min_support_count=2,
            success_ignore_checks=["会计凭证", "借", "贷", "借贷平衡"],
            success_rule_tail="金额只取用户输入，并输出借贷平衡检查",
        )
        traces = [
            {
                "trace_id": "step_0001:item_jv_a",
                "item_id": "jv_a",
                "step": 1,
                "hard": 1,
                "task_type": "journal_entry",
                "question": "loan disbursement 100",
                "passed_checks": ["会计凭证", "借", "贷", "贷款", "100"],
            },
            {
                "trace_id": "step_0001:item_jv_b",
                "item_id": "jv_b",
                "step": 1,
                "hard": 1,
                "task_type": "journal_entry",
                "question": "loan repayment 50",
                "passed_checks": ["会计凭证", "借", "贷", "还款", "50"],
            },
        ]
        _, succ_p = generate_step_proposals([], traces, cfg, step=1)
        rule = succ_p[0]["candidate_rule"]
        assert "贷款" in rule
        assert "还款" in rule
        assert "金额只取用户输入" in rule
        assert "会计凭证" not in rule


class TestArtifactQuality:
    def test_passes_clean_seeds(self):
        atoms = [
            SkillAtom(
                atom_id="a1",
                kind="constraint",
                claim="rule",
                source_refs=[SourceRef(type="code", id="node-1")],
                status="candidate",
            ),
        ]
        seeds = [{
            "id": "seed_1",
            "context_refs": ["sym:X"],
            "expected_checks": ["balance"],
        }]
        q = compute_artifact_quality(atoms, seeds)
        assert q["passed"] is True
        assert q["seed_missing_id"] == 0

    def test_fails_missing_id(self):
        q = compute_artifact_quality([], [{"expected_checks": ["x"]}])
        assert q["passed"] is False
        assert "seed_missing_id" in q["failures"]


class TestStrictGate:
    def test_reject_tie_under_strict(self):
        gate = GateManager(metric="soft", strict_improvement=True, reject_ties=True, delta=0.01)
        decision = gate.evaluate(0.5, 0.70, best_score=0.70, current_score=0.70)
        assert decision.action == "reject"
        assert "tie" in decision.reason or "no_improvement" in decision.reason

    def test_accept_tie_when_non_strict_and_ties_allowed(self):
        gate = GateManager(
            metric="soft",
            strict_improvement=False,
            reject_ties=False,
            allow_tie_acceptance=True,
        )
        decision = gate.evaluate(0.5, 0.70, best_score=0.70, current_score=0.70)
        assert decision.action == "accept"
        assert "tie_accepted" in decision.reason


class TestTraceMergeMode:
    def test_trace_merge_disables_strict_gate(self):
        cfg = SelfEvolutionConfig.from_dict({}, trace_merge_only=True)
        assert cfg.enabled is True
        assert cfg.strict_improvement is False
        assert cfg.inject_rule_ids is False
        assert cfg.frontier_enabled is False


class TestFrontierPool:
    def test_try_add_and_replace(self, tmp_path):
        pool = FrontierPool(str(tmp_path), max_size=2)
        assert pool.try_add("- rule A\n", 0.6, 1)
        assert pool.try_add("- rule B\n", 0.7, 2)
        assert pool.try_add("- rule C\n", 0.8, 3)
        assert len(pool.entries) == 2
        scores = sorted([e["score"] for e in pool.entries], reverse=True)
        assert scores == [0.8, 0.7]

    def test_select_parent_different_skill(self, tmp_path):
        pool = FrontierPool(str(tmp_path), max_size=2)
        pool.try_add("- alpha\n", 0.75, 1)
        current = "- beta\n"
        parent, src = pool.select_parent(current, 0.74)
        assert src.startswith("frontier")
        assert "alpha" in parent


class _OfflineSelfEvolveAdapter:
    """离线 adapter：固定失败 rollout，驱动 trace pool / proposals。"""

    code_tools = None

    def setup(self, cfg=None):
        return None

    def rollout(self, skill, items, target_backend=None, out_dir="", code_retrieval_kwargs=None):
        out = []
        for item in items:
            checks = list(item.get("expected_checks") or [])
            missed = checks[1:] or checks[:1]
            passed = [c for c in checks if c not in missed]
            out.append({
                "id": item.get("id", ""),
                "question": item.get("question", ""),
                "context_refs": list(item.get("context_refs") or []),
                "expected_checks": checks,
                "passed_checks": passed,
                "missed_checks": missed,
                "hard": 0,
                "soft": 0.2,
                "predicted_answer": "stub",
                "fail_reason": "stub",
                "task_type": item.get("task_type", "journal_entry"),
            })
        return out

    def evaluate(self, skill, items, target_backend=None):
        return {"soft": 0.35, "accuracy": 0.0, "f1": 0.1}


class _OfflineAllSuccessAdapter:
    """离线 adapter：固定成功 rollout，用于验证成功 trace 也能沉淀规则。"""

    code_tools = None

    def setup(self, cfg=None):
        return None

    def rollout(self, skill, items, target_backend=None, out_dir="", code_retrieval_kwargs=None):
        out = []
        for item in items:
            checks = list(item.get("expected_checks") or [])
            out.append({
                "id": item.get("id", ""),
                "question": item.get("question", ""),
                "context_refs": list(item.get("context_refs") or []),
                "expected_checks": checks,
                "passed_checks": checks,
                "missed_checks": [],
                "hard": 1,
                "soft": 1.0,
                "predicted_answer": "## 会计凭证\n借\n贷\n借贷平衡",
                "fail_reason": "",
                "task_type": item.get("task_type", "journal_entry"),
            })
        return out

    def evaluate(self, skill, items, target_backend=None):
        return {"soft": 1.0, "accuracy": 1.0, "f1": 1.0}


class _OfflineKnowledgeToleranceAdapter(_OfflineAllSuccessAdapter):
    """成功知识候选略降 selection，但应被 knowledge gate 容忍沉淀。"""

    def _downgraded_results(self, skill, items):
        from code_to_skill.skillopt_loop.reflect_helpers import SCENARIO_SECTION_HEADING

        if SCENARIO_SECTION_HEADING not in skill and "Scenario rules" not in skill:
            return None
        out = []
        for item in items:
            checks = list(item.get("expected_checks") or [])
            out.append({
                "id": item.get("id", ""),
                "question": item.get("question", ""),
                "context_refs": list(item.get("context_refs") or []),
                "expected_checks": checks,
                "passed_checks": checks,
                "missed_checks": [],
                "hard": 1,
                "soft": 0.95,
                "accuracy": 0.95,
                "predicted_answer": "## 会计凭证\n借\n贷\n借贷平衡",
                "fail_reason": "",
                "task_type": item.get("task_type", "journal_entry"),
            })
        return out

    def rollout(self, skill, items, target_backend=None, out_dir="", code_retrieval_kwargs=None):
        downgraded = self._downgraded_results(skill, items)
        if downgraded is not None:
            return downgraded
        return super().rollout(skill, items, target_backend=target_backend, out_dir=out_dir)

    def evaluate(self, skill, items, target_backend=None):
        downgraded = self._downgraded_results(skill, items)
        if downgraded is not None:
            return {"soft": 0.95, "accuracy": 0.95, "f1": 0.95}
        return {"soft": 1.0, "accuracy": 1.0, "f1": 1.0}


class TestSelfEvolutionIntegration:
    @pytest.mark.parametrize("mode", ["trace_merge", "self_evolve"])
    def test_offline_run_produces_artifacts(self, tmp_path, mode):
        items = [
            {
                "id": "jv_a",
                "question": "buy inventory",
                "task_type": "journal_entry",
                "expected_checks": ["balance", "debit", "credit"],
                "context_refs": ["sym:Journal"],
            },
            {
                "id": "jv_b",
                "question": "sell inventory",
                "task_type": "journal_entry",
                "expected_checks": ["balance", "debit", "credit"],
                "context_refs": ["sym:Journal"],
            },
        ]
        kwargs = {
            "trace_merge": mode == "trace_merge",
            "self_evolve": mode == "self_evolve",
        }
        out = str(tmp_path / "opt")
        run_skillopt_loop(
            initial_skill="# Skill\n- rule one\n",
            benchmark_items=items,
            selection_items=items[:1],
            output_dir=out,
            num_epochs=1,
            batch_size=2,
            edit_budget=2,
            use_llm_rollout=False,
            enable_code_tools=False,
            adapter=_OfflineSelfEvolveAdapter(),
            **kwargs,
        )
        assert os.path.isfile(os.path.join(out, "trace_pool", "traces.jsonl"))
        assert os.path.isdir(os.path.join(out, "proposals"))
        report = validate_self_evolution_run(out)
        assert report["passed"] is True

    def test_all_success_run_updates_current_with_trace_rules(self, tmp_path):
        items = [
            {
                "id": "jv_success_a",
                "question": "loan disbursement 100",
                "task_type": "journal_entry",
                "expected_checks": ["会计凭证", "借", "贷", "贷款", "现金", "100"],
                "context_refs": ["CashBasedAccountingProcessorForLoan.java#createJournalEntriesForDisbursements"],
            },
            {
                "id": "jv_success_b",
                "question": "loan repayment 50",
                "task_type": "journal_entry",
                "expected_checks": ["会计凭证", "借", "贷", "还款", "现金", "贷款", "50"],
                "context_refs": ["CashBasedAccountingProcessorForLoan.java#createJournalEntriesForRepayments"],
            },
        ]
        out = str(tmp_path / "opt_success")
        result = run_skillopt_loop(
            initial_skill="# Skill\n## 输出要求\n- base rule\n",
            benchmark_items=items,
            selection_items=items,
            output_dir=out,
            num_epochs=1,
            batch_size=2,
            edit_budget=2,
            use_llm_rollout=False,
            enable_code_tools=False,
            adapter=_OfflineAllSuccessAdapter(),
            self_evolution_settings={
                "enabled": True,
                "trace_pool": {"min_support_count": 2},
                "gate": {"strict_improvement": False, "reject_ties": False},
            },
            skillopt_settings={"quality_gate": {"enabled": False}},
        )
        current_skill_path = os.path.join(out, "skills", "skill_v0001.md")
        assert os.path.isfile(current_skill_path)
        current_skill = open(current_skill_path, encoding="utf-8").read()
        assert "jv_success_a" in current_skill or "贷款" in current_skill
        assert result["history"]
        assert result["history"][-1]["gate_action"] in ("accept", "accept_current_knowledge")
        assert result["history"][-1]["best_monotonic"] is True

    def test_success_knowledge_merge_updates_current_not_best(self, tmp_path):
        items = [
            {
                "id": "jv_success_a",
                "question": "loan disbursement 100",
                "task_type": "journal_entry",
                "expected_checks": ["会计凭证", "借", "贷", "贷款", "现金", "100"],
                "context_refs": ["CashBasedAccountingProcessorForLoan.java#createJournalEntriesForDisbursements"],
            },
            {
                "id": "jv_success_b",
                "question": "loan repayment 50",
                "task_type": "journal_entry",
                "expected_checks": ["会计凭证", "借", "贷", "还款", "现金", "贷款", "50"],
                "context_refs": ["CashBasedAccountingProcessorForLoan.java#createJournalEntriesForRepayments"],
            },
        ]
        out = str(tmp_path / "opt_knowledge")
        result = run_skillopt_loop(
            initial_skill="# Skill\n## 输出要求\n- base rule\n",
            benchmark_items=items,
            selection_items=items,
            output_dir=out,
            num_epochs=1,
            batch_size=2,
            edit_budget=2,
            use_llm_rollout=False,
            enable_code_tools=False,
            adapter=_OfflineKnowledgeToleranceAdapter(),
            self_evolution_settings={
                "enabled": True,
                "trace_pool": {"min_support_count": 2},
                "knowledge": {"enabled": True, "gate_tolerance": 0.1},
            },
            skillopt_settings={"quality_gate": {"enabled": False}},
        )
        assert result["best_score"] >= 0.95
        knowledge_report = os.path.join(out, "steps", "step_0001", "knowledge_merge.json")
        assert os.path.isfile(knowledge_report)
        with open(knowledge_report, encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["action"] == "accept"
        last = result["history"][-1]
        assert last["gate_action"] == "accept_current_knowledge"
        assert last["best_monotonic"] is True
        assert last["best_score_after"] >= last["best_score_before"] - 1e-9


class TestKnowledgeAcceptMonotonic:
    def test_knowledge_accept_does_not_lower_best(self):
        """best=0.70, knowledge=0.66, tolerance=0.05 → best stays 0.70."""
        gate = GateManager(metric="soft", delta=0.01)
        best_score = 0.70
        current_score = 0.70
        knowledge_gate = 0.66
        tolerance = 0.05
        knowledge_delta = gate.delta

        old_best = best_score
        if knowledge_gate > best_score + knowledge_delta:
            action = "accept_new_best_from_knowledge"
            best_score = knowledge_gate
        elif knowledge_gate >= current_score - tolerance:
            action = "accept_current_knowledge"
            current_score = knowledge_gate
        else:
            action = "reject_knowledge"

        assert action == "accept_current_knowledge"
        assert best_score == old_best == 0.70
        assert current_score == 0.66


class TestHygieneHelpers:
    def test_should_run_when_over_token_budget(self):
        cfg = SelfEvolutionConfig(max_skill_tokens=10)
        long_skill = "- " + ("x" * 200)
        assert should_run_hygiene(long_skill, cfg) is True

    def test_estimate_tokens(self):
        assert estimate_skill_tokens("abcd") >= 1

    def test_apply_hygiene_no_edits(self, tmp_path):
        class _Adapter:
            def evaluate(self, skill, items, target_backend=None):
                return {"accuracy": 0.5, "soft": 0.5}

        result = apply_hygiene_with_gate(
            "- only rule\n",
            str(tmp_path),
            adapter=_Adapter(),
            selection_items=[{"id": "t1", "expected_checks": ["x"]}],
            target_backend=None,
            force=False,
        )
        assert result["applied"] is False
