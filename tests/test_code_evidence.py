"""代码证据预取测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_to_skill.skillopt_loop.code_evidence import (
    ContextRefPathRule,
    _fetch_trace_summary,
    build_reflect_code_evidence,
    context_ref_path_candidates,
    normalize_context_ref,
    graph_queries_from_failure,
    parse_context_ref,
    trace_pairs_from_failure,
)
from code_to_skill.codegraph_mcp.handler import CodeToolsHandler, CodeRepoConfig

FINERACT_CONTEXT_REF_PATH_RULES = [
    ContextRefPathRule(
        prefix="fineract-provider/",
        expansions=("fineract-provider/src/main/java/org/apache/fineract/{rest}",),
        skip_if_contains="src/main/java",
    ),
    ContextRefPathRule(
        prefix="fineract-accounting/",
        expansions=(
            "fineract-accounting/src/main/java/org/apache/fineract/accounting/{rest}",
            "fineract-provider/src/main/java/org/apache/fineract/accounting/{rest}",
        ),
    ),
    ContextRefPathRule(
        prefix="fineract-core/",
        expansions=(
            "fineract-core/src/main/java/org/apache/fineract/{rest}",
            "fineract-provider/src/main/java/org/apache/fineract/{rest}",
        ),
    ),
]


def test_parse_context_ref():
    assert parse_context_ref("a/b/Foo.java#bar") == ("a/b/Foo.java", "bar")


def test_normalize_context_ref_with_project_rules():
    ref = normalize_context_ref(
        "fineract-accounting/journalentry/data/JournalEntryDataValidator.java",
        path_rules=FINERACT_CONTEXT_REF_PATH_RULES,
    )
    assert ref == (
        "fineract-accounting/src/main/java/org/apache/fineract/accounting/"
        "journalentry/data/JournalEntryDataValidator.java"
    )
    sym = normalize_context_ref(
        "fineract-core/accounting/common/AccountingConstants.java#FinancialActivity.LIABILITY_TRANSFER",
        path_rules=FINERACT_CONTEXT_REF_PATH_RULES,
    )
    assert sym.endswith("#FinancialActivity.LIABILITY_TRANSFER")
    assert "fineract-core/src/main/java" in sym


def test_context_ref_path_candidates_without_rules_only_basename():
    cands = context_ref_path_candidates("module/foo/Bar.java")
    assert cands == ["module/foo/Bar.java", "Bar.java"]


def test_context_ref_path_candidates_with_project_rules():
    cands = context_ref_path_candidates(
        "fineract-accounting/journalentry/data/JournalEntryDataValidator.java",
        path_rules=FINERACT_CONTEXT_REF_PATH_RULES,
    )
    assert "fineract-provider/src/main/java/org/apache/fineract/accounting/journalentry/data/JournalEntryDataValidator.java" in cands

    core = context_ref_path_candidates(
        "fineract-core/accounting/common/AccountingConstants.java",
        path_rules=FINERACT_CONTEXT_REF_PATH_RULES,
    )
    assert "fineract-core/src/main/java/org/apache/fineract/accounting/common/AccountingConstants.java" in core


def test_graph_queries_from_failure_skips_short_verification_tokens():
    qs = graph_queries_from_failure({
        "question": "向客户发放贷款 50000.00",
        "missed_checks": ["借", "贷", "会计", "金额", "Charge"],
    })
    assert "借" not in qs
    assert "贷" not in qs
    assert "会计" not in qs
    assert "金额" not in qs
    assert "Charge" in qs
    assert any("发放贷款" in q for q in qs)


def test_graph_queries_from_failure_keeps_code_like_tokens():
    qs = graph_queries_from_failure({
        "question": "task",
        "missed_checks": ["会计凭证", "inventory", "无人认领负债"],
    })
    assert "会计凭证" not in qs
    assert "inventory" in qs
    assert "无人认领负债" in qs


def test_trace_pairs_from_context_ref():
    pairs = trace_pairs_from_failure({
        "context_refs": ["com/example/FooService.java#processPayment"],
    })
    assert ("FooService", "processPayment") in pairs


def test_fetch_trace_summary(call_chain_graph_fixture):
    repo_root, db_path = call_chain_graph_fixture
    handler = CodeToolsHandler(
        [{"path": repo_root}],
        graph_db_path=db_path,
        repo_root=repo_root,
    )
    summary = _fetch_trace_summary(
        handler, from_symbol="placeOrder", to_symbol="charge",
    )
    assert summary == "" or "Call chain" in summary or "callees" in summary.lower()


@pytest.fixture
def call_chain_graph_fixture(tmp_path):
    from code_to_skill.code_graph import run_code_graph_pipeline

    repo = tmp_path / "repo"
    pkg = repo / "com" / "example"
    pkg.mkdir(parents=True)
    (pkg / "OrderService.java").write_text(
        """package com.example;
public class OrderService {
    private final PaymentService paymentService = new PaymentService();
    public void placeOrder(String id) { paymentService.charge(id); }
}
""",
        encoding="utf-8",
    )
    (pkg / "PaymentService.java").write_text(
        "package com.example;\npublic class PaymentService { public void charge(String id) {} }\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    run_code_graph_pipeline(
        repo_root=str(repo),
        include=["**/*.java"],
        output_root=str(out),
        use_cache=True,
    )
    return str(repo), str(out / "graph.db")


def test_build_reflect_code_evidence_without_graph():
    out = build_reflect_code_evidence(
        [{"id": "x", "hard": 0, "missed_checks": ["借"], "context_refs": []}],
        code_tools=None,
    )
    assert out.text == ""


@pytest.mark.skipif(
    not Path("test-data/sources/repos/fineract").is_dir(),
    reason="fineract repo missing",
)
def test_build_reflect_code_evidence_with_graph(tmp_path):
    from code_to_skill.code_graph import run_code_graph_pipeline

    out_root = tmp_path / "graph"
    run_code_graph_pipeline(
        repo_root="test-data/sources/repos/fineract",
        include=["fineract-provider/src/main/java/org/apache/fineract/accounting/**"],
        exclude=["**/test/**", "**/target/**"],
        output_root=str(out_root),
        use_cache=True,
    )
    db = out_root / "graph.db"
    if not db.is_file():
        pytest.skip("graph.db not built")

    handler = CodeToolsHandler(
        repos=[CodeRepoConfig(
            path="test-data/sources/repos/fineract",
            include=["fineract-provider/src/main/java/org/apache/fineract/accounting/**"],
        )],
        graph_db_path=str(db),
        repo_root="test-data/sources/repos/fineract",
    )
    failed = [{
        "id": "jv_purchase_001",
        "hard": 0,
        "question": "买入 A物品",
        "missed_checks": ["库存", "银行"],
        "context_refs": [
            "fineract-accounting/journalentry/data/JournalEntryDataValidator.java",
        ],
    }]
    evidence = build_reflect_code_evidence(failed, handler, max_cases=1)
    assert "Code Evidence" in evidence.text or "Case" in evidence.text
