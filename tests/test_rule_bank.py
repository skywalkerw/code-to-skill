"""Tests for rule_bank (design 08 phase B)."""
from pathlib import Path

from code_to_skill.skillopt_loop.rule_bank import (
    RuleBankConfig,
    inject_rule_bank_into_skill,
    load_rules,
    promote_rules_from_skill,
    save_rules,
    select_active_rules,
    should_record_rule_bank_regression,
    upsert_candidate_rules,
)


def test_load_and_select_active_rules(tmp_path: Path):
    path = tmp_path / "rules.jsonl"
    save_rules(path, [
        {
            "rule_id": "a", "text": "rule A", "status": "active",
            "support_count": 2, "accepted_count": 2, "regression_count": 0,
            "evidence_refs": ["Foo.java"],
        },
        {
            "rule_id": "b", "text": "rule B", "status": "active",
            "support_count": 1, "accepted_count": 0, "regression_count": 1,
        },
    ])
    rules = load_rules(path)
    cfg = RuleBankConfig(exclude_regressed=True, max_active_rules=5, min_support_score=0.55)
    active = select_active_rules(rules, cfg)
    assert len(active) == 1
    assert active[0]["rule_id"] == "a"


def test_inject_rule_bank_into_skill():
    skill = "## Task\n\nGenerate output from context."
    rules = [{
        "rule_id": "r1", "text": "Always include a heading", "status": "active",
        "support_count": 2, "accepted_count": 2, "regression_count": 0,
        "evidence_refs": ["Bar.java"],
    }]
    cfg = RuleBankConfig(enabled=True, min_support_score=0.55)
    out = inject_rule_bank_into_skill(skill, rules, cfg)
    assert "## Rule bank (verified)" in out
    assert "Always include a heading" in out
    assert "## Task" in out


def test_rule_bank_disabled_without_path():
    cfg = RuleBankConfig.from_skillopt_settings({"rule_bank": {"enabled": True, "path": ""}})
    assert not cfg.enabled


def test_record_rule_bank_acceptance(tmp_path: Path):
    from code_to_skill.skillopt_loop.rule_bank import record_rule_bank_acceptance, save_rules

    path = tmp_path / "rules.jsonl"
    save_rules(path, [{
        "rule_id": "r1",
        "text": "Always include marker",
        "status": "active",
        "support_count": 1,
        "accepted_count": 0,
        "regression_count": 0,
        "source_items": [],
    }])
    skill = "## Rule bank (verified)\n\n- Always include marker\n\n## Task\n"
    n = record_rule_bank_acceptance(
        skill,
        path,
        source_run="run/test",
        step=2,
        replay_fixed_ids=["item_a"],
    )
    assert n == 1
    rules = load_rules(path)
    assert rules[0]["accepted_count"] == 1
    assert "item_a" in rules[0]["source_items"]


def test_promote_only_from_rule_bank_heading(tmp_path: Path):
    path = tmp_path / "rules.jsonl"
    skill = (
        "## Rule bank (verified)\n\n"
        "- Verified rule one\n\n"
        "## Task\n\n"
        "- Unrelated bullet\n"
    )
    promoted = promote_rules_from_skill(skill, source_run="run/test", path=path)
    assert len(promoted) == 1
    assert promoted[0]["text"] == "Verified rule one"


def test_promote_dedupes_by_rule_text_and_hashes_chinese_ids(tmp_path: Path):
    path = tmp_path / "rules.jsonl"
    duplicate_text = "摘要必须包含用户输入中明确的交易动作词。"
    save_rules(path, [{
        "rule_id": "action_words",
        "text": duplicate_text,
        "status": "active",
        "support_count": 1,
        "accepted_count": 1,
        "regression_count": 0,
    }])
    skill = (
        "## Rule bank (verified)\n\n"
        f"- {duplicate_text}\n"
        "- 信息充分时输出必须以会计凭证开头。\n"
    )

    promoted = promote_rules_from_skill(skill, source_run="run/test", path=path)

    assert len(promoted) == 1
    assert promoted[0]["rule_id"].startswith("rule_")
    rules = load_rules(path)
    assert len(rules) == 2
    assert [r["text"] for r in rules].count(duplicate_text) == 1


def test_upsert_candidate_rules_records_accepted_diagnosis_rules(tmp_path: Path):
    path = tmp_path / "rules.jsonl"
    n = upsert_candidate_rules(
        [
            {
                "rule_id": "r1",
                "text": "Always include marker",
                "source_item": "case_a",
                "evidence_refs": ["Foo.java"],
            },
            {
                "rule_id": "r2",
                "text": "Skip rejected case",
                "source_item": "case_b",
            },
        ],
        path,
        source_run="run/test",
        step=3,
        accepted_item_ids=["case_a"],
    )
    assert n == 1
    rules = load_rules(path)
    assert len(rules) == 1
    assert rules[0]["rule_id"] == "r1"
    assert rules[0]["source_items"] == ["case_a"]
    assert rules[0]["evidence_refs"] == ["Foo.java"]
    assert rules[0]["accepted_count"] == 1


def test_upsert_candidate_rules_merges_duplicate_text_and_replaces_weak_id(tmp_path: Path):
    path = tmp_path / "rules.jsonl"
    save_rules(path, [{
        "rule_id": "rule",
        "text": "信息充分时生成凭证。",
        "status": "active",
        "support_count": 1,
        "accepted_count": 1,
        "regression_count": 0,
        "source_runs": [],
        "source_items": [],
        "evidence_refs": [],
    }])

    n = upsert_candidate_rules(
        [{
            "rule_id": "markdown",
            "text": "信息充分时生成凭证。",
            "source_item": "case_a",
        }],
        path,
        source_run="run/test",
        step=1,
        accepted_item_ids=["case_a"],
    )

    assert n == 1
    rules = load_rules(path)
    assert len(rules) == 1
    assert rules[0]["rule_id"].startswith("rule_")
    assert rules[0]["source_items"] == ["case_a"]
    assert rules[0]["accepted_count"] == 2


def test_should_record_rule_bank_regression_only_for_accepted_replayed_candidate():
    report = {
        "candidate_hash": "candidate-a",
        "regressed_ids": ["case_a"],
    }
    accepted = {"accept_new_best", "accept"}

    assert should_record_rule_bank_regression(
        action="accept_new_best",
        replay_report=report,
        accepted_candidate_hash="candidate-a",
        accepted_actions=accepted,
    )
    assert not should_record_rule_bank_regression(
        action="reject",
        replay_report=report,
        accepted_candidate_hash="candidate-a",
        accepted_actions=accepted,
    )
    assert not should_record_rule_bank_regression(
        action="accept",
        replay_report=report,
        accepted_candidate_hash="current-skill",
        accepted_actions=accepted,
    )
    assert not should_record_rule_bank_regression(
        action="accept",
        replay_report={"candidate_hash": "candidate-a", "regressed_ids": []},
        accepted_candidate_hash="candidate-a",
        accepted_actions=accepted,
    )
