"""Benchmark split 与 Reflect/Edit 改进测试。"""
import json
from pathlib import Path

import pytest

from code_to_skill.skillopt_loop.benchmark_splits import BenchmarkSplits
from code_to_skill.skillopt_loop.edit_validator import filter_valid_edits, validate_edit
from code_to_skill.skillopt_loop.llm_components import (
    _rule_based_patches,
    _rank_edits_by_coverage,
)
from code_to_skill.skillopt_loop.reflect_helpers import (
    RULE_SECTION_HEADING_PRIMARY,
    skill_compact_for_reflect,
)
from code_to_skill.skillopt_loop.scoring import score_benchmark_item, score_rollout_result
from code_to_skill.skillopt_loop.types import EditOp


class TestBenchmarkSplits:
    def test_from_dir(self):
        path = Path("demo-project/benchmarks/fineract")
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
        path = Path("demo-project/benchmarks/fineract")
        splits = BenchmarkSplits.from_dir(str(path))
        resolved = splits.resolve()
        assert resolved.use_explicit_splits
        assert resolved.source == "explicit_files"
        assert len(resolved.train) == 15
        assert len(resolved.selection) == 22
        assert len(resolved.test) == 8

    def test_resolve_train_only(self):
        items = [{"id": f"t{i}", "expected_checks": ["a"]} for i in range(10)]
        splits = BenchmarkSplits(train=items, selection=[], test=[])
        resolved = splits.resolve()
        assert not resolved.use_explicit_splits
        assert resolved.source == "train_only"
        assert len(resolved.train) == 10
        assert resolved.selection == []
        assert resolved.test == []

    def test_validate_no_overlap(self):
        path = Path("demo-project/benchmarks/fineract")
        splits = BenchmarkSplits.from_dir(str(path))
        warnings = splits.validate_splits()
        assert warnings == []

    def test_fast_subset_from_dir(self):
        path = Path("demo-project/benchmarks/fineract-fast")
        splits = BenchmarkSplits.from_dir(str(path))
        assert len(splits.train) == 5
        assert len(splits.selection) == 6
        assert len(splits.test) == 3
        assert splits.validate_splits() == []

    def test_from_dir_injects_benchmark_dir_and_python_scorer(self):
        path = Path("demo-project/benchmarks/fineract-fast").resolve()
        splits = BenchmarkSplits.from_dir(str(path))
        item = splits.train[0]
        assert item["_benchmark_dir"] == str(path)
        assert item["scorer"] == "python_script"
        assert item["scorer_config"]["script"] == "../score_expected_checks.py"

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

    def test_python_script_scorer(self, tmp_path):
        script = tmp_path / "score_item.py"
        script.write_text(
            "import json, sys\n"
            "payload = json.load(sys.stdin)\n"
            "text = payload['predicted']\n"
            "ok = '借方' in text and '贷方' in text and '平衡' in text\n"
            "print(json.dumps({\n"
            "  'hard': 1 if ok else 0,\n"
            "  'soft': 1.0 if ok else 0.25,\n"
            "  'passed_checks': ['balanced'] if ok else [],\n"
            "  'missed_checks': [] if ok else ['balanced'],\n"
            "  'justification': 'script evaluated structure'\n"
            "}, ensure_ascii=False))\n",
            encoding="utf-8",
        )
        result = score_benchmark_item(
            "表格包含借方、贷方，并说明平衡。",
            {
                "scorer": "python_script",
                "score_script": str(script),
                "expected_checks": ["balanced"],
            },
        )
        assert result["hard"] == 1
        assert result["score_type"] == "python_script"
        assert result["passed_checks"] == ["balanced"]

    def test_python_script_scorer_accepts_count_fields(self, tmp_path):
        script = tmp_path / "score_counts.py"
        script.write_text(
            "import json\n"
            "print(json.dumps({'soft': 0.5, 'passed': 1, 'total': 2}))\n",
            encoding="utf-8",
        )
        result = score_benchmark_item(
            "answer",
            {
                "scorer": "python_script",
                "score_script": str(script),
                "expected_checks": ["a", "b"],
            },
        )
        assert result["hard"] == 0
        assert result["soft"] == 0.5
        assert result["passed"] == 1
        assert result["missed_checks"] == ["a", "b"]

    def test_shared_benchmark_score_script(self):
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[1]
            / "demo-project/benchmarks/score_expected_checks.py"
        )
        item = {
            "scorer": "python_script",
            "scorer_config": {"script": "../score_expected_checks.py"},
            "_benchmark_dir": str(script.parent / "fineract-fast"),
            "expected_checks": ["借方", "贷方", "平衡"],
        }
        result = score_benchmark_item(
            "凭证含借方 100 与贷方 100，借贷平衡。",
            item,
        )
        assert result["hard"] == 1
        assert result["score_type"] == "python_script"
        assert set(result["passed_checks"]) == {"借方", "贷方", "平衡"}

    def test_python_script_scorer_resolves_config_base_dir(self, tmp_path):
        script = tmp_path / "score_relative.py"
        script.write_text(
            "import json\n"
            "print(json.dumps({'hard': 1, 'soft': 1.0, 'passed_checks': ['ok']}))\n",
            encoding="utf-8",
        )
        result = score_benchmark_item(
            "answer",
            {
                "scorer": "python_script",
                "scorer_config": {
                    "script": "score_relative.py",
                    "base_dir": str(tmp_path),
                },
                "expected_checks": ["ok"],
            },
        )
        assert result["hard"] == 1
        assert result["passed_checks"] == ["ok"]


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
        ok, _ = validate_edit(edit, "# Skill\n### Output format")
        assert ok

    def test_reject_all_bullets_already_in_skill(self):
        skill = (
            f"# Skill\n{RULE_SECTION_HEADING_PRIMARY}\n\n"
            "- Output must satisfy verification check «借»\n"
            "- Output must satisfy verification check «贷»"
        )
        edit = EditOp(
            op="insert_after",
            target=RULE_SECTION_HEADING_PRIMARY,
            content=(
                f"{RULE_SECTION_HEADING_PRIMARY}\n\n"
                "- Output must satisfy verification check «借»\n"
                "- Output must satisfy verification check «贷»"
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
        skill = (
            "# Title\n## Intro\nlong intro\n"
            "### Output format\nrules here\n## Constraints\nconstraints"
        )
        compact = skill_compact_for_reflect(skill)
        assert "long intro" not in compact
        assert "Output format" in compact
        assert "Constraints" in compact


class TestRuleBasedPatches:
    def test_generates_semantic_rules_not_keyword_dump(self):
        results = [{
            "id": "jv_purchase_001",
            "hard": 0,
            "task_type": "journal_entry",
            "missed_checks": ["库存", "银行", "100.00"],
            "expected_checks": ["会计凭证", "借", "贷", "库存", "银行", "100.00"],
        }]
        skill = "# Skill\n## Workflow\n\n### Output format"
        patches = _rule_based_patches(results, skill)
        assert patches
        edit = patches[0]["edits"][0]
        assert "库存" in edit["content"]
        assert "银行" in edit["content"]
        assert "verification check" in edit["content"]
        assert edit["op"] == "insert_after"
        assert edit["target"] == "## Workflow"
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
            f"# Skill\n## Workflow\n\n{RULE_SECTION_HEADING_PRIMARY}\n\n"
            "- Output must satisfy verification check «会计凭证»\n"
            "- Output must satisfy verification check «借»"
        )
        patches = _rule_based_patches(results, skill)
        assert patches
        edit = patches[0]["edits"][0]
        assert "库存" in edit["content"]
        assert "verification check «会计凭证»" not in edit["content"]
        assert edit["op"] == "insert_after"
        assert "verification check" in edit["target"] or edit["target"] == "## Workflow"

    def test_all_rules_present_emits_task_hint(self):
        results = [{
            "id": "jv_x",
            "hard": 0,
            "task_type": "journal_entry",
            "missed_checks": ["库存", "银行"],
            "expected_checks": ["库存", "银行"],
        }]
        skill = (
            f"# Skill\n{RULE_SECTION_HEADING_PRIMARY}\n\n"
            "- Output must satisfy verification check «库存»\n"
            "- Output must satisfy verification check «银行»"
        )
        patches = _rule_based_patches(results, skill)
        assert patches
        assert "task_type=journal_entry" in patches[0]["edits"][0]["content"]

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
        skill = "# Skill\n## Workflow\n### Output format"
        patches = _rule_based_patches(results, skill)
        edit = patches[0]["edits"][0]
        assert "jv_purchase_001" in edit["related_task_ids"]
        assert "库存" in edit["related_missed_checks"]
        assert "银行" in edit["related_missed_checks"]

    def test_infer_traceability_from_content(self):
        from code_to_skill.skillopt_loop.edit_traceability import infer_edit_traceability

        edit = {"content": "- Output must satisfy verification check «库存»"}
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
