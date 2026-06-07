"""Benchmark split 与 Reflect/Edit 改进测试。"""
import json
from pathlib import Path

import pytest

from code_to_skill.skillopt_loop.benchmark_splits import BenchmarkSplits
from code_to_skill.skillopt_loop.edit_validator import filter_valid_edits, validate_edit
from code_to_skill.skillopt_loop.llm_components import (
    _rule_based_patches,
    _rank_edits_by_coverage,
    _skill_compact_for_reflect,
)
from code_to_skill.skillopt_loop.scoring import score_rollout_result
from code_to_skill.skillopt_loop.types import EditOp


class TestBenchmarkSplits:
    def test_from_dir(self):
        path = Path("test-data/benchmarks/fineract")
        splits = BenchmarkSplits.from_dir(str(path))
        assert len(splits.train) == 15
        assert len(splits.selection) == 22
        assert len(splits.test) == 8
        assert splits.has_explicit_splits

    def test_empty_dir(self, tmp_path):
        splits = BenchmarkSplits.from_dir(str(tmp_path))
        assert splits.train == []
        assert not splits.has_explicit_splits

    def test_resolve_explicit(self):
        path = Path("test-data/benchmarks/fineract")
        splits = BenchmarkSplits.from_dir(str(path))
        resolved = splits.resolve()
        assert resolved.use_explicit_splits
        assert resolved.source == "explicit_files"
        assert len(resolved.train) == 15
        assert len(resolved.selection) == 22
        assert len(resolved.test) == 8

    def test_resolve_ratio_fallback(self):
        items = [{"id": f"t{i}", "expected_checks": ["a"]} for i in range(10)]
        splits = BenchmarkSplits(train=items, selection=[], test=[])
        resolved = splits.resolve(selection_split_ratio=0.3, test_split_ratio=0.2)
        assert not resolved.use_explicit_splits
        assert resolved.source == "ratio"
        assert len(resolved.train) == 5
        assert len(resolved.selection) == 3
        assert len(resolved.test) == 2

    def test_validate_no_overlap(self):
        path = Path("test-data/benchmarks/fineract")
        splits = BenchmarkSplits.from_dir(str(path))
        warnings = splits.validate_splits()
        assert warnings == []

    def test_validate_detects_overlap(self):
        item = {"id": "dup", "expected_checks": ["a"]}
        splits = BenchmarkSplits(
            train=[item],
            selection=[item],
            test=[],
        )
        warnings = splits.validate_splits()
        assert any("train/selection overlap" in w for w in warnings)


class TestScoringChecks:
    def test_missed_and_passed_checks(self):
        result = score_rollout_result("会计凭证 借 贷 100.00", ["会计凭证", "借", "贷", "库存", "银行"])
        assert "会计凭证" in result["passed_checks"]
        assert "库存" in result["missed_checks"]
        assert "银行" in result["missed_checks"]

    def test_amount_check_ignores_thousands_separator(self):
        result = score_rollout_result(
            "金额 50,000.00 借 贷",
            ["50000", "借", "贷"],
        )
        assert "50000" in result["passed_checks"]
        assert result["hard"] == 1


class TestEditValidator:
    def test_reject_meta_comment(self):
        edit = EditOp(op="append", content="# Verify: 3 tasks failed, need improvement")
        ok, reason = validate_edit(edit, "# Skill")
        assert not ok
        assert reason == "meta_comment"

    def test_accept_actionable_rule(self):
        edit = EditOp(
            op="insert_after",
            target="### 2.3",
            content="- 输出必须包含关键词「库存」和「银行」",
        )
        ok, _ = validate_edit(edit, "# Skill\n### 2.3 生成会计凭证")
        assert ok

    def test_reject_all_bullets_already_in_skill(self):
        skill = (
            "# Skill\n### 分录输出要求（自动生成）\n\n"
            "- 分录表格须包含借方行，「借贷」列标注「借」\n"
            "- 分录表格须包含贷方行，「借贷」列标注「贷」"
        )
        edit = EditOp(
            op="insert_after",
            target="### 分录输出要求（自动生成）",
            content=(
                "### 分录输出要求（自动生成）\n\n"
                "- 分录表格须包含借方行，「借贷」列标注「借」\n"
                "- 分录表格须包含贷方行，「借贷」列标注「贷」"
            ),
        )
        ok, reason = validate_edit(edit, skill)
        assert not ok
        assert reason == "duplicate"


class TestTokenBudgets:
    def test_configure_overrides(self):
        from code_to_skill.skillopt_loop.token_budgets import (
            TokenBudgets,
            configure_token_budgets,
            get_token_budgets,
        )

        configure_token_budgets(None)
        defaults = get_token_budgets()
        assert defaults.reflect_failure == 16384
        assert defaults.rollout == 8192

        configure_token_budgets({"reflect_failure": 8192, "reflect_retry": [16384, 32768]})
        updated = get_token_budgets()
        assert updated.reflect_failure == 8192
        assert updated.reflect_retry == [16384, 32768]

        configure_token_budgets({
            "reflect_failure": 16384,
            "reflect_retry": [32768, 65536],
        })


class TestReflectPrompt:
    def test_compact_skill_omits_examples(self):
        skill = "# Title\n## 一、核心任务\nlong intro\n### 2.3 生成会计凭证\nrules here\n## 三、必须遵守的约束\nconstraints"
        compact = _skill_compact_for_reflect(skill)
        assert "核心任务" not in compact
        assert "2.3" in compact
        assert "约束" in compact


class TestRuleBasedPatches:
    def test_generates_semantic_rules_not_keyword_dump(self):
        results = [{
            "id": "jv_purchase_001",
            "hard": 0,
            "task_type": "journal_entry",
            "missed_checks": ["库存", "银行", "100.00"],
            "expected_checks": ["会计凭证", "借", "贷", "库存", "银行", "100.00"],
        }]
        skill = "# Skill\n### 2.3 生成会计凭证\n\n## 六、验证检查清单"
        patches = _rule_based_patches(results, skill)
        assert patches
        edit = patches[0]["edits"][0]
        assert "库存" in edit["content"]
        assert "银行" in edit["content"]
        assert "输出必须包含关键词" not in edit["content"]
        assert edit["op"] == "insert_after"
        assert edit["target"] == "### 2.3 生成会计凭证"
        assert "# Verify" not in edit["content"]

    def test_skips_rules_already_in_skill_and_appends_new(self):
        results = [{
            "id": "jv_purchase_001",
            "hard": 0,
            "task_type": "journal_entry",
            "missed_checks": ["库存", "银行", "会计凭证"],
            "expected_checks": ["会计凭证", "借", "贷", "库存", "银行"],
        }]
        skill = (
            "# Skill\n### 2.3 生成会计凭证\n\n"
            "### 分录输出要求（自动生成）\n\n"
            "- 输出必须以「## 会计凭证」为标题\n"
            "- 分录表格须包含借方行，「借贷」列标注「借」"
        )
        patches = _rule_based_patches(results, skill)
        assert patches
        edit = patches[0]["edits"][0]
        assert "库存" in edit["content"]
        assert "输出必须以" not in edit["content"]
        assert edit["op"] == "insert_after"
        assert "分录表格须包含借方行" in edit["target"]

    def test_all_rules_present_emits_task_hint(self):
        results = [{
            "id": "jv_x",
            "hard": 0,
            "task_type": "journal_entry",
            "missed_checks": ["库存", "银行"],
            "expected_checks": ["库存", "银行"],
        }]
        skill = (
            "# Skill\n### 分录输出要求（自动生成）\n\n"
            "- 购入/存货交易：借方科目名称须含「库存」\n"
            "- 付款类交易：贷方科目名称须含「银行」"
        )
        patches = _rule_based_patches(results, skill)
        assert patches
        assert "针对journal_entry" in patches[0]["edits"][0]["content"]

    def test_parse_reflect_from_content(self):
        from code_to_skill.skillopt_loop.llm_components import _parse_reflect_response
        from code_to_skill.model_provider.types import ModelResponse

        resp = ModelResponse(
            request_id="r1", backend_id="mock", model="m",
            content='{"reasoning": "test", "edits": [{"op": "append", "content": "- 必须输出会计凭证"}]}',
            parsed=None,
        )
        parsed = _parse_reflect_response(resp)
        assert parsed is not None
        assert len(parsed["edits"]) == 1

    def test_filter_rejects_placeholder(self):
        edits = [
            EditOp(op="append", content="# Verify: failed"),
            EditOp(op="insert_after", target="### 2.3",
                   content="- 输出必须包含关键词「库存」，购入分录的贷方必须包含「银行」"),
        ]
        valid, rejected = filter_valid_edits(edits, "# Skill")
        assert len(valid) == 1
        assert len(rejected) == 1


class TestSelectCoverage:
    def test_ranks_by_missed_coverage(self):
        edits = [
            EditOp(op="append", content="- 通用规则"),
            EditOp(op="insert_after", target="### 2.3",
                   content="- 输出必须包含关键词「库存」和「银行」和「借贷校验」"),
        ]
        results = [{"hard": 0, "missed_checks": ["库存", "银行", "借贷校验"]}]
        ranked = _rank_edits_by_coverage(edits, results, budget=1)
        assert "库存" in ranked[0]["edit"].content


class TestEditTraceability:
    def test_rule_patch_annotates_tasks_and_checks(self):
        results = [{
            "id": "jv_purchase_001",
            "hard": 0,
            "task_type": "journal_entry",
            "missed_checks": ["库存", "银行"],
            "expected_checks": ["库存", "银行"],
        }]
        skill = "# Skill\n### 2.3 生成会计凭证"
        patches = _rule_based_patches(results, skill)
        edit = patches[0]["edits"][0]
        assert "jv_purchase_001" in edit["related_task_ids"]
        assert "库存" in edit["related_missed_checks"]
        assert "银行" in edit["related_missed_checks"]

    def test_infer_traceability_from_content(self):
        from code_to_skill.skillopt_loop.edit_traceability import infer_edit_traceability

        edit = {"content": "- 购入/存货交易：借方科目名称须含「库存」"}
        failed = [
            {"id": "a", "missed_checks": ["库存", "银行"]},
            {"id": "b", "missed_checks": ["贷"]},
        ]
        out = infer_edit_traceability(edit, failed)
        assert out["related_task_ids"] == ["a"]
        assert "库存" in out["related_missed_checks"]

    def test_rollout_failure_records(self):
        from code_to_skill.skillopt_loop.edit_traceability import rollout_failure_records

        records = rollout_failure_records([
            {"id": "ok", "hard": 1},
            {"id": "bad", "hard": 0, "missed_checks": ["借"], "fail_reason": "missed: 借"},
        ])
        assert len(records) == 1
        assert records[0]["id"] == "bad"
        assert records[0]["missed_checks"] == ["借"]
